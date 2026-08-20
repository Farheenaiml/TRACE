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
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
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


_qdrant_client = None


def get_qdrant() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is not None:
        return _qdrant_client

    try:
        client = QdrantClient(url=QDRANT_URL, timeout=1.5)
        # Attempt a quick health check call
        client.get_collections()
        _qdrant_client = client
        return client
    except Exception as e:
        print(f"Warning: Could not connect to Qdrant at {QDRANT_URL} ({e}).")
        print("Falling back to local in-memory Qdrant database.")
        _qdrant_client = QdrantClient(location=":memory:")
        return _qdrant_client


def ensure_issues_collection(qdrant: QdrantClient):
    if not qdrant.collection_exists(ISSUES_COLLECTION):
        qdrant.create_collection(
            collection_name=ISSUES_COLLECTION,
            vectors_config=VectorParams(size=384, distance=Distance.COSINE),
        )


def index_issues(issues: list[dict]) -> int:
    """Embed every issue's title+body and upsert into Qdrant. Returns count indexed."""
    qdrant = get_qdrant()
    ensure_issues_collection(qdrant)
    model = get_embedding_model()

    points = []
    for issue in issues:
        text = f"{issue['title']}\n{issue['body']}"
        vector = model.encode(text).tolist()
        points.append(PointStruct(
            id=int(issue["id"]),
            vector=vector,
            payload={
                "repo": issue["repo"],
                "title": issue["title"],
                "url": issue["url"],
                "state": issue.get("state", "open"),
                "date": issue.get("date"),
            },
        ))
    if points:
        qdrant.upsert(collection_name=ISSUES_COLLECTION, points=points)
    return len(points)


def find_duplicates(issue: dict, top_k: int = 3) -> list[dict]:
    """Step 1: duplicate check. Search for semantically similar past issues."""
    qdrant = get_qdrant()
    ensure_issues_collection(qdrant)
    model = get_embedding_model()

    text = f"{issue['title']}\n{issue['body']}"
    vector = model.encode(text).tolist()

    hits = qdrant.query_points(
        collection_name=ISSUES_COLLECTION,
        query=vector,
        limit=top_k + 1,  # +1 because the issue itself may already be indexed
    ).points

    duplicates = []
    for hit in hits:
        if str(hit.id) == str(issue["id"]):
            continue  # skip matching itself
        if hit.score >= DUPLICATE_THRESHOLD:
            duplicates.append({
                "id": hit.id,
                "title": hit.payload.get("title"),
                "url": hit.payload.get("url"),
                "similarity": round(hit.score, 3),
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
                    RETURN f.decision AS decision, f.correct AS correct, f.correctedDecision AS correctedDecision
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
                        "correctedDecision": record["correctedDecision"]
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

        print("  Step 1: checking for duplicates ...")
        duplicates = find_duplicates(issue)
        print(f"    -> {len(duplicates)} possible duplicate(s) found")

        print("  Step 2: scoring escalation ...")
        scored = score_escalation(issue, duplicates)
        print(f"    -> decision: {scored['decision']}")

        results.append({
            "issue_id": issue["id"],
            "title": issue["title"],
            "url": issue["url"],
            "decision": scored["decision"],
            "reason": scored["reason"],
            "security_sensitive": scored.get("security_sensitive", False),
            "duplicates": duplicates,
            "has_corrected_duplicate": scored.get("has_corrected_duplicate", False)
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
