"""
TRACE — Week 1 starter script (Member 3).

Takes the output of extractor.py (Member 2) and writes it into both
databases:
  - Neo4j: stores WHO wrote WHICH commit, and its rationale, as a graph
  - Qdrant: stores a numeric "meaning fingerprint" (embedding) of each
            rationale sentence, so we can later search by meaning

Requires Neo4j and Qdrant running locally via Docker BEFORE running
this script:

    docker run -d --name trace-neo4j -p 7474:7474 -p 7687:7687 \\
        -e NEO4J_AUTH=neo4j/your_password neo4j

    docker run -d --name trace-qdrant -p 6333:6333 qdrant/qdrant

Usage:
    python graph_store.py <path/to/commits_extracted.json>
"""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from neo4j import GraphDatabase
import chromadb
from sentence_transformers import SentenceTransformer

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
CHROMA_URL = os.getenv("CHROMA_URL", "http://localhost:8000")

COLLECTION_NAME = "rationale_sentences"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"  # small, free, runs locally — 384 dimensions


def get_node_label(source_type: str) -> str:
    st = (source_type or "").lower()
    if st == "issue":
        return "Issue"
    elif st in ("pull_request", "pr"):
        return "PullRequest"
    elif st == "documentation":
        return "Documentation"
    return "Commit"


def write_to_neo4j(driver, records: list[dict]):
    """Create one Commit, Issue, or PullRequest node per record, with its rationale sentences attached."""
    with driver.session() as session:
        for r in records:
            stype = r.get("source_type") or r.get("type", "commit")
            label = get_node_label(stype)
            
            cypher = f"""
            MERGE (c:{label} {{id: $source_id}})
            SET c.repo = $repo, c.url = $source_url, c.has_rationale = $has_rationale, c.type = $type
            WITH c
            UNWIND $sentences AS sentence
            MERGE (rat:Rationale {{text: sentence, commit_id: $source_id}})
            MERGE (c)-[:HAS_RATIONALE]->(rat)
            """
            session.run(
                cypher,
                source_id=str(r["source_id"]),
                repo=r["repo"],
                source_url=r["source_url"],
                has_rationale=r["has_rationale"],
                type=stype,
                sentences=r["rationale_sentences"],
            )
    print(f"Wrote {len(records)} records into Neo4j graph store.")


def get_chroma_client():
    chroma_url = os.getenv("CHROMA_URL", "http://localhost:8000")
    from urllib.parse import urlparse
    parsed = urlparse(chroma_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8000
    
    # Avoid connecting to own FastAPI app
    if host in ("localhost", "127.0.0.1") and port == 8000:
        data_dir = Path(__file__).resolve().parent.parent / "data" / "chroma_db"
        data_dir.mkdir(parents=True, exist_ok=True)
        print(f"Bypassing localhost:8000 port conflict. Using local persistent Chroma client at {data_dir}...")
        return chromadb.PersistentClient(path=str(data_dir))
        
    try:
        client = chromadb.HttpClient(host=host, port=int(port))
        client.heartbeat()
        return client
    except Exception as e:
        print(f"Warning: Could not connect to Chroma server at {chroma_url}: {e}")
        data_dir = Path(__file__).resolve().parent.parent / "data" / "chroma_db"
        data_dir.mkdir(parents=True, exist_ok=True)
        print(f"Falling back to local persistent Chroma client at {data_dir}...")
        return chromadb.PersistentClient(path=str(data_dir))


def write_to_chroma(chroma_client, model: SentenceTransformer, records: list[dict]):
    """Embed every rationale sentence and upsert into Chroma DB."""
    collection = chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"}
    )

    ids = []
    embeddings = []
    metadatas = []
    documents = []
    point_id = 0
    for r in records:
        stype = r.get("source_type") or r.get("type", "commit")
        collection.delete(
            where={
                "$and": [
                    {"repo": r["repo"]},
                    {"commit_id": str(r["source_id"])},
                ]
            }
        )
        source_documents = list(r["rationale_sentences"])
        full_context = "\n\n".join(part for part in (r.get("title", ""), r.get("body", "")) if part).strip()
        if full_context and full_context not in source_documents:
            source_documents.append(full_context)
        for sentence in source_documents:
            if not sentence or not sentence.strip():
                continue
            embedding = model.encode(sentence).tolist()
            doc_id = f"{r['repo'].replace('/', '_')}_{r['source_id']}_{point_id}"
            
            ids.append(doc_id)
            embeddings.append(embedding)
            metadatas.append({
                "commit_id": str(r["source_id"]),
                "source_id": str(r["source_id"]),
                "type": stype,
                "repo": r["repo"],
                "url": r["source_url"] or "#",
                "title": r.get("title", ""),
                "author": r.get("author", "unknown"),
                "date": r.get("date", ""),
                "updated_at": r.get("updated_at", r.get("date", "")),
            })
            documents.append(sentence)
            point_id += 1

    if ids:
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents
        )
    print(f"Wrote {len(ids)} rationale sentences into Chroma vector database.")


def main():
    parser = argparse.ArgumentParser(description="Load extracted rationale into Neo4j and Chroma.")
    parser.add_argument("input_file", help="Path to a *_extracted.json file produced by extractor.py")
    args = parser.parse_args()

    if not NEO4J_PASSWORD:
        raise SystemExit("Set NEO4J_PASSWORD in your .env file first.")

    records = json.loads(Path(args.input_file).read_text(encoding="utf-8"))
    print(f"Loaded {len(records)} extracted records from {args.input_file}.")

    print("Connecting to Neo4j ...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    write_to_neo4j(driver, records)
    driver.close()

    print("Connecting to Chroma ...")
    chroma_client = get_chroma_client()
    print(f"Loading embedding model ({EMBEDDING_MODEL}) — first run downloads it, ~90MB ...")
    model = SentenceTransformer(EMBEDDING_MODEL)
    write_to_chroma(chroma_client, model, records)

    print("\nDone. Open http://localhost:7474 to see the graph in Neo4j Browser.")


if __name__ == "__main__":
    main()
