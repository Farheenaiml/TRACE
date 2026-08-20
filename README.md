# TRACE

AI-powered decision intelligence platform that extracts, stores, and retrieves
the rationale behind software development decisions, and answers questions
about them through a conversational, evidence-grounded interface.

## Structure

```
TRACE/
├── frontend/                 React app (built with TanStack Start + Tailwind)
│   ├── src/components/trace/      TRACE-specific UI components
│   ├── src/lib/mock-api.ts        Placeholder data layer — swap for real API calls later
│   └── src/routes/                App pages (repo picker, dashboard, recall view)
│
├── backend/
│   ├── ingest.py              Pulls commits from a GitHub repo -> normalized JSON
│   ├── extractor.py           Reads that JSON, flags rationale sentences
│   ├── graph_store.py         Loads extracted rationale into Neo4j + Qdrant
│   └── requirements.txt
│
├── data/raw/                  Ingested + extracted JSON lands here (gitignored)
├── docs/                      Architecture notes, schema docs
└── .env.example                Copy to .env and fill in your own values
```

## Setup

**Backend**
```
cd backend
pip install -r requirements.txt
cp ../.env.example ../.env   # then fill in GITHUB_TOKEN, NEO4J_PASSWORD, etc.
```

**Frontend**
```
cd frontend
npm install
npm run dev
```

## Current pipeline (working, run in this order)

```
python backend/ingest.py <owner>/<repo> --limit 20
python backend/extractor.py data/raw/<repo>_commits.json
python backend/graph_store.py data/raw/<repo>_commits_extracted.json   # needs Neo4j + Qdrant running via Docker
```

The frontend currently runs entirely on the placeholder data in
`frontend/src/lib/mock-api.ts`. It is not yet connected to the backend above —
that connection is the next major piece of work (see below).
