import chromadb
from neo4j import GraphDatabase
import os
import requests
import json
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from pathlib import Path

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "farheen123")

def run_e2e_rag_test():
    repo_id = "test-org/test-rag"
    
    # 1. Clean previous run
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as s:
        s.run("MATCH (n) WHERE n.repo = $repo OR n.id STARTS WITH $repo DETACH DELETE n", repo=repo_id)
        
    # 2. Write test nodes to Neo4j
    with driver.session() as s:
        # Create repository node
        s.run("MERGE (r:Repository {id: $repo}) SET r.name='test-rag', r.language='Python'", repo=repo_id)
        
        # Create commit c1
        commit_id = "5bd067d3dd8742deb9e280e7e3bb988b4379a81a"
        s.run("""
            MATCH (r:Repository {id: $repo})
            CREATE (c:Commit {
                id: $commit_id,
                repo: $repo,
                url: $url,
                author: $author,
                date: $date,
                title: $title,
                type: 'commit'
            })
            CREATE (rat:Rationale {
                text: $text,
                commit_id: $commit_id
            })
            CREATE (c)-[:BELONGS_TO]->(r)
            CREATE (c)-[:HAS_RATIONALE]->(rat)
        """, 
        repo=repo_id,
        commit_id=commit_id,
        url=f"https://github.com/{repo_id}/commit/{commit_id}",
        author="Zareen",
        date="2026-08-20T12:00:00Z",
        title="Switch to PostgreSQL",
        text="We decided to use PostgreSQL instead of MongoDB because PostgreSQL supports ACID compliance and complex relational queries."
        )
    driver.close()

    # 3. Write test sentence embedding to Chroma DB using local PersistentClient
    data_dir = Path(__file__).resolve().parent / "data" / "chroma_db"
    if not data_dir.exists():
        data_dir = Path(__file__).resolve().parent.parent / "data" / "chroma_db"
    
    chroma_client = chromadb.PersistentClient(path=str(data_dir))
    collection = chroma_client.get_or_create_collection(
        name="rationale_sentences",
        metadata={"hnsw:space": "cosine"}
    )
    
    model = SentenceTransformer("all-MiniLM-L6-v2")
    sentence = "We decided to use PostgreSQL instead of MongoDB because PostgreSQL supports ACID compliance and complex relational queries."
    vector = model.encode(sentence).tolist()
    
    collection.upsert(
        ids=[f"{repo_id.replace('/', '_')}_c1_0"],
        embeddings=[vector],
        metadatas=[{
            "commit_id": "5bd067d3dd8742deb9e280e7e3bb988b4379a81a",
            "type": "commit",
            "repo": repo_id,
            "url": f"https://github.com/{repo_id}/commit/5bd067d3dd8742deb9e280e7e3bb988b4379a81a"
        }],
        documents=[sentence]
    )
    print("Dummy rationale sentence successfully inserted into Neo4j and Chroma DB.")

    # 4. Invoke RAG Query
    query_payload = {
        "repoId": repo_id,
        "question": "why did you choose PostgreSQL over MongoDB?"
    }
    
    print("\n=== querying RAG engine ===")
    res = requests.post("http://127.0.0.1:8000/query", json=query_payload, timeout=25)
    print("Status Code:", res.status_code)
    if res.status_code == 200:
        data = res.json()
        print("\nAnswer:")
        print(data.get("answer"))
        print("\nCitations:")
        print(json.dumps(data.get("citations"), indent=2))
        
if __name__ == "__main__":
    run_e2e_rag_test()
