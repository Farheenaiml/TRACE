# TRACE — AI-Powered Decision Intelligence Engine

TRACE is an AI-powered decision intelligence platform that automatically extracts, indexes, and retrieves the rationale behind software development decisions. It parses commits, pull requests, issues, discussions, code structures, and wikis to answer engineering questions through a conversational, evidence-grounded interface.

---

## System Architecture

```
TRACE/
├── frontend/                     React web application (TanStack Start + Vite)
│   ├── src/components/trace/     Core UI components (Answer panel, confidence badges)
│   ├── src/lib/mock-api.ts       Graceful-degradation client (tries real API first, falls back to offline mock data)
│   └── src/routes/               Pages & routing (index, connect-repo, Ask dashboard, Recall, Guardian, Health)
│
├── backend/                      REST API & agentic reasoning pipeline (FastAPI)
│   ├── main.py                   FastAPI gateway, async job scheduler, & search router
│   ├── llm.py                    Consolidated Groq -> Ollama -> local fallback LLM client
│   ├── ingest.py                 GitHub API downloader (pulls commits, PRs, issues, wiki, docs, tree)
│   ├── extractor.py              NLP parser to extract architectural rationale sentences
│   ├── graph_store.py            Neo4j Graph Database + Qdrant Vector database connectors
│   ├── agent.py                  RepoGuardian triage agent (multi-step duplicate check & scoring loop)
│   ├── health.py                 Health metrics engine & OLS trend forecaster
│   └── brief.py                  Weekly digest generator for maintainers
│
└── data/raw/                     Asynchronously ingested raw datasets and status files (Gitignored)
```

---

## Infrastructure Setup

To run TRACE end-to-end, start the local Neo4j graph database and Qdrant vector database via Docker:

```bash
# Start Neo4j
docker run -d --name trace-neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/your_password neo4j

# Start Qdrant
docker run -d --name trace-qdrant -p 6333:6333 qdrant/qdrant
```

---

## Configuration

Copy `.env.example` to `.env` in the repository root and fill in your values:

```ini
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
QDRANT_URL=http://localhost:6333
OLLAMA_URL=http://localhost:11434
GROQ_API_KEY=gsk_...
GITHUB_TOKEN=ghp_...
SCAN_INTERVAL_MINUTES=15
```

---

## Running the Services

### 1. Backend REST API
Installs requirements and starts the FastAPI server on port `8000`:
```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

### 2. Frontend Development Server
Installs dependencies and runs Vite development server:
```bash
cd frontend
npm install
npm run dev
```

---

## Ingestion & Triage Pipeline

TRACE features an asynchronous two-step ingestion pipeline triggered from the frontend UI or directly via the API:

### Step 1: Request Ingestion (`POST /repos/add`)
Send a POST request with the GitHub repository URL to initiate ingestion:
```bash
curl -X POST http://localhost:8000/repos/add \
  -H "Content-Type: application/json" \
  -d '{"repoUrl": "https://github.com/owner/repo"}'
```
This performs a validation check against the GitHub API, writes a starting state, and schedules a background worker.

### Step 2: Asynchronous Job Processing & Polling
The background task executes the full ingestion pipeline:
1. **GitHub Ingestion (`ingest.py`)**: Fetches repository metadata, commits, PRs, issues, wiki documentation, and source code trees.
2. **Rationale Extraction (`extractor.py`)**: Runs NLP algorithms to isolate and extract rationale sentences.
3. **Graph & Vector Loaders (`graph_store.py`)**: Builds entity relations in Neo4j and calculates/saves vector embeddings in Qdrant.
4. **RepoGuardian Agent Triage (`agent.py`)**: Automatically scans issues for duplicate detection, escalation scoring, and priority alerts.
5. **Mark Ready**: Writes a completion status file when done.

During processing, the frontend polls the status endpoint to display live progress:
```bash
# Get raw status
curl http://localhost:8000/repos/owner/repo/ingest-status

# Get progress strings
curl http://localhost:8000/repos/owner/repo/status
```
Once complete, the repository becomes active on the dashboard.
