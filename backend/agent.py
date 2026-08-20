"""
RepoGuardian agent module.

Runs the compulsory agentic loop for the hackathon PS on top of TRACE's
existing infrastructure:

    for each open issue:
        1. duplicate check      -> Qdrant similarity search
        2. context retrieval    -> related past issues/commits
        3. escalation scoring   -> LLM call (Groq -> Ollama -> deterministic
                                   fallback, same pattern as main.py's call_llm)
        4. logged decision      -> evidence-linked, explainable

Each step is a separate, loggable "subtask" — that's what satisfies the
PS's "creates subtasks" and "multi-step investigation" requirements.
Nothing here needs a real agent framework; a clear, logged sequence of
steps is the requirement, not a specific library.

Usage (standalone, for testing without the API):
    python agent.py <path/to/repo_issues.json>

Wired into main.py as POST /agent/scan (see bottom of this file for the
FastAPI route to paste in).
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
import chromadb

load_dotenv()

CHROMA_URL = os.getenv("CHROMA_URL", "http://localhost:8000")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

ISSUES_COLLECTION = "repo_issues"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
DUPLICATE_THRESHOLD = 0.80  # cosine similarity above this = likely duplicate

class MockEmbeddingModel:
    def encode(self, text: str):
        import hashlib
        import math
        words = text.lower().split()
        vector = [0.0] * 384
        for w in words:
            h = int(hashlib.md5(w.encode('utf-8')).hexdigest(), 16)
            idx = h % 384
            vector[idx] += 1.0
        norm = math.sqrt(sum(x * x for x in vector))
        if norm > 0:
            vector = [x / norm for x in vector]
        else:
            vector[0] = 1.0
            
        class Encoded:
            def tolist(self):
                return vector
        return Encoded()

# Lazy-loaded so this module can be imported by main.py without forcing
# a second model load if main.py already has one in memory.
_model = None


def get_embedding_model():
    global _model
    if _model is None:
        try:
            import os
            os.environ["HF_HUB_OFFLINE"] = "1"
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        except Exception as e:
            print(f"Warning: Could not load local embedding model ({e}).")
            print("Falling back to deterministic mock vectorizer.")
            _model = MockEmbeddingModel()
    return _model


_chroma_client = None


def get_chroma():
    global _chroma_client
    if _chroma_client is not None:
        return _chroma_client

    chroma_url = os.getenv("CHROMA_URL", "http://localhost:8000")
    from urllib.parse import urlparse
    parsed = urlparse(chroma_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8000
    try:
        client = chromadb.HttpClient(host=host, port=int(port))
        client.heartbeat()
        _chroma_client = client
        return client
    except Exception as e:
        print(f"Warning: Could not connect to Chroma at {chroma_url} ({e}).")
        print("Falling back to local in-memory Chroma database.")
        _chroma_client = chromadb.EphemeralClient()
        return _chroma_client


def index_issues(issues: list[dict]) -> int:
    """Embed every issue's title+body and upsert into Chroma. Returns count indexed."""
    chroma_client = get_chroma()
    collection = chroma_client.get_or_create_collection(
        name=ISSUES_COLLECTION,
        metadata={"hnsw:space": "cosine"}
    )
    model = get_embedding_model()

    ids = []
    embeddings = []
    metadatas = []
    documents = []

    for issue in issues:
        text = f"{issue['title']}\n{issue['body']}"
        vector = model.encode(text).tolist()
        
        ids.append(f"{issue['repo']}:{issue['id']}")
        embeddings.append(vector)
        metadatas.append({
            "repo": issue["repo"],
            "title": issue["title"],
            "url": issue["url"] or "#",
            "state": issue.get("state", "open"),
            "date": issue.get("date") or "",
        })
        documents.append(text)

    if ids:
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents
        )
    return len(ids)


def find_duplicates(issue: dict, top_k: int = 3) -> list[dict]:
    """Step 1: duplicate check. Search for semantically similar past issues."""
    chroma_client = get_chroma()
    collection = chroma_client.get_or_create_collection(
        name=ISSUES_COLLECTION,
        metadata={"hnsw:space": "cosine"}
    )
    model = get_embedding_model()

    text = f"{issue['title']}\n{issue['body']}"
    vector = model.encode(text).tolist()

    results = collection.query(
        query_embeddings=[vector],
        n_results=top_k + 1,
        where={"repo": issue["repo"]}
    )

    duplicates = []
    if results and "ids" in results and results["ids"]:
        ids = results["ids"][0]
        distances = results["distances"][0] if "distances" in results and results["distances"] else []
        metadatas = results["metadatas"][0] if "metadatas" in results and results["metadatas"] else []

        for i, doc_id in enumerate(ids):
            if str(doc_id) == f"{issue['repo']}:{issue['id']}":
                continue  # skip matching itself
            
            dist = distances[i] if i < len(distances) else 0.0
            similarity = 1.0 - dist
            
            if similarity >= DUPLICATE_THRESHOLD:
                meta = metadatas[i] if i < len(metadatas) else {}
                duplicates.append({
                    "id": doc_id,
                    "title": meta.get("title", ""),
                    "url": meta.get("url", ""),
                    "similarity": round(similarity, 3),
                })
    return duplicates[:top_k]


def get_feedback_for_issue(repo_id: str, issue_id: str) -> Optional[dict]:
    # Try Neo4j first
    import os
    from neo4j import GraphDatabase
    neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password = os.getenv("NEO4J_PASSWORD")
    
    if neo4j_password:
        try:
            driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
            with driver.session() as session:
                res = session.run(
                    """
                    MATCH (c {id: $issue_id})-[:HAS_FEEDBACK]->(f:Feedback)
                    RETURN f.decision AS decision, f.correct AS correct,
                           f.correctedDecision AS correctedDecision,
                           f.action AS action, f.humanDecision AS humanDecision
                    ORDER BY f.timestamp DESC
                    LIMIT 1
                    """,
                    issue_id=str(issue_id)
                )
                record = res.single()
                if record:
                    return {
                        "decision": record["decision"],
                        "correct": record["correct"],
                        "correctedDecision": record["correctedDecision"],
                        "action": record["action"],
                        "humanDecision": record["humanDecision"]
                    }
        except Exception as e:
            print(f"Failed to check Neo4j for feedback: {e}")
            
    # Fallback to local JSON
    from pathlib import Path
    import json
    repo_slug = repo_id.split('/')[-1] if repo_id and '/' in repo_id else (repo_id or "unknown")
    feedback_path = Path(__file__).resolve().parent.parent / "data" / "raw" / f"{repo_slug}_feedback.json"
    if feedback_path.exists():
        try:
            feedbacks = json.loads(feedback_path.read_text(encoding="utf-8"))
            # Filter backwards
            for fb in reversed(feedbacks):
                if str(fb.get("issueId")) == str(issue_id):
                    return {
                        "decision": fb.get("decision"),
                        "correct": fb.get("correct"),
                        "correctedDecision": fb.get("correctedDecision")
                    }
        except Exception as e:
            print(f"Failed to read local feedback JSON: {e}")
            
    return None


def score_escalation(issue: dict, duplicates: list[dict]) -> dict:
    """
    Step 3: ask the LLM to decide what should happen with this issue,
    with an explicit evidence-backed reason. Same Groq -> Ollama ->
    deterministic-fallback pattern as main.py's call_llm, so it degrades
    gracefully under the same conditions during the demo.
    """
    dup_context = (
        "\n".join(f"- Possible duplicate: '{d['title']}' (similarity {d['similarity']})" for d in duplicates)
        if duplicates else "No similar past issues found."
    )

    feedback_context = []
    has_maintainer_correction = False
    
    repo_id = issue.get("repo", "unknown")
    for d in duplicates:
        fb = get_feedback_for_issue(repo_id, d["id"])
        if fb:
            has_maintainer_correction = True
            if not fb.get("correct") and fb.get("correctedDecision"):
                feedback_context.append(
                    f"Note: a similar past issue '{d['title']}' (id: {d['id']}) was corrected by a maintainer from '{fb['decision']}' to '{fb['correctedDecision']}' — take this into account."
                )

    feedback_str = "\n".join(feedback_context) if feedback_context else ""

    system_prompt = (
        "You are RepoGuardian, an AI assistant that triages GitHub issues for busy maintainers. "
        "Given one issue and any detected duplicates, decide ONE action: "
        "'escalate' (genuinely needs a maintainer's attention now), "
        "'needs_more_info' (reasonable but missing reproduction steps/details), "
        "'duplicate' (a near-identical issue already exists), or "
        "'low_priority' (minor, stale, or low-impact). "
        "Base your decision only on the issue text and duplicate evidence given. "
        "Respond with a JSON object with exactly these keys: "
        "'decision' (one of the four values above), "
        "'reason' (one or two sentences citing specific evidence from the issue or duplicates), "
        "'security_sensitive' (true/false, true if this could be a security vulnerability report)."
    )
    user_prompt = (
        f"Issue #{issue['id']}: {issue['title']}\n\n"
        f"Body:\n{issue['body'][:1500]}\n\n"
        f"Labels: {', '.join(issue.get('labels', [])) or 'none'}\n"
        f"Comments so far: {issue.get('comments', 0)}\n\n"
        f"Duplicate check results:\n{dup_context}"
    )
    if feedback_str:
        user_prompt += f"\n\nMaintainer Feedback on Similar Past Issues:\n{feedback_str}"

    res_dict = None
    if GROQ_API_KEY:
        try:
            res = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                },
                timeout=15,
            )
            res.raise_for_status()
            res_dict = json.loads(res.json()["choices"][0]["message"]["content"])
        except Exception as e:
            print(f"Groq escalation scoring failed, trying Ollama: {e}")

    if res_dict is None:
        try:
            res = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": "llama3.2",
                    "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0.2},
                },
                timeout=20,
            )
            res.raise_for_status()
            res_dict = json.loads(res.json()["message"]["content"])
        except Exception as e:
            print(f"Ollama escalation scoring failed or unavailable: {e}")

    if res_dict is None:
        # Deterministic fallback — keeps the demo alive if both LLMs are down
        if duplicates:
            decision = "duplicate"
            reason = f"Likely duplicate of '{duplicates[0]['title']}' (similarity {duplicates[0]['similarity']})."
        elif issue.get("comments", 0) == 0 and len(issue["body"]) < 100:
            decision = "needs_more_info"
            reason = "Issue body is very short and has no discussion yet; likely missing reproduction details."
        else:
            decision = "escalate"
            reason = "No duplicate found and issue has active discussion; recommend maintainer review."
        res_dict = {"decision": decision, "reason": reason, "security_sensitive": False}

    res_dict["has_corrected_duplicate"] = has_maintainer_correction
    return res_dict


def contributor_follow_up(issue: dict) -> dict:
    """Identify missing reproduction or environment details and draft a request."""
    body = (issue.get("body") or "").lower()
    labels = {str(label).lower() for label in issue.get("labels", [])}
    missing = []
    if len(body.strip()) < 120 or not any(term in body for term in ("steps to reproduce", "reproduction", "reproduce")):
        missing.append("reproduction steps")
    if not any(term in body for term in ("environment", "os:", "version", "python", "node", "browser")) and not (labels & {"bug", "error"}):
        missing.append("environment and version details")

    if not missing:
        return {"needs_follow_up": False, "missing": [], "message": None}

    requested = " and ".join(missing)
    return {
        "needs_follow_up": True,
        "missing": missing,
        "message": f"Thanks for reporting this. Could you add {requested}? These details will help us reproduce and investigate the issue.",
    }


def _investigation_time() -> str:
    return datetime.now(timezone.utc).isoformat()


def investigate_pr_history(issue: dict, limit: int = 5) -> list[dict]:
    """Find repository-scoped PR history related to the issue text."""
    from neo4j import GraphDatabase
    keywords = [word.strip(".,!?()[]{}") for word in issue.get("title", "").lower().split() if len(word) > 4]
    if not keywords or not os.getenv("NEO4J_PASSWORD"):
        return []
    driver = None
    try:
        driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD")),
        )
        with driver.session() as session:
            result = session.run(
                """
                MATCH (pr:PullRequest)
                WHERE pr.id STARTS WITH $prefix
                  AND any(keyword IN $keywords WHERE toLower(pr.title) CONTAINS keyword OR toLower(pr.body) CONTAINS keyword)
                RETURN pr.id AS id, pr.title AS title, pr.url AS url, pr.merged AS merged,
                       pr.merged_at AS merged_at
                ORDER BY pr.updated_at DESC
                LIMIT $limit
                """,
                prefix=f"{issue['repo']}:",
                keywords=keywords,
                limit=limit,
            )
            return [dict(record) for record in result]
    except Exception as exc:
        print(f"PR history investigation failed: {exc}")
        return []
    finally:
        if driver:
            driver.close()


def investigate_repository_context(issue: dict, limit: int = 5) -> list[dict]:
    """Find repository files related to the issue title/body."""
    from neo4j import GraphDatabase
    keywords = [word.strip(".,!?()[]{}") for word in f"{issue.get('title', '')} {issue.get('body', '')}".lower().split() if len(word) > 4]
    if not keywords or not os.getenv("NEO4J_PASSWORD"):
        return []
    driver = None
    try:
        driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD")),
        )
        with driver.session() as session:
            result = session.run(
                """
                MATCH (f:File)
                WHERE f.id STARTS WITH $prefix
                  AND any(keyword IN $keywords WHERE toLower(f.path) CONTAINS keyword OR toLower(f.name) CONTAINS keyword)
                RETURN f.id AS id, f.path AS path, f.extension AS extension
                LIMIT $limit
                """,
                prefix=f"{issue['repo']}:",
                keywords=keywords[:20],
                limit=limit,
            )
            return [dict(record) for record in result]
    except Exception as exc:
        print(f"Repository context investigation failed: {exc}")
        return []
    finally:
        if driver:
            driver.close()


def investigate_health_impact(repo_id: str) -> dict:
    """Capture the current health evidence used by the final decision."""
    try:
        from health import compute_health
        health = compute_health(repo_id)
        return {
            "status": health.get("health_status"),
            "score": health.get("health_score"),
            "reasons": health.get("health_reasons", []),
        }
    except Exception as exc:
        return {"status": "unavailable", "score": None, "reasons": [{"message": str(exc)}]}


def evaluate_importance(issue: dict, duplicates: list[dict], related_prs: list[dict], health_impact: dict) -> dict:
    """Score escalation evidence without relying on an opaque model decision."""
    title_body = f"{issue.get('title', '')} {issue.get('body', '')}".lower()
    labels = {str(label).lower() for label in issue.get("labels", [])}
    score = 0
    reasons = []

    security_terms = ("security", "vulnerability", "cve", "token leak", "credential", "exploit")
    impact_terms = ("crash", "outage", "data loss", "production", "breaking", "cannot login", "authentication", "oauth", "payment")
    important_components = ("auth", "api", "database", "security", "payment", "storage")

    if "security" in labels or any(term in title_body for term in security_terms):
        score += 35
        reasons.append("Security-sensitive language or labels detected")
    if "high" in labels or "critical" in labels or any(term in title_body for term in impact_terms):
        score += 25
        reasons.append("High-impact production or core-functionality signal detected")
    if any(term in title_body for term in important_components):
        score += 15
        reasons.append("Issue appears to affect an important repository component")
    if duplicates:
        score += 15
        reasons.append(f"Related historical issue evidence found ({len(duplicates)} match(es))")
    if len(duplicates) >= 2:
        score += 10
        reasons.append("Multiple similar reports suggest a repeated issue")
    if issue.get("comments", 0) >= 10:
        score += 10
        reasons.append("High comment volume suggests maintainer or contributor contention")
    if issue.get("state") == "open" and issue.get("updated_at"):
        try:
            updated = datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - updated).days >= 90:
                score += 10
                reasons.append("Open issue has been stale for at least 90 days")
        except ValueError:
            pass
    if related_prs:
        score += 10
        reasons.append(f"Related pull-request history found ({len(related_prs)} PR(s))")
    if (health_impact.get("score") or 0) >= 30:
        score += 15
        reasons.append("Repository health evidence indicates meaningful project impact")

    return {"score": min(score, 100), "reasons": reasons}


def build_explanation(issue: dict, decision: str, importance: dict, duplicates: list[dict], related_prs: list[dict], related_files: list[dict], health_impact: dict) -> dict:
    """Create a frontend-ready, evidence-backed explanation for the decision."""
    evidence = []
    for duplicate in duplicates:
        source_id = str(duplicate.get("id", ""))
        evidence.append({
            "type": "issue",
            "id": source_id,
            "label": duplicate.get("title") or source_id,
            "url": duplicate.get("url") or f"https://github.com/{issue['repo']}/issues/{source_id.rsplit(':', 1)[-1]}",
            "detail": f"{round(float(duplicate.get('similarity', 0)) * 100)}% semantic similarity"
        })
    for pull_request in related_prs:
        evidence.append({
            "type": "pull_request",
            "id": str(pull_request.get("id", "")),
            "label": pull_request.get("title") or str(pull_request.get("id", "")),
            "url": pull_request.get("url") or "#",
            "detail": "Related pull-request history"
        })
    for file_item in related_files:
        evidence.append({
            "type": "file",
            "id": str(file_item.get("id", "")),
            "label": file_item.get("path") or str(file_item.get("id", "")),
            "url": f"https://github.com/{issue['repo']}/blob/main/{file_item.get('path', '')}",
            "detail": "Related repository component"
        })
    for reason in health_impact.get("reasons", []):
        evidence.append({
            "type": "health",
            "id": reason.get("metric", "health"),
            "label": "Repository health",
            "url": "#",
            "detail": reason.get("message", "Health impact detected")
        })

    evidence_count = len(evidence)
    confidence = min(0.99, 0.55 + (evidence_count * 0.05) + (importance.get("score", 0) * 0.003))
    if decision in ("low_priority", "needs_more_info") and evidence_count == 0:
        confidence = 0.6
    return {
        "confidence": round(confidence, 2),
        "confidence_percent": round(confidence * 100),
        "reasons": importance.get("reasons", []) or ["No strong escalation signal was found"],
        "evidence": evidence,
    }


def run_scan(issues: list[dict]) -> list[dict]:
    """
    The full agent loop. Runs all steps per issue and returns a logged,
    evidence-linked decision for each — this list IS the explainability
    trail the PS asks for.
    """
    print(f"Indexing {len(issues)} issues into Qdrant ...")
    index_issues(issues)

    results = []
    for issue in issues:
        print(f"\nInvestigating issue #{issue['id']}: {issue['title'][:60]}")

        timeline = []
        step_started = _investigation_time()
        print("  Step 1: checking for duplicates ...")
        duplicates = find_duplicates(issue)
        print(f"    -> {len(duplicates)} possible duplicate(s) found")
        timeline.append({"step": "related_issues", "label": "Duplicate search", "started_at": step_started, "completed_at": _investigation_time(), "evidence": {"matches": duplicates}})

        step_started = _investigation_time()
        print("  Step 2: investigating PR history ...")
        related_prs = investigate_pr_history(issue)
        timeline.append({"step": "pr_history", "label": "Historical analysis", "started_at": step_started, "completed_at": _investigation_time(), "evidence": {"related_prs": related_prs}})

        step_started = _investigation_time()
        print("  Step 3: checking repository context ...")
        related_files = investigate_repository_context(issue)
        timeline.append({"step": "repository_context", "label": "Code investigation", "started_at": step_started, "completed_at": _investigation_time(), "evidence": {"files": related_files}})

        step_started = _investigation_time()
        print("  Step 4: checking health impact ...")
        health_impact = investigate_health_impact(issue.get("repo", "unknown"))
        timeline.append({"step": "health_impact", "label": "Health analysis", "started_at": step_started, "completed_at": _investigation_time(), "evidence": health_impact})

        importance = evaluate_importance(issue, duplicates, related_prs, health_impact)

        step_started = _investigation_time()
        print("  Step 5: scoring escalation ...")
        scored = score_escalation(issue, duplicates)
        follow_up = contributor_follow_up(issue)
        decision = scored["decision"]
        if importance["score"] >= 60 or "Security-sensitive language or labels detected" in importance["reasons"]:
            decision = "escalate"
        elif decision == "escalate" and importance["score"] < 20:
            decision = "low_priority"
        elif follow_up["needs_follow_up"] and importance["score"] < 40:
            decision = "needs_more_info"
        print(f"    -> decision: {decision} (importance {importance['score']}/100)")
        explanation = build_explanation(issue, decision, importance, duplicates, related_prs, related_files, health_impact)
        timeline.append({"step": "final_decision", "label": "Final decision", "started_at": step_started, "completed_at": _investigation_time(), "evidence": {"decision": decision, "reason": scored["reason"], "importance": importance, "explanation": explanation}})

        results.append({
            "issue_id": issue["id"],
            "title": issue["title"],
            "url": issue["url"],
            "decision": decision,
            "reason": scored["reason"],
            "importance": importance,
            "explanation": explanation,
            "security_sensitive": scored.get("security_sensitive", False),
            "duplicates": duplicates,
            "has_corrected_duplicate": scored.get("has_corrected_duplicate", False),
            "follow_up": follow_up,
            "investigation_timeline": timeline
        })

    return results


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python agent.py <path/to/repo_issues.json>")

    path = Path(sys.argv[1])
    issues = json.loads(path.read_text(encoding="utf-8"))
    issues = [i for i in issues if i["type"] == "issue"]  # skip PRs for triage

    if not issues:
        sys.exit("No issues (type='issue') found in that file — did you run ingest.py with --issues?")

    results = run_scan(issues)

    out_path = path.parent / f"{path.stem}_agent_results.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(f"\n\n=== Scan complete: {len(results)} issues investigated ===")
    for r in results:
        flag = "🔒 SECURITY" if r["security_sensitive"] else ""
        print(f"[{r['decision'].upper():16}] #{r['issue_id']}: {r['title'][:50]} {flag}")
        print(f"    reason: {r['reason']}")
    print(f"\nSaved full results -> {out_path}")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# FastAPI route to paste into main.py — add this near the other @app routes,
# and add `from agent import run_scan` near the top of main.py.
# ---------------------------------------------------------------------------
#
# @app.post("/agent/scan")
# def agent_scan(repoId: str):
#     import json
#     from pathlib import Path
#     issues_path = Path(__file__).resolve().parent.parent / "data" / "raw" / f"{repoId.split('/')[-1]}_issues.json"
#     if not issues_path.exists():
#         raise HTTPException(status_code=404, detail=f"No ingested issues found for {repoId}. Run ingest.py --issues first.")
#     issues = json.loads(issues_path.read_text(encoding="utf-8"))
#     issues = [i for i in issues if i["type"] == "issue"]
#     from agent import run_scan
#     results = run_scan(issues)
#     return {"repoId": repoId, "scanned": len(results), "results": results}
