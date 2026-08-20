import requests
import time

try:
    print("Sending ingestion request for 'octocat/Hello-World'...")
    res = requests.post("http://127.0.0.1:8000/repos/ingest", json={"repoUrl": "octocat/Hello-World"})
    print("POST STATUS:", res.status_code)
    print("POST RESPONSE:", res.json())

    repo_id = res.json().get("id")
    if repo_id:
        print(f"Polling status for {repo_id}...")
        for i in range(12):
            status_res = requests.get(f"http://127.0.0.1:8000/repos/ingest/status/{repo_id}")
            print(f"Poll #{i+1}:", status_res.json())
            if status_res.json().get("status") in ["done", "failed"] or "error" in status_res.json().get("status", ""):
                print("Finalized!")
                break
            time.sleep(1.5)
except Exception as e:
    print("Failed to run verification script:", e)
