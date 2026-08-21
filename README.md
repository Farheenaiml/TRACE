# TRACE

TRACE is a repository intelligence platform for maintainers. It ingests a GitHub repository, extracts development decisions from commits, pull requests, issues, discussions, documentation, and source structure, then presents the results through a live web console.

## What It Provides

- Repository ingestion with visible progress stages
- Commit, issue, pull request, discussion, wiki, and source-structure indexing
- Rationale extraction and Neo4j relationship storage
- Chroma semantic search with Neo4j keyword fallback
- RepoGuardian agent investigations for duplicates, missing information, importance, security signals, and escalation
- Maintainer Inbox and Security Watch views backed by persisted agent results
- Repository health metrics, explainable risk reasons, history, and backlog forecasting
- Weekly maintainer briefs
- Repository questions answered from RAG and the Neo4j graph with citations
- Repository switching for any GitHub repository

## Architecture

```text
GitHub repository
        |
        v
FastAPI ingestion service
  |       |        |
  v       v        v
 GitHub  AST      PyDriller
  |       |        |
  +-------+--------+
          |
          v
  Rationale extraction
       |          |
       v          v
    Neo4j       Chroma
       |          |
       +----+-----+
            v
     RepoGuardian agents
            |
            v
      FastAPI REST API
            |
            v
  TanStack Start maintainer console
```

## Requirements

- Python 3.11 or newer
- Node.js 18 or newer
- Git
- A GitHub token for private repositories and higher API limits
- Neo4j for graph storage
- Optional Groq or Ollama configuration for LLM-generated summaries

The embedding model is loaded locally by `sentence-transformers`. Chroma uses the persistent database under `data/chroma_db` when configured for local storage.

## Configuration

Create a root `.env` file. Start from `.env.example` when available:

```ini
GITHUB_TOKEN=your_github_token
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_neo4j_password
CHROMA_URL=http://localhost:8000
OLLAMA_URL=http://localhost:11434
GROQ_API_KEY=your_groq_key
SCAN_INTERVAL_MINUTES=15
```

`GITHUB_TOKEN` is required for live GitHub ingestion. Groq and Ollama are optional; the backend has deterministic fallbacks for summaries and answers.

## Start Infrastructure

Start Neo4j if it is not already running:

```powershell
docker run -d --name trace-neo4j `
  -p 7474:7474 -p 7687:7687 `
  -e NEO4J_AUTH=neo4j/your_neo4j_password neo4j
```

TRACE can use its persistent local Chroma database in `data/chroma_db`. Do not run a separate Chroma HTTP service on port `8000`, because the FastAPI backend uses that port.

## Run Locally

Open two terminals.

### Backend

From the `backend` directory:

```powershell
cd C:\Users\zaree\Downloads\TRACE\TRACE\backend
..\.venv\Scripts\Activate.ps1
uvicorn main:app --reload --port 8000
```

Alternatively, from the project root:

```powershell
cd C:\Users\zaree\Downloads\TRACE\TRACE
.\.venv\Scripts\Activate.ps1
uvicorn backend.main:app --reload --port 8000
```

### Frontend

```powershell
cd C:\Users\zaree\Downloads\TRACE\TRACE\frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

The Vite development proxy forwards `/api/*` requests to `http://127.0.0.1:8000`.

## Use the Application

1. Enter a GitHub URL or an `owner/repository` value, for example `psf/requests`.
2. Watch the ingestion pipeline progress through GitHub fetch, cloning, AST parsing, commit mining, issue normalization, rationale extraction, Neo4j indexing, and monitoring.
3. Review Dashboard and Repository Intelligence for extracted counts and recent activity.
4. Review Investigations, Maintainer Inbox, Security Watch, Agent Activity, Health, Forecast, and Weekly Brief.
5. Use Ask RepoGuardian to query indexed decisions using semantic retrieval and graph context.

## Important API Endpoints

| Endpoint | Purpose |
| --- | --- |
| `POST /repos/ingest` | Start ingestion for a GitHub repository |
| `GET /repos/ingest/status/{repo_id}` | Read ingestion stage and progress counts |
| `GET /repos/{repo_id}` | Read repository metadata |
| `GET /repos/activity?repoId=owner/repo` | Read commit, issue, PR, contributor, and collaborator activity |
| `GET /repos/overview?repoId=owner/repo` | Read Neo4j entity counts and repository activity |
| `GET /repos/investigations?repoId=owner/repo` | Read agent investigations and monitoring steps |
| `GET /repos/inbox?repoId=owner/repo` | Read escalated maintainer items |
| `GET /repos/monitor/status?repoId=owner/repo` | Read the persisted monitoring run |
| `GET /health?repoId=owner/repo` | Read health metrics and explainable reasons |
| `GET /health/investigation?repoId=owner/repo` | Read health trend findings |
| `GET /brief?repoId=owner/repo` | Generate the current weekly brief |
| `POST /repos/query` | Ask a grounded repository question |
| `POST /recall` | Find similar indexed issues and decisions |

FastAPI docs are available at `http://localhost:8000/docs` while the backend is running.

## Data Sources and Metric Meaning

TRACE keeps source distinctions visible:

- GitHub totals are live GitHub API totals when available.
- Indexed counts describe records currently stored in Neo4j or local repository files.
- Agent metrics describe the latest completed RepoGuardian scan.
- The health activity-window metric is calculated from issue creation and last-update timestamps; it is not an exact maintainer first-response time.
- Forecasts use persisted health snapshots and are marked with their confidence and history status.

## Validation

Backend syntax check:

```powershell
python -m py_compile backend\main.py backend\health.py backend\orchestrator.py backend\langgraph_orchestrator.py
```

Frontend production build:

```powershell
cd frontend
npm run build
```

Backend tests, when dependencies and services are available:

```powershell
pytest backend
```

## Repository Layout

```text
backend/       FastAPI API, ingestion, extraction, graph, health, and agents
frontend/      TanStack Start maintainer console
data/raw/      Persisted repository datasets and agent results
data/chroma_db Persistent local Chroma data
docs/          Project documentation
```
