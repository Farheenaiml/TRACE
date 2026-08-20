import requests
import json
import time

BASE_URL = "http://127.0.0.1:8000"

def verify():
    repo_id = "octocat/Hello-World"
    
    # 1. Verify RAG Query (which should use Chroma DB under the hood now)
    query_payload = {
        "repoId": repo_id,
        "question": "what is this repository about?"
    }
    
    print("=== Testing RAG Query ===")
    try:
        res = requests.post(f"{BASE_URL}/query", json=query_payload, timeout=20)
        print(f"Status Code: {res.status_code}")
        if res.status_code == 200:
            data = res.json()
            print("Answer:")
            print(data.get("answer"))
            print("Citations:")
            print(json.dumps(data.get("citations"), indent=2))
        else:
            print(res.text)
    except Exception as e:
        print(f"RAG query request failed: {e}")
        
    print()

    # 2. Verify Incremental Sync Ingestion Endpoint
    sync_payload = {
        "repoUrl": f"https://github.com/{repo_id}"
    }
    print("=== Testing Incremental Sync Ingestion ===")
    try:
        res = requests.post(f"{BASE_URL}/repos/sync", json=sync_payload, timeout=20)
        print(f"Status Code: {res.status_code}")
        if res.status_code == 200:
            print(json.dumps(res.json(), indent=2))
            
            # Poll status to see progress
            for _ in range(5):
                time.sleep(2)
                status_res = requests.get(f"{BASE_URL}/repos/ingest/status/{repo_id}")
                if status_res.status_code == 200:
                    status_data = status_res.json()
                    print(f"Status: {status_data.get('status')}")
                    if status_data.get('status') not in ['graphql', 'cloning', 'ast_parsing', 'commits', 'issues', 'indexing']:
                        break
        else:
            print(res.text)
    except Exception as e:
        print(f"Sync request failed: {e}")

if __name__ == "__main__":
    verify()
