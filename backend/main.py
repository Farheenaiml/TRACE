import os
import json
import requests
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from neo4j import GraphDatabase
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from datetime import datetime, timezone
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
from agent import run_scan

# Load environment variables
load_dotenv()
os.environ["HF_HUB_OFFLINE"] = "1"

app = FastAPI(title="TRACE REST API", version="1.0.0")

# Enable CORS for the frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_API_BASE = "https://api.github.com"
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "15"))


def github_get(path: str, params: Optional[dict] = None):
    if not GITHUB_TOKEN:
        return None
    response = requests.get(
        f"{GITHUB_API_BASE}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        params=params,
        timeout=15,
    )
    if response.status_code == 401:
        raise HTTPException(status_code=502, detail="GitHub token was rejected")
    if response.status_code == 403:
        raise HTTPException(status_code=502, detail="GitHub API rate limit exceeded")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def github_repo_response(repo: dict) -> dict:
    return {
        "id": repo["full_name"],
        "name": repo["name"],
        "description": repo.get("description") or "No description provided.",
        "language": repo.get("language") or "Unknown",
        "decisions": 0,
    }

def run_repo_scan(repo_slug: str, repo_id_label: str, issues_list: list) -> dict:
    raw_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    
    results = run_scan(issues_list)
    timestamp = datetime.now(timezone.utc).isoformat()
    
    data = {
        "repoId": repo_id_label,
        "last_run": timestamp,
        "scanned": len(results),
        "results": results
    }
    
    out_path = raw_dir / f"{repo_slug}_agent_results.json"
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data

def scheduled_scan_job():
    raw_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    issue_files = list(raw_dir.glob("*_issues.json"))
    if not issue_files:
        print("[scheduled scan] No ingested issues files found in data/raw/, skipping scan.")
        return

    print(f"[scheduled scan] Running automated triage scan across {len(issue_files)} ingested repo(s)...")
    for issues_path in issue_files:
        try:
            repo_slug = issues_path.name.replace("_issues.json", "")
            print(f"[scheduled scan] Scanning issues for repo: {repo_slug}")
            issues = json.loads(issues_path.read_text(encoding="utf-8"))
            issues = [i for i in issues if i.get("type") == "issue"]
            data = run_repo_scan(repo_slug, repo_slug, issues)
            print(f"[scheduled scan] Completed scan for {repo_slug}: {data['scanned']} issues investigated. Saved to {repo_slug}_agent_results.json")
        except Exception as e:
            print(f"[scheduled scan] Error scanning {issues_path.name}: {e}")

scheduler = BackgroundScheduler()

@app.on_event("startup")
def start_scheduler():
    scheduler.add_job(scheduled_scan_job, "interval", minutes=SCAN_INTERVAL_MINUTES)
    scheduler.start()
    print(f"[scheduled scan] APScheduler started. Interval set to every {SCAN_INTERVAL_MINUTES} minute(s).")

@app.on_event("shutdown")
def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()

COLLECTION_NAME = "rationale_sentences"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Try loading sentence-transformers. 
# On Windows, PyTorch can sometimes trigger OSError [WinError 1450] (insufficient system resources).
# We catch this error and fallback to Neo4j database-level keyword text search.
HAS_EMBEDDINGS = False
model = None

try:
    print("Initializing local embedding model...")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(EMBEDDING_MODEL)
    HAS_EMBEDDINGS = True
    print("Embedding model initialized successfully.")
except Exception as e:
    print(f"\nWARNING: Could not load sentence-transformers ({e}).")
    print("Falling back to Neo4j Database Keyword Search Mode.\n")

qdrant_client = None

def get_qdrant_client():
    global qdrant_client
    if qdrant_client is None and HAS_EMBEDDINGS:
        try:
            qdrant_client = QdrantClient(url=QDRANT_URL)
        except Exception as e:
            print(f"Warning: Could not connect to Qdrant at {QDRANT_URL}: {e}")
    return qdrant_client

def get_neo4j_driver():
    if not NEO4J_PASSWORD:
        raise HTTPException(status_code=500, detail="NEO4J_PASSWORD environment variable is not set")
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        return driver
    except Exception as e:
        print(f"Neo4j connection error: {e}")
        return None

# Schemas
class QueryRequest(BaseModel):
    repoId: str
    question: str

class Citation(BaseModel):
    id: str
    label: str
    kind: str
    url: str

class Decision(BaseModel):
    id: str
    title: str
    when: str
    author: str

class AnswerResponse(BaseModel):
    question: str
    answer: str
    confidence: str
    citations: List[Citation]
    contradiction: Optional[str] = None
    related: List[Decision]

class RecallRequest(BaseModel):
    repoId: str
    title: str
    body: str = ""

class RecallMatchResponse(BaseModel):
    id: str
    title: str
    summary: str
    similarity: float
    when: str
    status: str

class RepoResponse(BaseModel):
    id: str
    name: str
    description: str
    language: str
    decisions: int

class FeedbackRequest(BaseModel):
    repoId: str
    issueId: str
    decision: str
    correct: bool
    correctedDecision: Optional[str] = None

# Mock Data
MOCK_REPOS = [
    {
        "id": "auth-service",
        "name": "auth-service",
        "description": "Identity, sessions and token issuance for the platform.",
        "language": "TypeScript",
        "decisions": 218,
    },
    {
        "id": "minihooked",
        "name": "MiniHooked",
        "description": "Lightweight React hooks toolkit used across product teams.",
        "language": "TypeScript",
        "decisions": 94,
    },
    {
        "id": "casesense",
        "name": "CaseSense",
        "description": "Case-management engine with a rules DSL and audit trail.",
        "language": "Python",
        "decisions": 341,
    },
]

MOCK_ANSWERS = [
    {
        "question": "Why was OAuth chosen over JWT in the auth module?",
        "answer": "OAuth 2.0 was adopted as the authorization framework in Q2 2024 after the team hit revocation limits with self-issued JWTs. The discussion in PR #142 concluded that stateless JWTs made instant session revocation impossible without a deny-list, which would have reintroduced a shared datastore anyway. OAuth with short-lived access tokens plus refresh-token rotation gave the security team a revocation path and let the mobile clients reuse the provider's consent screen. JWT was not removed entirely — it remains the wire format for the access tokens themselves.",
        "confidence": "high",
        "citations": [
            {"id": "c1", "label": "commit a3f21c", "kind": "commit", "url": "#"},
            {"id": "c2", "label": "PR #142", "kind": "pr", "url": "#"},
            {"id": "c3", "label": "ADR-011 Token strategy", "kind": "doc", "url": "#"},
            {"id": "c4", "label": "issue #98", "kind": "issue", "url": "#"},
        ],
        "contradiction": "This may conflict with a past decision: commit a3f21c removed the basic-auth fallback for security reasons, so any OAuth outage path must not re-enable it.",
        "related": [
            {"id": "d1", "title": "Adopted refresh-token rotation for mobile clients", "when": "3 months ago", "author": "@rin"},
            {"id": "d2", "title": "Dropped basic-auth fallback from the gateway", "when": "7 months ago", "author": "@marcus"},
            {"id": "d3", "title": "Moved session store from Redis to Postgres", "when": "9 months ago", "author": "@aditi"},
            {"id": "d4", "title": "Standardised on 15-minute access token TTL", "when": "1 year ago", "author": "@rin"},
        ],
    },
    {
        "question": "Why do we still ship a custom useDebounce instead of a library?",
        "answer": "The team evaluated three third-party debounce hooks in early 2025 and kept the in-house implementation. The deciding factor was bundle weight: the candidates pulled in lodash-style scheduling helpers that added ~4kb gzipped for behaviour the internal version covers in 22 lines. A secondary concern raised in issue #61 was that external hooks flushed pending calls on unmount, which broke the search-as-you-type analytics contract.",
        "confidence": "medium",
        "citations": [
            {"id": "c5", "label": "commit 7b19de", "kind": "commit", "url": "#"},
            {"id": "c6", "label": "issue #61", "kind": "issue", "url": "#"},
            {"id": "c7", "label": "PR #77", "kind": "pr", "url": "#"},
        ],
        "related": [
            {"id": "d5", "title": "Set a 12kb budget for the hooks bundle", "when": "5 months ago", "author": "@lena"},
            {"id": "d6", "title": "Rejected lodash as a runtime dependency", "when": "8 months ago", "author": "@marcus"},
            {"id": "d7", "title": "Added flush-on-unmount opt-in to useDebounce", "when": "10 months ago", "author": "@lena"},
        ],
    },
    {
        "question": "Why is the rules engine evaluated server-side only?",
        "answer": "Server-side evaluation was chosen so the rule set never leaves the audit boundary. Compliance review in 2024 flagged that shipping the DSL to the browser would expose scoring thresholds that clients are contractually not allowed to see. The team accepted the extra round-trip latency (~80ms p95) and added an optimistic UI layer instead. A partial client-side validator exists, but it only checks syntax, never outcomes.",
        "confidence": "high",
        "citations": [
            {"id": "c8", "label": "commit f04a11", "kind": "commit", "url": "#"},
            {"id": "c9", "label": "ADR-004 Rule evaluation", "kind": "doc", "url": "#"},
        ],
        "related": [
            {"id": "d8", "title": "Added optimistic UI for rule previews", "when": "2 months ago", "author": "@aditi"},
            {"id": "d9", "title": "Split syntax validation out of the evaluator", "when": "6 months ago", "author": "@jonas"},
            {"id": "d10", "title": "Introduced immutable audit log for rule runs", "when": "1 year ago", "author": "@aditi"},
        ],
    },
]

MOCK_RECALLS = [
    {
        "id": "r1",
        "title": "Login loop after token refresh on Safari",
        "summary": "Same symptom class: refresh token rejected because the cookie SameSite attribute was dropped by the proxy. Fixed by pinning SameSite=None; Secure at the gateway.",
        "similarity": 0.93,
        "when": "4 months ago",
        "status": "closed",
    },
    {
        "id": "r2",
        "title": "Intermittent 401s during deploy windows",
        "summary": "Signing keys were rotated before the old key left the JWKS cache. The rollout order was changed so keys publish one deploy ahead of use.",
        "similarity": 0.81,
        "when": "7 months ago",
        "status": "closed",
    },
    {
        "id": "r3",
        "title": "ADR-011: token strategy and revocation",
        "summary": "Decision record explaining short-lived access tokens plus rotation, which constrains any fix that lengthens session lifetime.",
        "similarity": 0.74,
        "when": "1 year ago",
        "status": "merged",
    },
    {
        "id": "r4",
        "title": "Session store migration caused duplicate sessions",
        "summary": "During the Redis to Postgres cutover, dual writes produced two live sessions per user. Related if the report mentions duplicate devices.",
        "similarity": 0.62,
        "when": "9 months ago",
        "status": "closed",
    },
    {
        "id": "r5",
        "title": "Rate limiter counts refresh calls as logins",
        "summary": "Open issue with overlapping vocabulary; may be the same root cause if the user is being throttled.",
        "similarity": 0.48,
        "when": "3 weeks ago",
        "status": "open",
    },
]

def check_db_empty_for_repo(repo_id: str) -> bool:
    driver = get_neo4j_driver()
    if driver is None:
        return True
    try:
        with driver.session() as session:
            res = session.run(
                "MATCH (c:Commit {repo: $repo_id}) RETURN count(c) AS count",
                repo_id=repo_id
            )
            val = res.single()
            return val["count"] == 0 if val else True
    except Exception as e:
        print(f"Error checking DB contents: {e}")
        return True
    finally:
        if driver:
            driver.close()

# Fallback Database search when embeddings fail
def search_neo4j_keywords(repo_id: str, text: str, limit: int = 5) -> list:
    stopwords = {"why", "how", "what", "where", "when", "who", "which", "whose", "whom", "was", "were", "been", "have", "has", "had", "does", "done", "doing", "would", "should", "could", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "as", "at", "be", "because", "before", "being", "below", "between", "both", "but", "by", "can", "do", "for", "from", "in", "into", "is", "it", "its", "of", "on", "or", "other", "our", "out", "over", "same", "so", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", "up", "very", "with", "you", "your"}
    
    words = [w.strip(".,!?\"'()[]{}") for w in text.lower().split()]
    keywords = [w for w in words if len(w) > 3 and w not in stopwords]
    
    if not keywords:
        keywords = [w for w in words if len(w) > 2]
    if not keywords:
        keywords = [""]

    driver = get_neo4j_driver()
    if not driver:
        return []
        
    results = []
    try:
        with driver.session() as session:
            cypher = """
            MATCH (c {repo: $repo_id})-[:HAS_RATIONALE]->(rat:Rationale)
            WHERE c:Commit OR c:Issue OR c:PullRequest
            WITH c, rat, 
                 [kw IN $keywords WHERE toLower(rat.text) CONTAINS kw] AS matches
            WHERE size(matches) > 0
            RETURN rat.text AS text, c.id AS commit_id, c.url AS url, c.type AS type, size(matches) AS score
            ORDER BY score DESC
            LIMIT $limit
            """
            res = session.run(cypher, repo_id=repo_id, keywords=keywords, limit=limit)
            for record in res:
                results.append({
                    "text": record["text"],
                    "commit_id": record["commit_id"],
                    "type": record.get("type") or "commit",
                    "url": record["url"] or "#",
                    "score": float(record["score"]) / max(len(keywords), 1)
                })
    except Exception as e:
        print(f"Error querying Neo4j fallback: {e}")
    finally:
        driver.close()
    return results

# Helper to format dates
def format_when(iso_date_str: str) -> str:
    if not iso_date_str:
        return "recently"
    return iso_date_str.split("T")[0]

def format_source_label(c: dict) -> str:
    stype = (c.get("source_type") or c.get("type") or "commit").lower()
    cid = str(c.get("commit_id") or c.get("id") or "")
    display_id = cid[:7] if len(cid) >= 7 else cid
    if stype == "issue":
        return f"Issue [#{display_id}]"
    elif stype in ("pull_request", "pr"):
        return f"PR [#{display_id}]"
    return f"Commit [{display_id}]"

# Generate synthesized LLM answer
def call_llm(question: str, context_sentences: List[dict]) -> dict:
    context_str = "\n".join([
        f"- {format_source_label(c)}: '{c['text']}' (Author: {c.get('author', 'Unknown')}, Date: {c.get('date', 'Unknown')})"
        for c in context_sentences
    ])
    
    system_prompt = (
        "You are TRACE, an AI decision intelligence engine for software repositories. "
        "Your task is to answer user queries about software architecture and development decisions based ONLY on the provided context of past commit messages, PR descriptions, and rationale statements. "
        "Strictly ground your answer in the facts provided. Do not invent or assume things not in the context. "
        "If the context contains decisions that clash or contradict (e.g. one commit revokes or alters a decision in another), explain this conflict clearly and fill the 'contradiction' property. "
        "Determine the overall confidence score ('high', 'medium', or 'low') based on how well the context directly answers the user's question. "
        "You must respond with a JSON object containing exactly these properties: \n"
        "  - 'answer': string, a detailed synthesized response.\n"
        "  - 'confidence': string, one of 'high', 'medium', or 'low'.\n"
        "  - 'contradiction': optional string, detail any architectural conflicts, otherwise null."
    )
    
    user_prompt = f"Context:\n{context_str}\n\nQuestion: {question}"

    # Try Groq first
    if GROQ_API_KEY:
        print("Synthesizing answer using Groq...")
        try:
            headers = {
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2
            }
            res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=15)
            res.raise_for_status()
            content = res.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception as e:
            print(f"Groq generation failed, attempting Ollama fallback: {e}")

    # Try local Ollama fallback
    try:
        print("Synthesizing answer using Ollama...")
        payload = {
            "model": "llama3.2",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.2}
        }
        res = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=20)
        res.raise_for_status()
        content = res.json()["message"]["content"]
        return json.loads(content)
    except Exception as e:
        print(f"Ollama generation failed or unavailable: {e}")

    # Local Rule-based template fallback
    print("Running local deterministic fallback synthesis...")
    summary_sentences = [f"Regarding {format_source_label(c)}: '{c['text']}'" for c in context_sentences]
    answer = (
        "No LLM endpoints (Groq/Ollama) are currently running or configured. Here is the direct factual extraction from the repository index:\n\n"
        + "\n\n".join(summary_sentences)
    )
    return {
        "answer": answer,
        "confidence": "medium",
        "contradiction": "LLM validation offline; unable to parse semantic conflicts dynamically."
    }


# Endpoints

@app.get("/repos", response_model=List[RepoResponse])
def list_repositories():
    if GITHUB_TOKEN:
        repos = github_get("/user/repos", {"per_page": 100, "sort": "updated", "affiliation": "owner,collaborator,organization_member"})
        if repos is not None:
            return [github_repo_response(repo) for repo in repos]

    driver = get_neo4j_driver()
    if driver is None:
        return MOCK_REPOS

    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (r:Repository)
                OPTIONAL MATCH (c:Commit)-[:BELONGS_TO]->(r)
                OPTIONAL MATCH (c)-[:HAS_RATIONALE]->(rat:Rationale)
                RETURN r.id AS id, r.name AS name, r.description AS description, r.language AS language, count(rat) AS decisions
                """
            )
            repos = []
            for record in result:
                repos.append({
                    "id": record["id"],
                    "name": record["name"],
                    "description": record["description"],
                    "language": record["language"],
                    "decisions": record["decisions"]
                })
            
            if not repos:
                return MOCK_REPOS
            
            # Ensure mock repos are accessible in the UI
            for mock_r in MOCK_REPOS:
                if not any(r["id"] == mock_r["id"] for r in repos):
                    repos.append(mock_r)

            return repos
    except Exception as e:
        print(f"Error querying Neo4j for repos: {e}")
        return MOCK_REPOS
    finally:
        driver.close()

@app.get("/repos/{repo_id:path}", response_model=RepoResponse)
def get_repository(repo_id: str):
    if GITHUB_TOKEN and repo_id.count("/") == 1:
        repo = github_get(f"/repos/{repo_id}")
        if repo:
            return github_repo_response(repo)

    driver = get_neo4j_driver()
    if driver is not None:
        try:
            with driver.session() as session:
                result = session.run(
                    """
                    MATCH (r:Repository {id: $repo_id})
                    OPTIONAL MATCH (c:Commit)-[:BELONGS_TO]->(r)
                    OPTIONAL MATCH (c)-[:HAS_RATIONALE]->(rat:Rationale)
                    RETURN r.id AS id, r.name AS name, r.description AS description, r.language AS language, count(rat) AS decisions
                    """,
                    repo_id=repo_id
                )
                record = result.single()
                if record:
                    return {
                        "id": record["id"],
                        "name": record["name"],
                        "description": record["description"],
                        "language": record["language"],
                        "decisions": record["decisions"]
                    }
        except Exception as e:
            print(f"Error getting repo {repo_id}: {e}")
        finally:
            driver.close()

    mock_r = next((r for r in MOCK_REPOS if r["id"] == repo_id), None)
    if mock_r:
        return mock_r

    raise HTTPException(status_code=404, detail="Repository not found")

@app.post("/query", response_model=AnswerResponse)
def query_decision(req: QueryRequest):
    if GITHUB_TOKEN and req.repoId.count("/") == 1:
        commits = github_get(f"/repos/{req.repoId}/commits", {"per_page": 20}) or []
        words = {word for word in req.question.lower().split() if len(word) > 4}
        relevant = [
            commit for commit in commits
            if words.intersection(set(commit["commit"]["message"].lower().split()))
        ] or commits[:5]
        if relevant:
            citations = []
            related = []
            for commit in relevant[:5]:
                sha = commit["sha"]
                message = commit["commit"]["message"].split("\n", 1)[0]
                author = commit["author"]["login"] if commit.get("author") else commit["commit"]["author"]["name"]
                citations.append({"id": sha[:8], "label": f"commit {sha[:7]}", "kind": "commit", "url": commit["html_url"]})
                related.append({"id": sha[:8], "title": message, "when": format_when(commit["commit"]["author"]["date"]), "author": f"@{author}"})
            return {
                "question": req.question,
                "answer": "Recent GitHub history for this repository does not contain a dedicated decision record for this question. The closest commit evidence is: " + "; ".join(c["commit"]["message"].split("\n", 1)[0] for c in relevant[:5]),
                "confidence": "medium" if len(relevant) > 1 else "low",
                "citations": citations,
                "related": related,
            }

    is_mock = any(req.repoId == r["id"] for r in MOCK_REPOS)
    is_empty = check_db_empty_for_repo(req.repoId)

    if is_mock and is_empty:
        q_lower = req.question.lower()
        hit = None
        for a in MOCK_ANSWERS:
            keywords = [w for w in a["question"].lower().split() if len(w) > 4]
            if any(kw in q_lower for kw in keywords):
                hit = a
                break
        
        if hit:
            return {
                "question": req.question,
                "answer": hit["answer"],
                "confidence": hit["confidence"],
                "citations": [Citation(**c) for c in hit["citations"]],
                "contradiction": hit.get("contradiction"),
                "related": [Decision(**d) for d in hit["related"]]
            }
        
        return {
            "question": req.question,
            "answer": "Based on the indexed history for this repository, there is no decision record matching this query in the static mocks.",
            "confidence": "low",
            "citations": [
                {"id": "cf1", "label": "commit 91cc02", "kind": "commit", "url": "#"},
                {"id": "cf2", "label": "PR #205", "kind": "pr", "url": "#"}
            ],
            "related": [
                {"id": "df1", "title": "Review comments migrated into ADR format", "when": "4 months ago", "author": "@jonas"},
                {"id": "df2", "title": "Backfilled decision index for legacy commits", "when": "11 months ago", "author": "@rin"}
            ]
        }

    # Retrieve context
    search_res = []
    
    # Try Qdrant semantic search if embeddings are working
    qdrant = get_qdrant_client()
    if HAS_EMBEDDINGS and qdrant:
        try:
            if qdrant.collection_exists(COLLECTION_NAME):
                query_vector = model.encode(req.question).tolist()
                hits = qdrant.search(
                    collection_name=COLLECTION_NAME,
                    query_vector=query_vector,
                    limit=5,
                    query_filter=Filter(
                        must=[FieldCondition(key="repo", match=MatchValue(value=req.repoId))]
                    )
                )
                for h in hits:
                    search_res.append({
                        "text": h.payload["text"],
                        "commit_id": h.payload["commit_id"],
                        "type": h.payload.get("type", "commit"),
                        "url": h.payload.get("url", "#"),
                        "score": h.score
                    })
        except Exception as e:
            print(f"Qdrant search failed, falling back to Neo4j text search: {e}")

    # Fallback to database keyword search if Qdrant returned nothing or failed
    if not search_res:
        print("Using Neo4j keyword search...")
        search_res = search_neo4j_keywords(req.repoId, req.question)

    if not search_res:
        raise HTTPException(status_code=404, detail="No matching rationale entries found in this repository.")

    # Pull details from Neo4j
    driver = get_neo4j_driver()
    context_sentences = []
    citations = []
    related = []
    
    if driver is not None:
        try:
            with driver.session() as session:
                for hit in search_res:
                    commit_id = hit["commit_id"]
                    
                    res = session.run(
                        """
                        MATCH (c {id: $commit_id})
                        RETURN c.repo AS repo, c.url AS url, c.author AS author, c.date AS date, c.title AS title, c.type AS type
                        """,
                        commit_id=commit_id
                    )
                    record = res.single()
                    
                    author = "@unknown"
                    date_str = ""
                    title = ""
                    url = hit["url"]
                    stype = hit.get("type", "commit")
                    
                    if record:
                        if record.get("author"):
                            author = f"@{record['author'].lower().replace(' ', '')}"
                        date_str = format_when(record.get("date"))
                        title = record.get("title") or ""
                        url = record.get("url") or url
                        stype = record.get("type") or stype

                    context_sentences.append({
                        "text": hit["text"],
                        "commit_id": commit_id,
                        "source_type": stype,
                        "author": author,
                        "date": date_str,
                        "score": hit["score"]
                    })
                    
                    stype_lower = str(stype).lower()
                    display_cid = commit_id[:6] if len(commit_id) >= 6 else commit_id
                    if stype_lower == "issue":
                        kind = "issue"
                        label = f"issue #{commit_id}"
                    elif stype_lower in ("pull_request", "pr"):
                        kind = "pr"
                        label = f"PR #{commit_id}"
                    else:
                        kind = "commit"
                        label = f"commit {display_cid}"

                    citations.append({
                        "id": f"c_{display_cid}",
                        "label": label,
                        "kind": kind,
                        "url": url
                    })
                    
                    related.append({
                        "id": f"d_{display_cid}",
                        "title": title or hit["text"],
                        "when": date_str,
                        "author": author
                    })
        finally:
            driver.close()
    else:
        # Fallback if Neo4j is offline but Qdrant is somehow online
        for hit in search_res:
            context_sentences.append({
                "text": hit["text"],
                "commit_id": hit["commit_id"],
                "author": "@unknown",
                "date": "recently",
                "score": hit["score"]
            })
            citations.append({
                "id": f"c_{hit['commit_id'][:6]}",
                "label": f"commit {hit['commit_id'][:6]}",
                "kind": "commit",
                "url": hit["url"]
            })
            related.append({
                "id": f"d_{hit['commit_id'][:6]}",
                "title": hit["text"],
                "when": "recently",
                "author": "@unknown"
            })

    # Synthesize LLM answer
    synthesis = call_llm(req.question, context_sentences)
    
    # Determine confidence score
    avg_score = sum(h["score"] for h in search_res) / len(search_res)
    confidence = synthesis.get("confidence", "low")
    if avg_score >= 0.8:
        confidence = "high"
    elif avg_score >= 0.5:
        confidence = "medium"

    return {
        "question": req.question,
        "answer": synthesis["answer"],
        "confidence": confidence,
        "citations": citations,
        "contradiction": synthesis.get("contradiction"),
        "related": related
    }

@app.post("/recall", response_model=List[RecallMatchResponse])
def recall_issue(req: RecallRequest):
    is_mock = any(req.repoId == r["id"] for r in MOCK_REPOS)
    is_empty = check_db_empty_for_repo(req.repoId)
    
    if is_mock and is_empty:
        return MOCK_RECALLS

    search_res = []
    
    # Try Qdrant semantic search first
    qdrant = get_qdrant_client()
    if HAS_EMBEDDINGS and qdrant:
        try:
            if qdrant.collection_exists(COLLECTION_NAME):
                text_to_embed = f"{req.title}\n{req.body}"
                query_vector = model.encode(text_to_embed).tolist()
                hits = qdrant.search(
                    collection_name=COLLECTION_NAME,
                    query_vector=query_vector,
                    limit=5,
                    query_filter=Filter(
                        must=[FieldCondition(key="repo", match=MatchValue(value=req.repoId))]
                    )
                )
                for h in hits:
                    search_res.append({
                        "text": h.payload["text"],
                        "commit_id": h.payload["commit_id"],
                        "url": h.payload.get("url", "#"),
                        "score": h.score
                    })
        except Exception as e:
            print(f"Qdrant search failed in recall, falling back: {e}")

    # Fallback to database keyword search
    if not search_res:
        search_res = search_neo4j_keywords(req.repoId, f"{req.title} {req.body}")
        
    driver = get_neo4j_driver()
    matches = []
    
    if driver is not None:
        try:
            with driver.session() as session:
                for hit in search_res:
                    commit_id = hit["commit_id"]
                    
                    res = session.run(
                        """
                        MATCH (c:Commit {id: $commit_id})
                        RETURN c.title AS title, c.date AS date, c.body AS body
                        """,
                        commit_id=commit_id
                    )
                    record = res.single()
                    
                    title = hit["text"]
                    when = "recently"
                    summary = hit["text"]
                    
                    if record:
                        title = record["title"] or title
                        when = format_when(record["date"])
                        summary = f"Identified in commit rationale: '{hit['text']}'"
                        
                    matches.append({
                        "id": commit_id,
                        "title": title,
                        "summary": summary,
                        "similarity": float(hit["score"]),
                        "when": when,
                        "status": "merged"
                    })
        finally:
            driver.close()
    else:
        for hit in search_res:
            matches.append({
                "id": hit["commit_id"],
                "title": hit["text"],
                "summary": f"Similar decision rationale: '{hit['text']}'",
                "similarity": float(hit["score"]),
                "when": "recently",
                "status": "merged"
            })
            
    return matches

@app.get("/agent/status")
def agent_status(repoId: str):
    repo_slug = repoId.split('/')[-1]
    raw_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
    results_path = raw_dir / f"{repo_slug}_agent_results.json"
    
    if not results_path.exists():
        alt_path = raw_dir / f"{repo_slug}_issues_agent_results.json"
        if alt_path.exists():
            results_path = alt_path
        else:
            return {
                "repoId": repoId,
                "last_run": None,
                "scanned": 0,
                "results": []
            }
        
    try:
        data = json.loads(results_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {
                "repoId": repoId,
                "last_run": None,
                "scanned": len(data),
                "results": data
            }
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading scan status: {e}")

@app.post("/agent/scan")
def agent_scan(repoId: str):
    repo_slug = repoId.split('/')[-1]
    raw_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
    issues_path = raw_dir / f"{repo_slug}_issues.json"
    if not issues_path.exists():
        raise HTTPException(status_code=404, detail=f"No ingested issues found for {repoId}. Run ingest.py --issues first.")
    issues = json.loads(issues_path.read_text(encoding="utf-8"))
    issues = [i for i in issues if i.get("type") == "issue"]
    data = run_repo_scan(repo_slug, repoId, issues)
    return data

class CommentRequest(BaseModel):
    repoId: str
    issueNumber: str
    message: str

@app.post("/agent/comment")
def agent_comment(req: CommentRequest):
    import requests
    from pathlib import Path
    
    owner_repo = req.repoId
    if "/" not in owner_repo:
        repo_name = req.repoId.split('/')[-1]
        issues_path = Path(__file__).resolve().parent.parent / "data" / "raw" / f"{repo_name}_issues.json"
        if issues_path.exists():
            try:
                issues = json.loads(issues_path.read_text(encoding="utf-8"))
                if issues:
                    owner_repo = issues[0]["repo"]
            except Exception:
                pass
                
    if "/" not in owner_repo:
        raise HTTPException(status_code=400, detail=f"Cannot determine owner/repo format for {req.repoId}")
        
    owner, repo = owner_repo.split("/", 1)
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{req.issueNumber}/comments"
    
    headers = {
        "Accept": "application/vnd.github+json",
    }
    github_token = GITHUB_TOKEN
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    else:
        raise HTTPException(
            status_code=400,
            detail="GITHUB_TOKEN is not set in environment variables. Cannot post comment."
        )
        
    try:
        response = requests.post(url, headers=headers, json={"body": req.message}, timeout=15)
        response.raise_for_status()
        return {"status": "success", "comment": response.json()}
    except requests.exceptions.RequestException as e:
        detail = str(e)
        if e.response is not None:
            try:
                detail = e.response.json().get("message", detail)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Failed to post comment to GitHub: {detail}")

@app.get("/health")
def get_health(repoId: str):
    from health import compute_health
    
    repo_slug = repoId.split('/')[-1]
    raw_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
    issues_path = raw_dir / f"{repo_slug}_issues.json"

    is_mock = any(repoId == r["id"] for r in MOCK_REPOS)
    
    if not issues_path.exists() or (is_mock and check_db_empty_for_repo(repoId)):
        return {
            "repoId": repoId,
            "open_issue_count": 14,
            "backlog_growth_rate": 5.2,
            "duplicate_rate": 18.5,
            "active_contributor_count": 8,
            "security_flag_count": 2,
            "avg_response_time_hours": 12.4,
            "response_time_label": "Time to last activity (proxy for maintainer response time)",
            "history": [
                {"timestamp": "2026-08-01T00:00:00Z", "open_issue_count": 10},
                {"timestamp": "2026-08-08T00:00:00Z", "open_issue_count": 12},
                {"timestamp": "2026-08-15T00:00:00Z", "open_issue_count": 13},
                {"timestamp": "2026-08-20T00:00:00Z", "open_issue_count": 14}
            ]
        }

    return compute_health(repoId)

@app.get("/brief")
def get_brief(repoId: str):
    from brief import generate_weekly_brief
    return generate_weekly_brief(repoId)

@app.post("/agent/feedback")
def agent_feedback(req: FeedbackRequest):
    import json
    from pathlib import Path
    from datetime import datetime, timezone
    
    timestamp = datetime.now(timezone.utc).isoformat()
    repo_slug = req.repoId.split('/')[-1]
    
    # Try Neo4j first
    driver = None
    if NEO4J_PASSWORD:
        try:
            driver = get_neo4j_driver()
        except Exception as e:
            print(f"Neo4j driver setup failed: {e}")
    neo4j_success = False
    if driver is not None:
        try:
            with driver.session() as session:
                session.run(
                    """
                    MERGE (c {id: $issue_id})
                    ON CREATE SET c.repo = $repo_id, c.type = "issue"
                    WITH c
                    CREATE (f:Feedback {
                        decision: $decision,
                        correct: $correct,
                        correctedDecision: $corrected_decision,
                        timestamp: $timestamp
                    })
                    CREATE (c)-[:HAS_FEEDBACK]->(f)
                    """,
                    issue_id=str(req.issueId),
                    repo_id=req.repoId,
                    decision=req.decision,
                    correct=req.correct,
                    corrected_decision=req.correctedDecision,
                    timestamp=timestamp
                )
                neo4j_success = True
        except Exception as e:
            print(f"Neo4j feedback storage failed: {e}")
        finally:
            driver.close()
            
    if not neo4j_success:
        # Fallback to local JSON file
        raw_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        feedback_path = raw_dir / f"{repo_slug}_feedback.json"
        
        feedbacks = []
        if feedback_path.exists():
            try:
                feedbacks = json.loads(feedback_path.read_text(encoding="utf-8"))
                if not isinstance(feedbacks, list):
                    feedbacks = []
            except Exception:
                feedbacks = []
                
        feedbacks.append({
            "repoId": req.repoId,
            "issueId": req.issueId,
            "decision": req.decision,
            "correct": req.correct,
            "correctedDecision": req.correctedDecision,
            "timestamp": timestamp
        })
        
        try:
            feedback_path.write_text(json.dumps(feedbacks, indent=2), encoding="utf-8")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to persist local feedback JSON: {e}")
            
    return {"status": "success", "neo4j": neo4j_success}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
