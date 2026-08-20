# TRACE

AI-powered decision intelligence platform that extracts, stores, and retrieves
the rationale behind software development decisions. The current repo is the
**backend** (ingestion pipeline + FastAPI). A previous frontend in this tree
was incomplete and has been removed.

## Structure

```
TRACE/
├── backend/                   FastAPI API + ingestion/agent pipeline
│   ├── main.py                REST API (CORS enabled for a future frontend)
│   ├── ingest.py              GitHub commits / issues / PRs → data/raw JSON
│   ├── extractor.py           Flag rationale sentences in ingested records
│   ├── graph_store.py         Load extracted rationale into Neo4j + vectors
│   ├── agent.py               RepoGuardian triage loop (duplicates, escalate)
│   ├── orchestrator.py        Background monitor runs
│   ├── langgraph_orchestrator.py  Multi-step investigation graph
│   ├── health.py              Backlog / contributor / forecast metrics
│   ├── brief.py               Weekly maintainer brief
│   └── intelligence_pipeline.py  Text cleanup and cross-reference resolution
├── data/raw/                  Ingested + extracted JSON (gitignored)
└── .env.example               Copy to .env and fill in your own values
```

## Setup

```
cd backend
pip install -r requirements.txt
cp ../.env.example ../.env   # then fill in GITHUB_TOKEN, NEO4J_PASSWORD, etc.
```

Start the API:

```
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

## Pipeline

```
python backend/ingest.py <owner>/<repo> --limit 20
python backend/ingest.py <owner>/<repo> --issues --limit 20
python backend/extractor.py data/raw/<repo>_commits.json
python backend/graph_store.py data/raw/<repo>_commits_extracted.json
```

Graph indexing needs Neo4j (and optionally Chroma) running locally.
