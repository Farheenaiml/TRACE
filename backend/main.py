import os
import json
import requests
from typing import List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
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

REPO_STATUSES = {}


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
class AddRepoRequest(BaseModel):
    repoUrl: str

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
MOCK_REPOS = []
MOCK_ANSWERS = []
MOCK_RECALLS = []

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
            WHERE c:Commit OR c:Issue OR c:PullRequest OR c:Discussion OR c:SourceFile OR c:Documentation
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
    elif stype == "discussion":
        return f"Discussion [#{display_id}]"
    elif stype == "source_file":
        return f"File [{display_id}]"
    elif stype == "documentation":
        return f"Doc [{display_id}]"
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

    from llm import call_llm_json
    res_dict = call_llm_json(system_prompt, user_prompt, temperature=0.2)
    if res_dict is not None:
        return res_dict

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

def get_status_file_path(repo_id: str) -> Path:
    repo_slug = repo_id.split('/')[-1]
    raw_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
    return raw_dir / f"{repo_slug}_ingest_status.json"

def write_ingest_status(repo_id: str, status: str, step: str, error: str = None):
    data = {
        "repo_id": repo_id,
        "status": status,
        "step": step,
        "error": error
    }
    REPO_STATUSES[repo_id] = data
    try:
        path = get_status_file_path(repo_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"Error writing ingest status for {repo_id}: {e}")

def read_ingest_status(repo_id: str) -> dict:
    if repo_id in REPO_STATUSES:
        return REPO_STATUSES[repo_id]
        
    path = get_status_file_path(repo_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            REPO_STATUSES[repo_id] = data
            return data
        except Exception as e:
            print(f"Error reading ingest status for {repo_id}: {e}")
            
    repo_slug = repo_id.split('/')[-1]
    raw_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
    issues_path = raw_dir / f"{repo_slug}_issues.json"
    if issues_path.exists():
        data = {
            "repo_id": repo_id,
            "status": "ready",
            "step": "done",
            "error": None
        }
        REPO_STATUSES[repo_id] = data
        return data
        
    return {
        "repo_id": repo_id,
        "status": "failed",
        "step": "not_started",
        "error": f"Repository '{repo_id}' has not been added yet."
    }

def get_ready_persisted_repos() -> list[dict]:
    ready_repos = []
    raw_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
    if raw_dir.exists():
        for path in raw_dir.glob("*_ingest_status.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("status") == "ready":
                    repo_id = data.get("repo_id")
                    if repo_id and "/" in repo_id:
                        repo_slug = repo_id.split('/')[-1]
                        ready_repos.append({
                            "id": repo_id,
                            "name": repo_slug,
                            "description": "Ready (local cache)",
                            "language": "Unknown",
                            "decisions": 0
                        })
            except Exception:
                pass
    return ready_repos

@app.get("/repos", response_model=List[RepoResponse])
def list_repositories():
    db_repos = []
    driver = None
    if NEO4J_PASSWORD:
        try:
            driver = get_neo4j_driver()
        except Exception as e:
            print(f"Neo4j driver load skipped/failed: {e}")
            driver = None
    if driver is not None:
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
                for record in result:
                    db_repos.append({
                        "id": record["id"],
                        "name": record["name"] or record["id"].split('/')[-1],
                        "description": record["description"] or "Imported from GitHub",
                        "language": record["language"] or "Unknown",
                        "decisions": record["decisions"] or 0
                    })
        except Exception as e:
            print(f"Error querying Neo4j for repos: {e}")
        finally:
            driver.close()

    # Get active/ready persisted and in-memory repos
    statuses_repos = get_ready_persisted_repos()
    for r_id, info in list(REPO_STATUSES.items()):
        if info.get("status") == "ready":
            if not any(r["id"] == r_id for r in db_repos) and not any(r["id"] == r_id for r in statuses_repos):
                repo_slug = r_id.split('/')[-1]
                statuses_repos.append({
                    "id": r_id,
                    "name": repo_slug,
                    "description": "Ready (local cache)",
                    "language": "Unknown",
                    "decisions": 0
                })

    # Retrieve GitHub repos if token is set
    gh_repos = []
    if GITHUB_TOKEN:
        repos = github_get("/user/repos", {"per_page": 100, "sort": "updated", "affiliation": "owner,collaborator,organization_member"})
        if repos is not None:
            temp_repos = [github_repo_response(repo) for repo in repos]
            for r in temp_repos:
                status_info = read_ingest_status(r["id"])
                if status_info["status"] == "ready":
                    gh_repos.append(r)

    # Combine lists
    all_repos = []
    seen_ids = set()

    for r in db_repos:
        if r["id"] not in seen_ids:
            all_repos.append(r)
            seen_ids.add(r["id"])

    for r in statuses_repos:
        if r["id"] not in seen_ids:
            all_repos.append(r)
            seen_ids.add(r["id"])

    for r in gh_repos:
        if r["id"] not in seen_ids:
            all_repos.append(r)
            seen_ids.add(r["id"])

    # Ensure mock/demo repos are always available as fallback - REMOVED

    return all_repos

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



    raise HTTPException(status_code=404, detail="Repository not found")

@app.post("/query", response_model=AnswerResponse)
def query_decision(req: QueryRequest):




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

    if not issues_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Repository health data not found for '{repoId}'. Run ingestion first."
        )

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

import re

def parse_github_repo(url_or_shorthand: str) -> tuple[str, str]:
    s = url_or_shorthand.strip()
    s = re.sub(r'^(https?://|git://|git\+ssh://|git@)', '', s)
    s = re.sub(r'^github\.com[:/]', '', s)
    s = re.sub(r'\.git$', '', s)
    s = s.strip('/')
    
    match = re.match(r'^([a-zA-Z0-9\-\_\.]+)/([a-zA-Z0-9\-\_\.]+)$', s)
    if not match:
        raise ValueError("Invalid repository format. Expected 'owner/repo' or a GitHub URL.")
    
    owner, repo = match.groups()
    return owner, repo

def run_ingestion_pipeline(owner: str, repo: str, repo_id: str, repo_name: str, description: str, language: str):
    try:
        # Start: Metadata & Commits fetching
        write_ingest_status(repo_id, "ingesting", "fetching_commits")
        
        # 1. Fetch metadata first to get default branch
        from ingest import (
            fetch_repo_metadata,
            fetch_commits,
            fetch_pull_requests,
            fetch_issues,
            fetch_discussions,
            fetch_source_files,
            fetch_documentation
        )
        metadata = fetch_repo_metadata(owner, repo)
        default_branch = metadata.get("default_branch", "main")
        
        # Update repo details from GitHub
        repo_name = metadata.get("name", repo_name)
        description = metadata.get("description") or description
        language = metadata.get("primary_language") or language
        
        commits = fetch_commits(owner, repo, limit=50)
        
        # Next: Issues & PRs
        write_ingest_status(repo_id, "ingesting", "fetching_issues")
        prs = fetch_pull_requests(owner, repo, limit=50)
        issues = fetch_issues(owner, repo, commits, prs, limit=100)
        
        combined_issues = prs + issues
        
        discussions = fetch_discussions(owner, repo)
        source_files = fetch_source_files(owner, repo, default_branch)
        docs = fetch_documentation(owner, repo, default_branch)
        
        # Next: Rationale extraction
        write_ingest_status(repo_id, "ingesting", "extracting_rationale")
        raw_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        
        commits_path = raw_dir / f"{repo}_commits.json"
        issues_path = raw_dir / f"{repo}_issues.json"
        discussions_path = raw_dir / f"{repo}_discussions.json"
        code_path = raw_dir / f"{repo}_code.json"
        docs_path = raw_dir / f"{repo}_docs.json"
        
        commits_path.write_text(json.dumps(commits, indent=2, ensure_ascii=False), encoding="utf-8")
        issues_path.write_text(json.dumps(combined_issues, indent=2, ensure_ascii=False), encoding="utf-8")
        discussions_path.write_text(json.dumps(discussions, indent=2, ensure_ascii=False), encoding="utf-8")
        code_path.write_text(json.dumps(source_files, indent=2, ensure_ascii=False), encoding="utf-8")
        docs_path.write_text(json.dumps(docs, indent=2, ensure_ascii=False), encoding="utf-8")
        
        from extractor import extract_rationale
        extracted_commits = extract_rationale(commits)
        extracted_issues = extract_rationale(combined_issues)
        extracted_discussions = extract_rationale(discussions)
        extracted_code = extract_rationale(source_files)
        extracted_docs = extract_rationale(docs)
        
        # Next: Building Graph
        write_ingest_status(repo_id, "ingesting", "building_graph")
        driver = get_neo4j_driver()
        if driver is not None:
            try:
                from graph_store import write_to_neo4j
                write_to_neo4j(driver, extracted_commits)
                write_to_neo4j(driver, extracted_issues)
                write_to_neo4j(driver, extracted_discussions)
                write_to_neo4j(driver, extracted_code)
                write_to_neo4j(driver, extracted_docs)
                
                # Merge Repository node and link
                with driver.session() as session:
                    session.run(
                        """
                        MERGE (r:Repository {id: $repo_id})
                        SET r.name = $repo_name,
                            r.description = $description,
                            r.language = $language
                        """,
                        repo_id=repo_id,
                        repo_name=repo_name,
                        description=description,
                        language=language
                    )
                    session.run(
                        """
                        MATCH (c {repo: $repo_id})
                        WHERE c:Commit OR c:Issue OR c:PullRequest OR c:Discussion OR c:SourceFile OR c:Documentation
                        MATCH (r:Repository {id: $repo_id})
                        MERGE (c)-[:BELONGS_TO]->(r)
                        """,
                        repo_id=repo_id
                    )
            except Exception as e:
                print(f"Warning: Neo4j write failed: {e}")
            finally:
                driver.close()
                
        # Next: Embedding Qdrant
        write_ingest_status(repo_id, "ingesting", "embedding")
        qdrant = get_qdrant_client()
        if qdrant is not None and HAS_EMBEDDINGS and model:
            try:
                from graph_store import write_to_qdrant
                write_to_qdrant(qdrant, model, extracted_commits)
                write_to_qdrant(qdrant, model, extracted_issues)
                write_to_qdrant(qdrant, model, extracted_discussions)
                write_to_qdrant(qdrant, model, extracted_code)
                write_to_qdrant(qdrant, model, extracted_docs)
            except Exception as e:
                print(f"Warning: Qdrant write failed: {e}")
                
        # Running automated triage scan
        triage_issues = [i for i in issues if i.get("type") == "issue"]
        run_repo_scan(repo, repo_id, triage_issues)
        
        # Done!
        write_ingest_status(repo_id, "ready", "done")
    except Exception as e:
        print(f"Ingestion pipeline failed for {repo_id}: {e}")
        write_ingest_status(repo_id, "failed", "failed", error=str(e))

@app.post("/repos/add")
def add_repository_endpoint(req: AddRepoRequest, background_tasks: BackgroundTasks):
    try:
        owner, repo = parse_github_repo(req.repoUrl)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    repo_id = f"{owner}/{repo}"
    
    # Check if already ready or currently ingesting via read_ingest_status helper
    status_info = read_ingest_status(repo_id)
    if status_info["status"] in ("ingesting", "ready"):
        return {"repoId": repo_id, "status": status_info["status"]}
        
    # Pre-flight check with GitHub API
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
        
    verify_url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        res = requests.get(verify_url, headers=headers, timeout=10)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to connect to GitHub API: {e}")
        
    if res.status_code == 404:
        raise HTTPException(status_code=400, detail=f"Repository '{owner}/{repo}' not found or is private/inaccessible.")
    elif res.status_code == 403:
        remaining = res.headers.get("X-RateLimit-Remaining")
        if remaining == "0":
            raise HTTPException(status_code=400, detail="GitHub API rate limit exceeded. Please try again later.")
        else:
            raise HTTPException(status_code=400, detail="Access denied by GitHub (403).")
    elif res.status_code != 200:
        raise HTTPException(status_code=400, detail=f"GitHub returned HTTP {res.status_code} during validation.")
        
    repo_details = res.json()
    repo_name = repo_details.get("name", repo)
    description = repo_details.get("description") or "Imported from GitHub"
    language = repo_details.get("language") or "Unknown"
    
    # Initialize status in memory and persist to file
    write_ingest_status(repo_id, "ingesting", "fetching_commits")
    
    # Kick off background task
    background_tasks.add_task(
        run_ingestion_pipeline,
        owner,
        repo,
        repo_id,
        repo_name,
        description,
        language
    )
    
    return {"repoId": repo_id, "status": "ingesting"}

@app.get("/repos/{repoId:path}/ingest-status")
def get_repo_ingest_status(repoId: str):
    return read_ingest_status(repoId)

@app.get("/repos/{repoId:path}/status")
def get_repo_status(repoId: str):
    # To satisfy old code or components that still poll /status:
    status_info = read_ingest_status(repoId)
    progress_map = {
        "fetching_commits": "Fetching commits from GitHub...",
        "fetching_issues": "Fetching issues and PRs from GitHub...",
        "extracting_rationale": "Extracting rationale sentences...",
        "building_graph": "Indexing into Neo4j...",
        "embedding": "Indexing into Qdrant...",
        "done": "Complete"
    }
    return {
        "status": status_info["status"],
        "progress": progress_map.get(status_info["step"], "Indexing..."),
        "error": status_info.get("error")
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
