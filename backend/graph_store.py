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

from dotenv import load_dotenv
from neo4j import GraphDatabase
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")

COLLECTION_NAME = "rationale_sentences"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"  # small, free, runs locally — 384 dimensions


def get_node_label(source_type: str) -> str:
    st = (source_type or "").lower()
    if st == "issue":
        return "Issue"
    elif st in ("pull_request", "pr"):
        return "PullRequest"
    elif st == "discussion":
        return "Discussion"
    elif st == "source_file":
        return "SourceFile"
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


def write_to_qdrant(qdrant: QdrantClient, model: SentenceTransformer, records: list[dict]):
    """Embed every rationale sentence and upsert into Qdrant."""
    # Create the collection if it doesn't exist yet
    if not qdrant.collection_exists(COLLECTION_NAME):
        qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=384, distance=Distance.COSINE),
        )

    points = []
    point_id = 0
    for r in records:
        stype = r.get("source_type") or r.get("type", "commit")
        for sentence in r["rationale_sentences"]:
            embedding = model.encode(sentence).tolist()
            points.append(PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "text": sentence,
                    "commit_id": str(r["source_id"]),
                    "type": stype,
                    "repo": r["repo"],
                    "url": r["source_url"],
                },
            ))
            point_id += 1

    if points:
        qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
    print(f"Wrote {len(points)} rationale sentences into Qdrant vector database.")


def main():
    parser = argparse.ArgumentParser(description="Load extracted rationale into Neo4j and Qdrant.")
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

    print("Connecting to Qdrant ...")
    qdrant = QdrantClient(url=QDRANT_URL)
    print(f"Loading embedding model ({EMBEDDING_MODEL}) — first run downloads it, ~90MB ...")
    model = SentenceTransformer(EMBEDDING_MODEL)
    write_to_qdrant(qdrant, model, records)

    print("\nDone. Open http://localhost:7474 to see the graph in Neo4j Browser.")


if __name__ == "__main__":
    main()
