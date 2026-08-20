# TRACE Backend Data Ingestion & Extraction Pipeline

This directory contains the core backend services and ingestion pipeline for TRACE.

## Rationale Pipeline Usage

The Rationale Extraction & Storage pipeline supports **Commits**, **Issues**, and **Pull Requests**.

### 1. Ingestion (`ingest.py`)

- **Commits**:
  ```bash
  python ingest.py owner/repo --limit 50
  ```
  Produces `../data/raw/{repo}_commits.json`.

- **Issues & Pull Requests**:
  ```bash
  python ingest.py owner/repo --issues --limit 50
  ```
  Produces `../data/raw/{repo}_issues.json`.

---

### 2. Rationale Extraction (`extractor.py`)

Run rationale extraction over ingested commits, issues, or PRs:

- **Commits**:
  ```bash
  python extractor.py ../data/raw/{repo}_commits.json
  ```
  Produces `../data/raw/{repo}_commits_extracted.json`.

- **Issues & PRs**:
  ```bash
  python extractor.py ../data/raw/{repo}_issues.json
  ```
  Produces `../data/raw/{repo}_issues_extracted.json`.

---

### 3. Graph & Vector Indexing (`graph_store.py`)

Requires Neo4j and Qdrant running locally via Docker:

```bash
docker run -d --name trace-neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/your_password neo4j
docker run -d --name trace-qdrant -p 6333:6333 qdrant/qdrant
```

Load extracted rationale records into Neo4j (as `:Commit`, `:Issue`, or `:PullRequest` nodes) and Qdrant vector index:

```bash
python graph_store.py ../data/raw/{repo}_commits_extracted.json
python graph_store.py ../data/raw/{repo}_issues_extracted.json
```

---

### 4. Running the REST API (`main.py`)

Start the FastAPI backend API:

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```
