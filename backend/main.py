import os
import json
import hashlib
import hmac
import requests
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from neo4j import GraphDatabase
import chromadb
from datetime import datetime, timezone
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
from agent import run_scan
import ast
import re
import shutil
import tempfile
import subprocess
import threading
from pydriller import Repository as PyDrillerRepo

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
CHROMA_URL = os.getenv("CHROMA_URL", "http://localhost:8000")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
GITHUB_API_BASE = "https://api.github.com"
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "15"))

# Global states for tracking in-progress ingestions
INGESTION_STATUSES = {}


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


def github_graphql(query: str, variables: Optional[dict] = None) -> Optional[dict]:
    if not GITHUB_TOKEN:
        return None
    try:
        response = requests.post(
            "https://api.github.com/graphql",
            headers={
                "Authorization": f"bearer {GITHUB_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": variables or {}},
            timeout=15,
        )
        if response.status_code == 200:
            return response.json()
        print(f"GraphQL request failed: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error calling GitHub GraphQL API: {e}")
    return None


def parse_code_structure(repo_dir: Path) -> dict:
    """Scan all source files in the cloned repository and extract classes, functions, and import details."""
    structure = {
        "files": [],
        "classes": [],
        "functions": [],
        "imports": [],
        "dependencies": [],
        "documents": [],
        "relationships": []
    }
    
    for path in repo_dir.rglob("*"):
        try:
            if path.is_file():
                # Skip build/dependency directories to speed up parsing
                if any(ignored in path.parts for ignored in (".git", "node_modules", ".venv", "venv", "__pycache__", "build", "dist")):
                    continue
                
                rel_path = str(path.relative_to(repo_dir)).replace("\\", "/")
                ext = path.suffix.lower().replace(".", "")
                
                # We cover common codebase extensions
                if ext not in ("py", "js", "ts", "tsx", "jsx", "java", "go", "rs", "cpp", "h", "cs", "md", "json", "txt", "toml", "xml"):
                    continue
                    
                file_id = f"{rel_path}"
                structure["files"].append({
                    "id": file_id,
                    "path": rel_path,
                    "name": path.name,
                    "extension": ext,
                    "folder": str(Path(rel_path).parent).replace("\\", "/"),
                    "size_bytes": path.stat().st_size
                })

                if ext == "md":
                    structure["documents"].append({
                        "id": file_id,
                        "path": rel_path,
                        "title": path.stem,
                        "kind": "readme" if path.name.lower() == "readme.md" else "markdown",
                        "content": path.read_text(encoding="utf-8", errors="ignore")[:50000]
                    })
                
                # Python AST parsing
                if ext == "py":
                    try:
                        content = path.read_text(encoding="utf-8", errors="ignore")
                        for imported in re.findall(r"(?m)^\s*(?:import|from)\s+([A-Za-z0-9_\.]+)", content):
                            structure["imports"].append({"file_id": file_id, "name": imported})
                        tree = ast.parse(content)
                        for node in ast.walk(tree):
                            if isinstance(node, ast.ClassDef):
                                class_id = f"{rel_path}::{node.name}"
                                structure["classes"].append({
                                    "id": class_id,
                                    "name": node.name,
                                    "file_id": file_id
                                })
                                structure["relationships"].append((file_id, "DEFINES", class_id))
                            elif isinstance(node, ast.FunctionDef):
                                func_id = f"{rel_path}::{node.name}"
                                structure["functions"].append({
                                    "id": func_id,
                                    "name": node.name,
                                    "file_id": file_id
                                })
                                structure["relationships"].append((file_id, "DEFINES", func_id))
                    except Exception as ast_err:
                        print(f"Error doing AST parse on {rel_path}: {ast_err}")
                
                # Regex parsing for JS/TS/Java/Cpp/Rust/Go/C#
                elif ext in ("js", "ts", "tsx", "jsx", "java", "rs", "go", "cpp", "cs"):
                    try:
                        content = path.read_text(encoding="utf-8", errors="ignore")
                        for imported in re.findall(r"(?m)^\s*import\s+(?:[^\"']+from\s+)?[\"']([^\"']+)[\"']", content):
                            structure["imports"].append({"file_id": file_id, "name": imported})
                        # Match classes
                        classes = re.findall(r"\bclass\s+([A-Za-z0-9_]+)", content)
                        for cls_name in classes:
                            class_id = f"{rel_path}::{cls_name}"
                            structure["classes"].append({
                                "id": class_id,
                                "name": cls_name,
                                "file_id": file_id
                            })
                            structure["relationships"].append((file_id, "DEFINES", class_id))
                        
                        # Match functions/methods
                        functions = re.findall(r"\b(?:function|fn|def)\s+([A-Za-z0-9_]+)", content)
                        methods = re.findall(r"\b([A-Za-z0-9_]+)\s*\([^)]*\)\s*\{", content)
                        all_funcs = list(set(functions + methods))
                        for f_name in all_funcs:
                            if f_name in ("if", "for", "while", "switch", "catch", "return", "class", "function", "export", "import"):
                                continue
                            func_id = f"{rel_path}::{f_name}"
                            structure["functions"].append({
                                "id": func_id,
                                "name": f_name,
                                "file_id": file_id
                            })
                            structure["relationships"].append((file_id, "DEFINES", func_id))
                    except Exception as rex_err:
                        print(f"Error parsing structural regex on {rel_path}: {rex_err}")
                if path.name.lower() in ("package.json", "requirements.txt", "pyproject.toml", "pom.xml", "go.mod", "cargo.toml"):
                    structure["dependencies"].append({
                        "file_id": file_id,
                        "name": path.name,
                        "content": path.read_text(encoding="utf-8", errors="ignore")[:50000]
                    })
        except Exception as file_err:
            print(f"Skipping {path} due to error: {file_err}")
            
    return structure


def parse_wiki_documents(wiki_dir: Path, repo_id: str) -> list[dict]:
    documents = []
    if not wiki_dir.exists():
        return documents
    for path in wiki_dir.rglob("*.md"):
        if ".git" in path.parts:
            continue
        rel_path = str(path.relative_to(wiki_dir)).replace("\\", "/")
        documents.append({
            "id": f"wiki/{rel_path}",
            "path": f"wiki/{rel_path}",
            "title": path.stem,
            "kind": "wiki",
            "content": path.read_text(encoding="utf-8", errors="ignore")[:50000],
            "url": f"https://github.com/{repo_id}/wiki/{path.stem}"
        })
    return documents


def mine_commits_locally(repo_dir: Path) -> list:
    commits_data = []
    try:
        for commit in PyDrillerRepo(str(repo_dir)).traverse_commits():
            files_modified = []
            for mf in commit.modified_files:
                files_modified.append({
                    "filename": mf.filename or "",
                    "filepath": mf.new_path or mf.old_path or "",
                    "added": mf.added_lines,
                    "deleted": mf.deleted_lines,
                    "diff": mf.diff or ""
                })
            commit_time = commit.author_date.isoformat() if commit.author_date else ""
            commits_data.append({
                "sha": commit.hash,
                "message": commit.msg,
                "author": commit.author.name or "unknown",
                "date": commit_time,
                "additions": commit.insertions,
                "deletions": commit.deletions,
                "parents": commit.parents,
                "files": files_modified
            })
    except Exception as e:
        print(f"Error mining commits locally with PyDriller: {e}")
    return commits_data


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
            repo_id = issues[0].get("repo", repo_slug) if issues else repo_slug
            data = run_repo_scan(repo_slug, repo_id, issues)
            from orchestrator import start_monitoring
            start_monitoring(repo_id)
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

chroma_client = None

def get_chroma_client():
    global chroma_client
    if chroma_client is None and HAS_EMBEDDINGS:
        chroma_url = os.getenv("CHROMA_URL", "http://localhost:8000")
        from urllib.parse import urlparse
        parsed = urlparse(chroma_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 8000
        
        # Avoid connecting to own FastAPI app
        if host in ("localhost", "127.0.0.1") and port == 8000:
            try:
                data_dir = Path(__file__).resolve().parent.parent / "data" / "chroma_db"
                data_dir.mkdir(parents=True, exist_ok=True)
                print(f"Bypassing localhost:8000 port conflict. Using local persistent Chroma client at {data_dir}...")
                chroma_client = chromadb.PersistentClient(path=str(data_dir))
            except Exception as ex:
                print(f"Chroma local initialization failed: {ex}")
            return chroma_client

        try:
            client = chromadb.HttpClient(host=host, port=int(port))
            client.heartbeat()
            chroma_client = client
        except Exception as e:
            print(f"Warning: Could not connect to Chroma at {chroma_url}: {e}")
            try:
                data_dir = Path(__file__).resolve().parent.parent / "data" / "chroma_db"
                data_dir.mkdir(parents=True, exist_ok=True)
                print(f"Falling back to local persistent Chroma client at {data_dir}...")
                chroma_client = chromadb.PersistentClient(path=str(data_dir))
            except Exception as ex:
                print(f"Chroma fallback failed: {ex}")
    return chroma_client


def get_chroma_sync_timestamps(repo_id: str) -> tuple[Optional[str], Optional[str]]:
    """Return the latest commit date and issue/PR update time stored in Chroma."""
    chroma = get_chroma_client()
    if not chroma:
        return None, None

    try:
        collection = chroma.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )
        stored = collection.get(where={"repo": repo_id}, include=["metadatas"])
        commit_dates = []
        issue_updates = []
        for metadata in stored.get("metadatas") or []:
            if not metadata:
                continue
            source_type = metadata.get("type", "commit")
            date_value = metadata.get("date") or ""
            updated_value = metadata.get("updated_at") or date_value
            if source_type == "commit" and date_value:
                commit_dates.append(date_value)
            elif source_type in ("issue", "pull_request", "pr") and updated_value:
                issue_updates.append(updated_value)
        return max(commit_dates, default=None), max(issue_updates, default=None)
    except Exception as e:
        print(f"Failed to fetch sync timestamps from Chroma: {e}")
        return None, None

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

class IngestRequest(BaseModel):
    repoUrl: str

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
    action: Optional[str] = None
    note: Optional[str] = None

class FollowUpRequest(BaseModel):
    repoId: str
    issueNumber: str
    execute: bool = False

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
                """
                MATCH (source {repo: $repo_id})
                WHERE source:Commit OR source:Issue OR source:PullRequest
                RETURN count(source) AS count
                """,
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
    if stype in ("issue", "pull_request", "pr") and ":" in cid:
        cid = cid.rsplit(":", 1)[-1]
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
    db_repos = []
    driver = get_neo4j_driver()
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
                        "name": record["name"],
                        "description": record["description"] or "No description.",
                        "language": record["language"] or "Other",
                        "decisions": record["decisions"] or 0
                    })
        except Exception as e:
            print(f"Error querying Neo4j for repos: {e}")
        finally:
            driver.close()

    # If GitHub token is present, we also fetch user's repos
    gh_repos = []
    if GITHUB_TOKEN:
        try:
            repos = github_get("/user/repos", {"per_page": 100, "sort": "updated", "affiliation": "owner,collaborator,organization_member"})
            if repos is not None:
                gh_repos = [github_repo_response(repo) for repo in repos]
        except Exception as e:
            print(f"Error calling GitHub API for user repos: {e}")

    # Combine them (avoiding duplicates)
    # Prefer db_repos (so decisions count is accurate), then gh_repos, then MOCK_REPOS
    combined = {r["id"]: r for r in db_repos}
    for r in gh_repos:
        if r["id"] not in combined:
            combined[r["id"]] = r
    for r in MOCK_REPOS:
        if r["id"] not in combined:
            combined[r["id"]] = r

    return list(combined.values())

@app.get("/repos/ingest/status/{repo_id:path}")
def get_ingestion_status(repo_id: str):
    repo_id = normalize_repo_id(repo_id)
    if repo_id not in INGESTION_STATUSES:
        # Check if DB has it
        is_empty = check_db_empty_for_repo(repo_id)
        if not is_empty:
            return {"status": "done"}
        return {"status": "not_started"}
    return {"status": INGESTION_STATUSES[repo_id]}


@app.get("/repos/monitor/status")
def get_monitor_status(repoId: str):
    from orchestrator import get_monitor_run
    return get_monitor_run(normalize_repo_id(repoId))


@app.post("/repos/monitor/run")
def run_repository_monitor(repoId: str):
    from orchestrator import get_monitor_run, start_monitoring
    repo_id = normalize_repo_id(repoId)
    if check_db_empty_for_repo(repo_id):
        raise HTTPException(status_code=409, detail="Ingest the repository before starting monitoring")
    started = start_monitoring(repo_id)
    return {"repoId": repo_id, "started": started, "run": get_monitor_run(repo_id)}


@app.get("/repos/investigations")
def get_repository_investigations(repoId: str):
    from orchestrator import get_monitor_run
    run = get_monitor_run(normalize_repo_id(repoId))
    return {
        "repoId": run.get("repoId", normalize_repo_id(repoId)),
        "status": run.get("status", "not_started"),
        "steps": run.get("steps", []),
        "investigations": run.get("results", {}).get("investigations", []),
    }


@app.get("/repos/inbox")
def get_maintainer_inbox(repoId: str):
    """Return only selectively escalated issues for the maintainer inbox."""
    from orchestrator import get_monitor_run
    run = get_monitor_run(normalize_repo_id(repoId))
    investigations = run.get("results", {}).get("investigations", [])
    escalated = [item for item in investigations if item.get("decision") == "escalate"]
    return {
        "repoId": normalize_repo_id(repoId),
        "status": run.get("status", "not_started"),
        "count": len(escalated),
        "items": escalated,
    }


@app.get("/repos/graph")
def get_repository_graph(repoId: str, limit: int = 500):
    """Return a repository-scoped node/edge graph for Neo4j visualization and tests."""
    repo_id = normalize_repo_id(repoId)
    driver = get_neo4j_driver()
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j is unavailable")

    limit = max(1, min(limit, 2000))
    nodes = {}
    edges = {}
    try:
        with driver.session() as session:
            node_result = session.run(
                """
                MATCH (n)
                WHERE n.id = $repo_id OR n.id STARTS WITH $prefix
                RETURN labels(n) AS labels, properties(n) AS properties
                LIMIT $limit
                """,
                repo_id=repo_id,
                prefix=f"{repo_id}:",
                limit=limit
            )
            for record in node_result:
                properties = dict(record["properties"] or {})
                node_id = str(properties.get("id", ""))
                if node_id:
                    nodes[node_id] = {
                        "id": node_id,
                        "labels": list(record["labels"] or []),
                        "properties": properties,
                    }

            edge_result = session.run(
                """
                MATCH (source)-[relationship]->(target)
                WHERE (source.id = $repo_id OR source.id STARTS WITH $prefix)
                  AND (target.id = $repo_id OR target.id STARTS WITH $prefix)
                RETURN source.id AS source, type(relationship) AS type,
                       target.id AS target, properties(relationship) AS properties
                LIMIT $limit
                """,
                repo_id=repo_id,
                prefix=f"{repo_id}:",
                limit=limit
            )
            for record in edge_result:
                edge_id = f"{record['source']}|{record['type']}|{record['target']}"
                edges[edge_id] = {
                    "source": record["source"],
                    "target": record["target"],
                    "type": record["type"],
                    "properties": dict(record["properties"] or {}),
                }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to read repository graph: {e}")
    finally:
        driver.close()

    return {
        "repoId": repo_id,
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
    }


@app.get("/repos/graph/summary")
def get_repository_graph_summary(repoId: str):
    repo_id = normalize_repo_id(repoId)
    driver = get_neo4j_driver()
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j is unavailable")
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (n)
                WHERE n.id = $repo_id OR n.id STARTS WITH $prefix
                UNWIND labels(n) AS label
                RETURN label, count(*) AS count
                ORDER BY label
                """,
                repo_id=repo_id,
                prefix=f"{repo_id}:"
            )
            counts = {record["label"]: record["count"] for record in result}
            return {"repoId": repo_id, "counts": counts, "total": sum(counts.values())}
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

def normalize_repo_id(input_str: str) -> str:
    input_str = input_str.strip()
    input_str = input_str.rstrip("/")
    if "github.com/" in input_str:
        parts = input_str.split("github.com/")[-1].split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    if "git@github.com:" in input_str:
        parts = input_str.split("git@github.com:")[-1].replace(".git", "").split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    if "/" in input_str:
        parts = input_str.split("/")
        return f"{parts[0]}/{parts[1]}"
    return input_str

def get_node_label_local(source_type: str) -> str:
    st = (source_type or "").lower()
    if st == "issue":
        return "Issue"
    elif st in ("pull_request", "pr"):
        return "PullRequest"
    elif st == "discussion":
        return "Discussion"
    elif st == "documentation":
        return "Documentation"
    return "Commit"


def graph_source_id(repo_id: str, source_id: object) -> str:
    value = str(source_id)
    return value if value.startswith(f"{repo_id}:") else f"{repo_id}:{value}"

def write_ingested_data_to_neo4j(
    repo_id: str,
    repo_meta: dict,
    commits: list,
    discussions: list,
    ast_structure: dict,
    issues: list
):
    driver = get_neo4j_driver()
    if driver is None:
        return
    try:
        with driver.session() as session:
            # 1. Merge Repository Node
            session.run(
                """
                MERGE (r:Repository {id: $repo_id})
                SET r.name = $repo_name,
                    r.description = $repo_desc,
                    r.language = $repo_lang,
                    r.stars = $stars,
                    r.forks = $forks,
                    r.topics = $topics,
                    r.license = $license,
                    r.owner = $owner
                """,
                repo_id=repo_id,
                repo_name=repo_meta["name"],
                repo_desc=repo_meta["description"],
                repo_lang=repo_meta["language"],
                stars=repo_meta["stars"],
                forks=repo_meta["forks"],
                topics=repo_meta.get("topics", []),
                license=repo_meta["license"],
                owner=repo_meta["owner"]
            )

            # 2. Merge Files
            for f in ast_structure["files"]:
                session.run(
                    """
                    MERGE (f:File {id: $id})
                    SET f.path = $path, f.name = $name, f.extension = $extension,
                        f.folder = $folder, f.size_bytes = $size_bytes
                    WITH f
                    MATCH (r:Repository {id: $repo_id})
                    MERGE (f)-[:BELONGS_TO]->(r)
                    """,
                    id=f"{repo_id}:{f['id']}",
                    path=f["path"],
                    name=f["name"],
                    extension=f["extension"],
                    folder=f.get("folder", ""),
                    size_bytes=f.get("size_bytes", 0),
                    repo_id=repo_id
                )

            for imported in ast_structure.get("imports", []):
                session.run(
                    """
                    MATCH (f:File {id: $file_id})
                    MERGE (m:Module {id: $module_id})
                    SET m.name = $name
                    MERGE (f)-[:IMPORTS]->(m)
                    """,
                    file_id=f"{repo_id}:{imported['file_id']}",
                    module_id=f"{repo_id}:module:{imported['name']}",
                    name=imported["name"]
                )

            for dependency in ast_structure.get("dependencies", []):
                session.run(
                    """
                    MATCH (f:File {id: $file_id})
                    MERGE (d:DependencyManifest {id: $dependency_id})
                    SET d.name = $name, d.content = $content
                    MERGE (f)-[:DECLARES_DEPENDENCIES]->(d)
                    """,
                    file_id=f"{repo_id}:{dependency['file_id']}",
                    dependency_id=f"{repo_id}:dependency:{dependency['name']}",
                    name=dependency["name"],
                    content=dependency["content"]
                )

            for document in ast_structure.get("documents", []):
                session.run(
                    """
                    MERGE (d:Documentation {id: $document_id})
                    SET d.path = $path, d.title = $title, d.kind = $kind, d.content = $content, d.url = $url
                    WITH d
                    MATCH (r:Repository {id: $repo_id})
                    MERGE (d)-[:DOCUMENTS]->(r)
                    """,
                    document_id=f"{repo_id}:doc:{document['id']}",
                    path=document["path"],
                    title=document["title"],
                    kind=document["kind"],
                    content=document["content"],
                    url=document.get("url", f"https://github.com/{repo_id}/blob/main/{document['path']}"),
                    repo_id=repo_id
                )

            # 3. Merge Classes
            for cls in ast_structure["classes"]:
                session.run(
                    """
                    MERGE (c:Class {id: $id})
                    SET c.name = $name
                    WITH c
                    MATCH (f:File {id: $file_id})
                    MERGE (f)-[:DEFINES]->(c)
                    """,
                    id=f"{repo_id}:{cls['id']}",
                    name=cls["name"],
                    file_id=f"{repo_id}:{cls['file_id']}"
                )

            # 4. Merge Functions
            for func in ast_structure["functions"]:
                session.run(
                    """
                    MERGE (f:Function {id: $id})
                    SET f.name = $name
                    WITH f
                    MATCH (file:File {id: $file_id})
                    MERGE (file)-[:DEFINES]->(f)
                    """,
                    id=f"{repo_id}:{func['id']}",
                    name=func["name"],
                    file_id=f"{repo_id}:{func['file_id']}"
                )

            # 5. Merge Commits Details
            for c in commits:
                session.run(
                    """
                    MERGE (co:Commit {id: $id})
                    SET co.message = $message,
                        co.sha = $sha,
                        co.title = $title,
                        co.url = $url,
                        co.repo = $repo_id,
                        co.author = $author,
                        co.date = $date,
                        co.lines_added = $additions,
                        co.lines_deleted = $deletions
                    WITH co
                    MATCH (r:Repository {id: $repo_id})
                    MERGE (co)-[:BELONGS_TO]->(r)
                    """,
                    id=graph_source_id(repo_id, c["sha"]),
                    sha=c["sha"],
                    title=c.get("title", c["message"].split("\n", 1)[0]),
                    url=c.get("url", "#"),
                    repo_id=repo_id,
                    message=c["message"],
                    author=c["author"],
                    date=c["date"],
                    additions=c.get("additions", 0),
                    deletions=c.get("deletions", 0)
                )

                # Link committed files if AST File matches
                for mf in c.get("files", []):
                    file_node_id = f"{repo_id}:{mf['filepath']}"
                    session.run(
                        """
                        MATCH (co:Commit {id: $commit_id})
                        MATCH (f:File {id: $file_id})
                        MERGE (co)-[m:MODIFIED]->(f)
                        SET m.lines_added = $added, m.lines_deleted = $deleted,
                            m.diff = $diff, m.status = $status
                        """,
                        commit_id=graph_source_id(repo_id, c["sha"]),
                        file_id=file_node_id,
                        added=mf.get("added", 0),
                        deleted=mf.get("deleted", 0),
                        diff=mf.get("diff", ""),
                        status=mf.get("status", "")
                    )

                author_id = f"{repo_id}:user:{c['author']}"
                session.run(
                    """
                    MERGE (u:User {id: $user_id})
                    SET u.name = $name
                    WITH u
                    MATCH (co:Commit {id: $commit_id})
                    MERGE (co)-[:AUTHORED_BY]->(u)
                    """,
                    user_id=author_id,
                    name=c["author"],
                    commit_id=graph_source_id(repo_id, c["sha"])
                )

                for parent_sha in c.get("parents", []):
                    session.run(
                        """
                        MATCH (child:Commit {id: $child_id})
                        MERGE (parent:Commit {id: $parent_id})
                        SET parent.repo = $repo_id, parent.sha = $parent_sha
                        MERGE (child)-[:PARENT_OF]->(parent)
                        """,
                        child_id=graph_source_id(repo_id, c["sha"]),
                        parent_id=graph_source_id(repo_id, parent_sha),
                        parent_sha=parent_sha,
                        repo_id=repo_id
                    )

            # 6. Merge Discussions and comments
            for d in discussions:
                session.run(
                    """
                    MERGE (di:Discussion {id: $id})
                    SET di.title = $title, di.body = $body, di.category = $category, di.author = $author
                    WITH di
                    MATCH (r:Repository {id: $repo_id})
                    MERGE (di)-[:BELONGS_TO]->(r)
                    """,
                    id=graph_source_id(repo_id, d["id"]),
                    repo_id=repo_id,
                    title=d["title"],
                    body=d["body"],
                    category=d["category"],
                    author=d["author"]
                )
                session.run(
                    """
                    MERGE (u:User {id: $user_id})
                    SET u.name = $name
                    WITH u
                    MATCH (di:Discussion {id: $discussion_id})
                    MERGE (di)-[:AUTHORED_BY]->(u)
                    """,
                    user_id=f"{repo_id}:user:{d['author']}",
                    name=d["author"],
                    discussion_id=graph_source_id(repo_id, d["id"])
                )

                # Merge comments
                for comment in d.get("comments", []):
                    session.run(
                        """
                        MERGE (cm:Comment {id: $id})
                        SET cm.body = $body, cm.author = $author
                        WITH cm
                        MATCH (di:Discussion {id: $disc_id})
                        MERGE (di)-[:HAS_COMMENT]->(cm)
                        """,
                        id=graph_source_id(repo_id, comment["id"]),
                        body=comment["body"],
                        author=comment["author"],
                        disc_id=graph_source_id(repo_id, d["id"])
                    )
                    session.run(
                        """
                        MERGE (u:User {id: $user_id})
                        SET u.name = $name
                        WITH u
                        MATCH (cm:Comment {id: $comment_id})
                        MERGE (cm)-[:AUTHORED_BY]->(u)
                        """,
                        user_id=f"{repo_id}:user:{comment['author']}",
                        name=comment["author"],
                        comment_id=graph_source_id(repo_id, comment["id"])
                    )

            # 7. Merge Issues and PRs
            for item in issues:
                itype = "Issue" if item["type"] == "issue" else "PullRequest"
                
                # Merge main node
                session.run(
                    f"""
                    MERGE (i:{itype} {{id: $id}})
                    SET i.title = $title,
                        i.body = $body,
                        i.repo = $repo_id,
                        i.state = $state,
                        i.author = $author,
                        i.date = $date,
                        i.updated_at = $updated_at,
                        i.closed_at = $closed_at,
                        i.closed_by = $closed_by,
                        i.url = $url,
                        i.milestone = $milestone
                    WITH i
                    MATCH (r:Repository {{id: $repo_id}})
                    MERGE (i)-[:BELONGS_TO]->(r)
                    """,
                    id=f"{repo_id}:{item['id']}",
                    title=item["title"],
                    body=item["body"],
                    state=item["state"],
                    author=item["author"],
                    date=item["date"],
                    updated_at=item["updated_at"],
                    closed_at=item.get("closed_at"),
                    closed_by=item.get("closed_by"),
                    url=item["url"],
                    milestone=item.get("milestone"),
                    repo_id=repo_id
                )

                session.run(
                    """
                    MERGE (u:User {id: $user_id})
                    SET u.name = $name
                    WITH u
                    MATCH (i {id: $item_id})
                    MERGE (i)-[:AUTHORED_BY]->(u)
                    """,
                    user_id=f"{repo_id}:user:{item['author']}",
                    name=item["author"],
                    item_id=f"{repo_id}:{item['id']}"
                )
                
                # Merge merged status if Pull Request
                if item["type"] == "pull_request":
                    session.run(
                        """
                        MATCH (pr:PullRequest {id: $id})
                        SET pr.merged = $merged,
                            pr.merged_at = $merged_at,
                            pr.merge_commit = $merge_commit,
                            pr.merged_by = $merged_by
                        """,
                        id=f"{repo_id}:{item['id']}",
                        merged=item.get("merged", False),
                        merged_at=item.get("merged_at"),
                        merge_commit=item.get("merge_commit"),
                        merged_by=item.get("merged_by")
                    )

                    for changed_file in item.get("changed_files", []):
                        file_id = f"{repo_id}:{changed_file['path']}"
                        session.run(
                            """
                            MERGE (f:File {id: $file_id})
                            SET f.path = $path, f.name = $name
                            WITH f
                            MATCH (pr:PullRequest {id: $pr_id})
                            MERGE (pr)-[c:CHANGED]->(f)
                            SET c.lines_added = $added, c.lines_deleted = $deleted,
                                c.change_type = $change_type
                            """,
                            file_id=file_id,
                            path=changed_file["path"],
                            name=Path(changed_file["path"]).name,
                            pr_id=f"{repo_id}:{item['id']}",
                            added=changed_file.get("additions", 0),
                            deleted=changed_file.get("deletions", 0),
                            change_type=changed_file.get("changeType", "")
                        )
                    
                    # Merge reviewers
                    for rev_name in item.get("reviewers", []):
                        session.run(
                            """
                            MATCH (pr:PullRequest {id: $id})
                            MERGE (u:User {id: $rev_id})
                            SET u.name = $rev_name
                            MERGE (pr)-[:HAS_REVIEWER]->(u)
                            """,
                            id=f"{repo_id}:{item['id']}",
                            rev_id=f"{repo_id}:user:{rev_name}",
                            rev_name=rev_name
                        )

                for label_name in item.get("labels", []):
                    session.run(
                        f"""
                        MATCH (i:{itype} {{id: $item_id}})
                        MERGE (label:Label {{id: $label_id}})
                        SET label.name = $label_name
                        MERGE (i)-[:HAS_LABEL]->(label)
                        """,
                        item_id=f"{repo_id}:{item['id']}",
                        label_id=f"{repo_id}:label:{label_name}",
                        label_name=label_name
                    )

                # Merge milestone node
                if item.get("milestone"):
                    session.run(
                        f"""
                        MATCH (i:{itype} {{id: $id}})
                        MERGE (m:Milestone {{id: $ms_id}})
                        SET m.title = $title
                        MERGE (i)-[:HAS_MILESTONE]->(m)
                        """,
                        id=f"{repo_id}:{item['id']}",
                        ms_id=f"{repo_id}:ms:{item['milestone']}",
                        title=item["milestone"]
                    )

                # Merge comments
                for comment in item.get("comments_list", []):
                    session.run(
                        f"""
                        MERGE (cm:Comment {{id: $id}})
                        SET cm.body = $body, cm.author = $author,
                            cm.review_state = $review_state,
                            cm.submitted_at = $submitted_at
                        WITH cm
                        MATCH (i:{itype} {{id: $item_id}})
                        MERGE (i)-[:HAS_COMMENT]->(cm)
                        """,
                        id=graph_source_id(repo_id, comment["id"]),
                        body=comment["body"],
                        author=comment["author"],
                        review_state=comment.get("review_state"),
                        submitted_at=comment.get("submitted_at"),
                        item_id=f"{repo_id}:{item['id']}"
                    )
                    session.run(
                        """
                        MERGE (u:User {id: $user_id})
                        SET u.name = $name
                        WITH u
                        MATCH (cm:Comment {id: $comment_id})
                        MERGE (cm)-[:AUTHORED_BY]->(u)
                        """,
                        user_id=f"{repo_id}:user:{comment['author']}",
                        name=comment["author"],
                        comment_id=graph_source_id(repo_id, comment["id"])
                    )
    except Exception as neo_err:
        print(f"Failed to write detailed ingestion schema to Neo4j: {neo_err}")
    finally:
        driver.close()

def perform_ingestion_background(
    repo_id: str,
    owner: str,
    repo_name: str,
    repo_desc: str,
    repo_lang: str,
    repo_meta: dict,
    headers: dict,
    is_sync: bool = False
):
    try:
        last_commit_date = None
        last_issue_update = None
        if is_sync:
            last_commit_date, last_issue_update = get_chroma_sync_timestamps(repo_id)
        # 1. Fetch Discussions, Issues, and PRs via GraphQL
        INGESTION_STATUSES[repo_id] = "graphql"
        discussions = []
        graphql_issues = []
        if GITHUB_TOKEN:
            gql_query = """
            query($owner: String!, $name: String!) {
              repository(owner: $owner, name: $name) {
                discussions(first: 30) {
                  nodes {
                    id
                    number
                    title
                    body
                    author {
                      login
                    }
                    category {
                      name
                    }
                    comments(first: 10) {
                      nodes {
                        id
                        body
                        author {
                          login
                        }
                      }
                    }
                  }
                }
                issues(first: 30, orderBy: {field: UPDATED_AT, direction: DESC}) {
                  nodes {
                    id
                    number
                    title
                    body
                    state
                    createdAt
                    updatedAt
                    url
                    closedAt
                    closedBy { login }
                    author {
                      login
                    }
                    milestone {
                      title
                    }
                    labels(first: 10) {
                      nodes {
                        name
                      }
                    }
                    comments(first: 10) {
                      nodes {
                        id
                        body
                        author {
                          login
                        }
                      }
                    }
                  }
                }
                pullRequests(first: 30, orderBy: {field: UPDATED_AT, direction: DESC}) {
                  nodes {
                    id
                    number
                    title
                    body
                    state
                    createdAt
                    updatedAt
                    url
                    merged
                                        mergedAt
                                        mergeCommit { oid }
                                        mergedBy { login }
                                        files(first: 100) {
                                            nodes {
                                                path
                                                additions
                                                deletions
                                                changeType
                                            }
                                        }
                                        reviews(first: 30) {
                                            nodes {
                                                id
                                                body
                                                state
                                                submittedAt
                                                author { login }
                                            }
                                        }
                    author {
                      login
                    }
                    milestone {
                      title
                    }
                    labels(first: 10) {
                      nodes {
                        name
                      }
                    }
                    reviewRequests(first: 10) {
                      nodes {
                        requestedReviewer {
                          ... on User {
                            login
                          }
                        }
                      }
                    }
                    comments(first: 10) {
                      nodes {
                        id
                        body
                        author {
                          login
                        }
                      }
                    }
                  }
                }
              }
            }
            """
            try:
                gql_res = github_graphql(gql_query, {"owner": owner, "name": repo_name})
                if gql_res and "data" in gql_res and gql_res["data"] and "repository" in gql_res["data"] and gql_res["data"]["repository"]:
                    repo_node = gql_res["data"]["repository"]
                    
                    # Parse Discussions
                    disc_nodes = repo_node.get("discussions", {}).get("nodes") or []
                    for node in disc_nodes:
                        cmt_nodes = node.get("comments", {}).get("nodes") or []
                        comments = []
                        for cmt in cmt_nodes:
                            comments.append({
                                "id": cmt["id"],
                                "body": cmt["body"] or "",
                                "author": cmt["author"]["login"] if cmt.get("author") else "unknown"
                            })
                        discussions.append({
                            "id": node["id"],
                            "number": node["number"],
                            "title": node["title"],
                            "body": node["body"] or "",
                            "author": node["author"]["login"] if node.get("author") else "unknown",
                            "category": node["category"]["name"] if node.get("category") else "General",
                            "comments": comments
                        })
                        
                    # Parse Issues
                    issue_nodes = repo_node.get("issues", {}).get("nodes") or []
                    for node in issue_nodes:
                        if last_issue_update and node["updatedAt"] <= last_issue_update:
                            continue
                        cmt_nodes = node.get("comments", {}).get("nodes") or []
                        comments = []
                        for cmt in cmt_nodes:
                            comments.append({
                                "id": cmt["id"],
                                "body": cmt["body"] or "",
                                "author": cmt["author"]["login"] if cmt.get("author") else "unknown"
                            })
                        graphql_issues.append({
                            "repo": repo_id,
                            "type": "issue",
                            "id": str(node["number"]),
                            "title": node["title"],
                            "body": node["body"] or "",
                            "author": node["author"]["login"] if node.get("author") else "unknown",
                            "date": node["createdAt"],
                            "updated_at": node["updatedAt"],
                            "state": node["state"],
                            "labels": [l["name"] for l in node.get("labels", {}).get("nodes") or []],
                            "comments": len(comments),
                            "comments_list": comments,
                            "url": node["url"],
                            "closed_at": node.get("closedAt"),
                            "closed_by": node.get("closedBy", {}).get("login") if node.get("closedBy") else None,
                            "linked_pull_requests": [],
                            "linked_commits": [],
                            "milestone": node["milestone"]["title"] if node.get("milestone") else None,
                            "merged": False,
                            "reviewers": []
                        })

                    # Parse PRs
                    pr_nodes = repo_node.get("pullRequests", {}).get("nodes") or []
                    for node in pr_nodes:
                        if last_issue_update and node["updatedAt"] <= last_issue_update:
                            continue
                        cmt_nodes = node.get("comments", {}).get("nodes") or []
                        comments = []
                        for cmt in cmt_nodes:
                            comments.append({
                                "id": cmt["id"],
                                "body": cmt["body"] or "",
                                "author": cmt["author"]["login"] if cmt.get("author") else "unknown"
                            })
                        
                        reviewers = []
                        req_rev = node.get("reviewRequests", {}).get("nodes") or []
                        for rr in req_rev:
                            reviewer = rr.get("requestedReviewer")
                            if reviewer and "login" in reviewer:
                                reviewers.append(reviewer["login"])

                        review_comments = []
                        for review in node.get("reviews", {}).get("nodes") or []:
                            review_comments.append({
                                "id": review["id"],
                                "body": review.get("body") or "",
                                "author": review.get("author", {}).get("login") if review.get("author") else "unknown",
                                "review_state": review.get("state"),
                                "submitted_at": review.get("submittedAt")
                            })
                                
                        graphql_issues.append({
                            "repo": repo_id,
                            "type": "pull_request",
                            "id": str(node["number"]),
                            "title": node["title"],
                            "body": node["body"] or "",
                            "author": node["author"]["login"] if node.get("author") else "unknown",
                            "date": node["createdAt"],
                            "updated_at": node["updatedAt"],
                            "state": node["state"],
                            "labels": [l["name"] for l in node.get("labels", {}).get("nodes") or []],
                            "comments": len(comments),
                            "comments_list": comments + review_comments,
                            "url": node["url"],
                            "milestone": node["milestone"]["title"] if node.get("milestone") else None,
                            "merged": node["merged"],
                            "merged_at": node.get("mergedAt"),
                            "merge_commit": node.get("mergeCommit", {}).get("oid") if node.get("mergeCommit") else None,
                            "merged_by": node.get("mergedBy", {}).get("login") if node.get("mergedBy") else None,
                            "changed_files": node.get("files", {}).get("nodes") or [],
                            "reviewers": reviewers
                        })
            except Exception as e:
                print(f"GraphQL discussions/issues/PRs fetch failed for {repo_id}: {e}")

        # 2. Clone Repository only for a full ingest; sync uses API data only.
        INGESTION_STATUSES[repo_id] = "cloning"
        ast_structure = {"files": [], "classes": [], "functions": [], "imports": [], "dependencies": [], "documents": [], "relationships": []}
        commits_pydriller = []
        clone_url = f"https://github.com/{owner}/{repo_name}.git"
        git_path = shutil.which("git") or "git"
        
        if not is_sync:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
                try:
                    print(f"Cloning {clone_url} to temporary directory...")
                    subprocess.run(
                        [git_path, "clone", "--depth", "30", clone_url, tmp_dir],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=45
                    )
                    tmp_path = Path(tmp_dir)
                    
                    # 3. Code AST analysis
                    INGESTION_STATUSES[repo_id] = "ast_parsing"
                    ast_structure = parse_code_structure(tmp_path)

                    wiki_path = tmp_path / "__wiki__"
                    try:
                        subprocess.run(
                            [git_path, "clone", "--depth", "1", f"https://github.com/{owner}/{repo_name}.wiki.git", str(wiki_path)],
                            capture_output=True,
                            text=True,
                            check=True,
                            timeout=30
                        )
                        ast_structure["documents"].extend(parse_wiki_documents(wiki_path, repo_id))
                    except Exception as wiki_err:
                        print(f"Wiki unavailable for {repo_id}; continuing without wiki pages: {wiki_err}")
                    
                    # 4. Commit mining
                    INGESTION_STATUSES[repo_id] = "commits"
                    commits_pydriller = mine_commits_locally(tmp_path)
                except Exception as clone_err:
                    print(f"Failed to clone/mine repository: {clone_err}")

        # 5. Fetch Issues & PRs
        INGESTION_STATUSES[repo_id] = "issues"
        # Fetch last 30 commits via REST API
        try:
            commit_params = {"per_page": 30}
            if last_commit_date:
                commit_params["since"] = last_commit_date
            c_res = requests.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/commits",
                headers=headers,
                params=commit_params,
                timeout=15
            )
            c_res.raise_for_status()
            raw_commits = c_res.json()
        except Exception as e:
            print(f"Error fetching commits for {repo_id}: {e}")
            raw_commits = []

        commits = []
        for c in raw_commits:
            c_date = c.get("commit", {}).get("author", {}).get("date", "")
            if last_commit_date and c_date and c_date <= last_commit_date:
                continue
            pyd_match = next((item for item in commits_pydriller if item["sha"] == c["sha"]), {})
            detail = github_get(f"/repos/{owner}/{repo_name}/commits/{c['sha']}") or {}
            detail_files = []
            for changed_file in detail.get("files", []):
                detail_files.append({
                    "filename": changed_file.get("filename", ""),
                    "filepath": changed_file.get("filename", ""),
                    "added": changed_file.get("additions", 0),
                    "deleted": changed_file.get("deletions", 0),
                    "diff": changed_file.get("patch", "") or "",
                    "status": changed_file.get("status", "")
                })
            commits.append({
                "repo": repo_id,
                "type": "commit",
                "id": c["sha"],
                "sha": c["sha"],
                "title": c["commit"]["message"].split("\n")[0],
                "body": c["commit"]["message"],
                "message": c["commit"]["message"],
                "author": c["commit"]["author"]["name"] if c.get("commit", {}).get("author") else "unknown",
                "date": c["commit"]["author"]["date"] if c.get("commit", {}).get("author") else "",
                "url": c["html_url"],
                "additions": detail.get("stats", {}).get("additions", pyd_match.get("additions") or 0),
                "deletions": detail.get("stats", {}).get("deletions", pyd_match.get("deletions") or 0),
                "files": detail_files or pyd_match.get("files") or [],
                "parents": [parent.get("sha") for parent in detail.get("parents", [])] or pyd_match.get("parents") or []
            })

        for pyd in commits_pydriller:
            if last_commit_date and pyd["date"] and pyd["date"] <= last_commit_date:
                continue
            if not any(c["sha"] == pyd["sha"] for c in commits):
                commits.append({
                    "repo": repo_id,
                    "type": "commit",
                    "id": pyd["sha"],
                    "sha": pyd["sha"],
                    "title": pyd["message"].split("\n")[0],
                    "body": pyd["message"],
                    "message": pyd["message"],
                    "author": pyd["author"],
                    "date": pyd["date"],
                    "url": f"https://github.com/{repo_id}/commit/{pyd['sha']}",
                    "additions": pyd["additions"],
                    "deletions": pyd["deletions"],
                    "files": pyd["files"],
                    "parents": pyd["parents"]
                })

        if graphql_issues:
            issues = graphql_issues
        else:
            try:
                i_res = requests.get(f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/issues?per_page=30", headers=headers, timeout=15)
                i_res.raise_for_status()
                raw_issues = i_res.json()
            except Exception as e:
                print(f"Error fetching issues/PRs for {repo_id}: {e}")
                raw_issues = []

            issues = []
            for item in raw_issues:
                if last_issue_update and item.get("updated_at") and item["updated_at"] <= last_issue_update:
                    continue
                is_pr = "pull_request" in item
                issues.append({
                    "repo": repo_id,
                    "type": "pull_request" if is_pr else "issue",
                    "id": str(item["number"]),
                    "title": item["title"],
                    "body": item.get("body") or "",
                    "author": item["user"]["login"] if item.get("user") else "unknown",
                    "date": item["created_at"],
                    "updated_at": item["updated_at"],
                    "state": item["state"],
                    "labels": [l["name"] for l in item.get("labels", [])],
                    "comments": item.get("comments", 0),
                    "comments_list": [],
                    "url": item["html_url"],
                    "milestone": item.get("milestone", {}).get("title") if item.get("milestone") else None,
                    "merged": False,
                    "reviewers": []
                })

        # 5.5. Normalize data through Repository Intelligence Pipeline
        from intelligence_pipeline import RepositoryIntelligencePipeline
        intel_pipeline = RepositoryIntelligencePipeline(repo_id)
        norm_res = intel_pipeline.normalize_pipeline(commits, discussions, issues)
        commits = norm_res["commits"]
        discussions = norm_res["discussions"]
        issues = norm_res["issues"]
        cross_references = norm_res["cross_references"]

        raw_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{repo_name}_issues.json").write_text(
            json.dumps(issues, indent=2), encoding="utf-8"
        )

        # 6. Extract Rationale
        INGESTION_STATUSES[repo_id] = "extracting_rationale"
        from extractor import extract_rationale
        all_text_records = []
        for c in commits:
            all_text_records.append({
                "id": c["sha"],
                "type": "commit",
                "body": c["body"],
                "title": c["title"],
                "repo": repo_id,
                "url": c["url"],
                "author": c["author"],
                "date": c["date"]
            })
        for i in issues:
            all_text_records.append({
                "id": i["id"],
                "type": i["type"],
                "body": i["body"],
                "title": i["title"],
                "repo": repo_id,
                "url": i["url"],
                "author": i["author"],
                "date": i["date"],
                "updated_at": i.get("updated_at", "")
            })
        for d in discussions:
            all_text_records.append({
                "id": d["id"],
                "type": "discussion",
                "body": d["body"],
                "title": d["title"],
                "repo": repo_id,
                "url": f"https://github.com/{repo_id}/discussions/{d['number']}",
                "author": d["author"],
                "date": ""
            })
        for document in ast_structure.get("documents", []):
            all_text_records.append({
                "id": f"doc:{document['id']}",
                "type": "documentation",
                "body": document["content"],
                "title": document["title"],
                "repo": repo_id,
                "url": document.get("url", f"https://github.com/{repo_id}/blob/main/{document['path']}"),
                "author": "repository",
                "date": ""
            })
        extracted_records = extract_rationale(all_text_records)
        for record in extracted_records:
            record["source_id"] = graph_source_id(repo_id, record["source_id"])

        # 7. Write to Neo4j
        INGESTION_STATUSES[repo_id] = "indexing"
        write_ingested_data_to_neo4j(repo_id, repo_meta, commits, discussions, ast_structure, issues)
        
        driver = get_neo4j_driver()
        if driver is not None:
            try:
                with driver.session() as session:
                    # Write Rationale records
                    for r_item in extracted_records:
                        stype = r_item.get("source_type") or r_item.get("type", "commit")
                        label = get_node_label_local(stype)
                        cypher = f"""
                        MERGE (r:Repository {{id: $repo_id}})
                        MERGE (c:{label} {{id: $source_id}})
                        SET c.repo = $repo_id, c.url = $source_url, c.has_rationale = $has_rationale, c.type = $type
                        MERGE (c)-[:BELONGS_TO]->(r)
                        WITH c
                        UNWIND $sentences AS sentence
                        MERGE (rat:Rationale {{text: sentence, commit_id: $source_id}})
                        MERGE (c)-[:HAS_RATIONALE]->(rat)
                        """
                        session.run(
                            cypher,
                            repo_id=repo_id,
                            source_id=str(r_item["source_id"]),
                            source_url=r_item["source_url"],
                            has_rationale=r_item["has_rationale"],
                            type=stype,
                            sentences=r_item["rationale_sentences"],
                        )
                    
                    # Write cross references
                    for ref in cross_references:
                        source_id = graph_source_id(repo_id, ref["source_id"])
                        st = ref["source_type"]
                        slabel = get_node_label_local(st) if st != "comment" else "Comment"
                        
                        if slabel == "Comment":
                            cypher_source = "MATCH (s:Comment {id: $source_id})"
                        else:
                            cypher_source = f"MATCH (s:{slabel} {{id: $source_id}})"

                        target_id = ref["target_id"]
                        tt = ref["target_type"]
                        rel = ref["relationship"]
                        
                        if tt == "Issue_or_PR":
                            cypher_ref = cypher_source + f"""
                            OPTIONAL MATCH (i:Issue {{id: $target_id}})
                            OPTIONAL MATCH (pr:PullRequest {{id: $target_id}})
                            WITH s, coalesce(i, pr) AS targetNode
                            WHERE targetNode IS NOT NULL
                            MERGE (s)-[r:{rel}]->(targetNode)
                            """
                        else:
                            if tt == "Commit":
                                target_id = graph_source_id(repo_id, target_id)
                            cypher_ref = cypher_source + f"""
                            MATCH (t:Commit {{id: $target_id}})
                            MERGE (s)-[r:{rel}]->(t)
                            """
                            
                        session.run(cypher_ref, source_id=source_id, target_id=target_id)
            except Exception as neo_err:
                print(f"Failed to save rationale/references to Neo4j: {neo_err}")
            finally:
                driver.close()

        # 8. Write to Chroma
        chroma = get_chroma_client()
        if HAS_EMBEDDINGS and chroma:
            from graph_store import write_to_chroma
            try:
                write_to_chroma(chroma, model, extracted_records)
            except Exception as chroma_err:
                print(f"Failed to save to Chroma during ingest: {chroma_err}")

        from orchestrator import start_monitoring
        start_monitoring(repo_id)
        INGESTION_STATUSES[repo_id] = "done"
    except Exception as e:
        print(f"Failed to perform background ingestion for {repo_id}: {e}")
        INGESTION_STATUSES[repo_id] = f"error: {str(e)}"

@app.post("/repos/ingest", response_model=RepoResponse)
def ingest_repository(req: IngestRequest):
    repo_id = normalize_repo_id(req.repoUrl)
    if "/" not in repo_id:
        raise HTTPException(status_code=400, detail="Invalid repository URL or format. Use 'owner/repo' or GitHub URL.")
        
    owner, repo_name = repo_id.split("/", 1)
    
    # 1. Fetch Repository Metadata
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    try:
        res = requests.get(f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}", headers=headers, timeout=15)
        if res.status_code == 404:
            raise HTTPException(status_code=404, detail=f"GitHub repository '{repo_id}' not found or is private.")
        res.raise_for_status()
        repo_info = res.json()
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=502, detail=f"Failed to fetch repository info from GitHub API: {e}")

    repo_desc = repo_info.get("description") or "No description provided."
    repo_lang = repo_info.get("language") or "Other"
    
    repo_meta = {
        "name": repo_name,
        "description": repo_desc,
        "language": repo_lang,
        "topics": repo_info.get("topics") or [],
        "stars": repo_info.get("stargazers_count") or 0,
        "forks": repo_info.get("forks_count") or 0,
        "license": repo_info.get("license", {}).get("name") if repo_info.get("license") else "None",
        "owner": owner
    }
    decisions_count = 0

    # Check if this repo has already been ingested
    is_empty = check_db_empty_for_repo(repo_id)
    if not is_empty:
        # Get decision count from Neo4j DB
        decisions_count = 0
        driver = get_neo4j_driver()
        if driver is not None:
            try:
                with driver.session() as session:
                    res = session.run(
                        """
                        MATCH (r:Repository {id: $repo_id})
                        OPTIONAL MATCH (c:Commit)-[:BELONGS_TO]->(r)
                        OPTIONAL MATCH (c)-[:HAS_RATIONALE]->(rat:Rationale)
                        RETURN count(rat) AS decisions
                        """,
                        repo_id=repo_id
                    )
                    record = res.single()
                    if record:
                        decisions_count = record["decisions"]
            except Exception as neo_err:
                print(f"Error querying decision count for {repo_id}: {neo_err}")
            finally:
                driver.close()
                
        return {
            "id": repo_id,
            "name": repo_name,
            "description": repo_desc,
            "language": repo_lang,
            "decisions": decisions_count
        }

    # Launch background thread if not already running
    if repo_id not in INGESTION_STATUSES or INGESTION_STATUSES[repo_id].startswith("error"):
        INGESTION_STATUSES[repo_id] = "graphql"
        t = threading.Thread(
            target=perform_ingestion_background,
            args=(repo_id, owner, repo_name, repo_desc, repo_lang, repo_meta, headers)
        )
        t.start()

    return {
        "id": repo_id,
        "name": repo_name,
        "description": repo_desc,
        "language": repo_lang,
        "decisions": decisions_count
    }

@app.post("/repos/sync", response_model=RepoResponse)
def sync_repository(req: IngestRequest):
    repo_id = normalize_repo_id(req.repoUrl)
    if "/" not in repo_id:
        raise HTTPException(status_code=400, detail="Invalid repository URL or format. Use 'owner/repo' or GitHub URL.")
        
    owner, repo_name = repo_id.split("/", 1)
    
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    try:
        res = requests.get(f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}", headers=headers, timeout=15)
        if res.status_code == 404:
            raise HTTPException(status_code=404, detail=f"GitHub repository '{repo_id}' not found or is private.")
        res.raise_for_status()
        repo_info = res.json()
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=502, detail=f"Failed to fetch repository info from GitHub API: {e}")

    repo_desc = repo_info.get("description") or "No description provided."
    repo_lang = repo_info.get("language") or "Other"
    
    repo_meta = {
        "name": repo_name,
        "description": repo_desc,
        "language": repo_lang,
        "topics": repo_info.get("topics") or [],
        "stars": repo_info.get("stargazers_count") or 0,
        "forks": repo_info.get("forks_count") or 0,
        "license": repo_info.get("license", {}).get("name") if repo_info.get("license") else "None",
        "owner": owner
    }

    # Start background ingestion sync thread
    INGESTION_STATUSES[repo_id] = "graphql"
    t = threading.Thread(
         target=perform_ingestion_background,
         args=(repo_id, owner, repo_name, repo_desc, repo_lang, repo_meta, headers, True)
    )
    t.start()

    decisions_count = 0
    driver = get_neo4j_driver()
    if driver is not None:
        try:
            with driver.session() as session:
                res = session.run(
                    """
                    MATCH (r:Repository {id: $repo_id})
                    OPTIONAL MATCH (c:Commit)-[:BELONGS_TO]->(r)
                    OPTIONAL MATCH (c)-[:HAS_RATIONALE]->(rat:Rationale)
                    RETURN count(rat) AS decisions
                    """,
                    repo_id=repo_id
                )
                record = res.single()
                if record:
                    decisions_count = record["decisions"]
        except Exception as neo_err:
            print(f"Error querying decision count for {repo_id}: {neo_err}")
        finally:
            driver.close()

    return {
        "id": repo_id,
        "name": repo_name,
        "description": repo_desc,
        "language": repo_lang,
        "decisions": decisions_count
    }


@app.post("/webhooks/github")
async def github_webhook(request: Request):
    """Receive GitHub activity and enqueue repository-scoped incremental sync."""
    if not GITHUB_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="GITHUB_WEBHOOK_SECRET is not configured")

    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature")

    payload = json.loads(body.decode("utf-8")) if body else {}
    event = request.headers.get("X-GitHub-Event", "ping")
    if event == "ping":
        return {"accepted": True, "event": "ping"}

    repo_id = (payload.get("repository") or {}).get("full_name")
    if not repo_id:
        raise HTTPException(status_code=400, detail="Webhook payload has no repository")

    supported_events = {"push", "issues", "issue_comment", "pull_request", "pull_request_review", "discussion", "discussion_comment", "create", "delete"}
    if event not in supported_events:
        return {"accepted": True, "event": event, "ignored": True, "reason": "unsupported event"}

    sync_repository(IngestRequest(repoUrl=repo_id))
    return {"accepted": True, "event": event, "repoId": repo_id, "queued": True}

@app.post("/repos/query", response_model=AnswerResponse)
@app.post("/query", response_model=AnswerResponse)
def query_decision(req: QueryRequest):
    # Retrieve context
    search_res = []
    if not HAS_EMBEDDINGS:
        raise HTTPException(status_code=503, detail="RAG embedding model is unavailable")

    chroma = get_chroma_client()
    if chroma:
        try:
            collection = chroma.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"}
            )
            query_vector = model.encode(req.question).tolist()
            results = collection.query(
                query_embeddings=[query_vector],
                n_results=5,
                where={"repo": req.repoId}
            )
            if results and "ids" in results and results["ids"]:
                ids = results["ids"][0]
                distances = results["distances"][0] if "distances" in results and results["distances"] else []
                metadatas = results["metadatas"][0] if "metadatas" in results and results["metadatas"] else []
                documents = results["documents"][0] if "documents" in results and results["documents"] else []
                for i, doc_id in enumerate(ids):
                    meta = metadatas[i] if i < len(metadatas) else {}
                    doc = documents[i] if i < len(documents) else ""
                    dist = distances[i] if i < len(distances) else 0.0
                    similarity = 1.0 - dist
                    search_res.append({
                        "text": doc or meta.get("text") or "",
                        "commit_id": meta.get("source_id") or meta.get("commit_id"),
                        "type": meta.get("type", "commit"),
                        "url": meta.get("url", "#"),
                        "title": meta.get("title", ""),
                        "author": meta.get("author", "unknown"),
                        "date": meta.get("date", ""),
                        "score": similarity
                    })
        except Exception as e:
            print(f"Chroma search failed, falling back to Neo4j text search: {e}")

    if not search_res:
        raise HTTPException(status_code=404, detail="No matching rationale entries found in Chroma for this repository.")

    graph_hits = search_neo4j_keywords(req.repoId, req.question, limit=5)
    seen_evidence = {(hit.get("commit_id"), hit.get("text")) for hit in search_res}
    for hit in graph_hits:
        evidence_key = (hit.get("commit_id"), hit.get("text"))
        if evidence_key not in seen_evidence:
            search_res.append(hit)
            seen_evidence.add(evidence_key)
        if len(search_res) >= 8:
            break

    context_sentences = []
    citations = []
    related = []
    for hit in search_res:
        commit_id = str(hit.get("commit_id") or "unknown")
        stype = hit.get("type", "commit")
        stype_lower = str(stype).lower()
        source_number = commit_id.rsplit(":", 1)[-1]
        display_cid = source_number[:7]
        author_value = hit.get("author") or "unknown"
        author = author_value if str(author_value).startswith("@") else f"@{author_value}"
        date_str = format_when(hit.get("date"))

        context_sentences.append({
            "text": hit["text"],
            "commit_id": commit_id,
            "source_type": stype,
            "author": author,
            "date": date_str,
            "score": hit["score"]
        })

        if stype_lower == "issue":
            kind = "issue"
            label = f"issue #{source_number}"
        elif stype_lower in ("pull_request", "pr"):
            kind = "pr"
            label = f"PR #{source_number}"
        else:
            kind = "commit"
            label = f"commit {display_cid}"

        citations.append({
            "id": f"c_{display_cid}",
            "label": label,
            "kind": kind,
            "url": hit["url"]
        })
        related.append({
            "id": f"d_{display_cid}",
            "title": hit.get("title") or hit["text"],
            "when": date_str,
            "author": author
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
    
    # Try Chroma semantic search first
    chroma = get_chroma_client()
    if HAS_EMBEDDINGS and chroma:
        try:
            collection = chroma.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"}
            )
            text_to_embed = f"{req.title}\n{req.body}"
            query_vector = model.encode(text_to_embed).tolist()
            results = collection.query(
                query_embeddings=[query_vector],
                n_results=5,
                where={"repo": req.repoId}
            )
            if results and "ids" in results and results["ids"]:
                ids = results["ids"][0]
                distances = results["distances"][0] if "distances" in results and results["distances"] else []
                metadatas = results["metadatas"][0] if "metadatas" in results and results["metadatas"] else []
                documents = results["documents"][0] if "documents" in results and results["documents"] else []
                for i, doc_id in enumerate(ids):
                    meta = metadatas[i] if i < len(metadatas) else {}
                    doc = documents[i] if i < len(documents) else ""
                    dist = distances[i] if i < len(distances) else 0.0
                    similarity = 1.0 - dist
                    search_res.append({
                        "text": doc or meta.get("text") or "",
                        "commit_id": meta.get("commit_id"),
                        "url": meta.get("url", "#"),
                        "score": similarity
                    })
        except Exception as e:
            print(f"Chroma search failed in recall, falling back: {e}")

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


@app.post("/agent/follow-up")
def agent_follow_up(req: FollowUpRequest):
    from agent import contributor_follow_up
    repo_id = normalize_repo_id(req.repoId)
    issues_path = Path(__file__).resolve().parent.parent / "data" / "raw" / f"{repo_id.split('/')[-1]}_issues.json"
    if not issues_path.exists():
        raise HTTPException(status_code=404, detail="No normalized issue data found for this repository")
    issues = json.loads(issues_path.read_text(encoding="utf-8"))
    issue = next((item for item in issues if str(item.get("id")) == str(req.issueNumber) and item.get("type") == "issue"), None)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found in the repository index")
    follow_up = contributor_follow_up(issue)
    response = {"repoId": repo_id, "issueNumber": req.issueNumber, **follow_up, "posted": False}
    if req.execute and follow_up["needs_follow_up"]:
        agent_comment(CommentRequest(repoId=repo_id, issueNumber=req.issueNumber, message=follow_up["message"]))
        response["posted"] = True
    return response

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


@app.get("/health/investigation")
def get_health_investigation(repoId: str):
    from health import investigate_health_trend
    return investigate_health_trend(normalize_repo_id(repoId))

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
    repo_id = normalize_repo_id(req.repoId)
    repo_slug = repo_id.split('/')[-1]
    allowed_actions = {"confirm", "not_important", "duplicate", "wrong"}
    action = req.action or ("confirm" if req.correct else "wrong")
    if action not in allowed_actions:
        raise HTTPException(status_code=400, detail=f"action must be one of {sorted(allowed_actions)}")
    human_decision = req.correctedDecision or action
    issue_graph_id = graph_source_id(repo_id, req.issueId)
    
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
                    MERGE (c:Issue {id: $issue_id})
                    SET c.repo = $repo_id, c.type = "issue"
                    WITH c
                    CREATE (f:Feedback {
                        decision: $decision,
                        humanDecision: $human_decision,
                        action: $action,
                        correct: $correct,
                        correctedDecision: $human_decision,
                        note: $note,
                        timestamp: $timestamp
                    })
                    CREATE (c)-[:HAS_FEEDBACK]->(f)
                    """,
                    issue_id=issue_graph_id,
                    repo_id=repo_id,
                    decision=req.decision,
                    human_decision=human_decision,
                    action=action,
                    correct=req.correct,
                    note=req.note,
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
            "repoId": repo_id,
            "issueId": req.issueId,
            "decision": req.decision,
            "correct": req.correct,
            "correctedDecision": human_decision,
            "action": action,
            "note": req.note,
            "timestamp": timestamp
        })
        
        try:
            feedback_path.write_text(json.dumps(feedbacks, indent=2), encoding="utf-8")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to persist local feedback JSON: {e}")

    chroma_indexed = False
    chroma = get_chroma_client()
    if HAS_EMBEDDINGS and chroma:
        try:
            collection = chroma.get_or_create_collection(name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})
            feedback_text = (
                f"Maintainer correction for issue {req.issueId} in {repo_id}. "
                f"AI decision: {req.decision}. Human action: {action}. "
                f"Corrected decision: {human_decision}. Note: {req.note or 'none'}."
            )
            collection.upsert(
                ids=[f"feedback:{repo_id}:{req.issueId}:{timestamp}"],
                embeddings=[model.encode(feedback_text).tolist()],
                metadatas=[{
                    "repo": repo_id,
                    "type": "maintainer_feedback",
                    "source_id": issue_graph_id,
                    "url": f"https://github.com/{repo_id}/issues/{req.issueId}",
                    "date": timestamp,
                    "updated_at": timestamp,
                    "title": f"Maintainer correction for issue #{req.issueId}",
                    "author": "maintainer",
                }],
                documents=[feedback_text],
            )
            chroma_indexed = True
        except Exception as feedback_chroma_err:
            print(f"Maintainer feedback Chroma indexing failed: {feedback_chroma_err}")

    return {"status": "success", "neo4j": neo4j_success, "chroma": chroma_indexed, "action": action, "humanDecision": human_decision}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
