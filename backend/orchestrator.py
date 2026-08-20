"""Repository-scoped monitoring and investigation orchestration."""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from agent import run_scan
from brief import generate_weekly_brief
from health import compute_health

MONITOR_RUNS: dict[str, dict] = {}
MONITOR_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _raw_dir() -> Path:
    path = Path(__file__).resolve().parent.parent / "data" / "raw"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _issues_path(repo_id: str) -> Path:
    return _raw_dir() / f"{repo_id.split('/')[-1]}_issues.json"


def _run_path(repo_id: str) -> Path:
    return _raw_dir() / f"{repo_id.split('/')[-1]}_monitor_run.json"


def _set_step(repo_id: str, step: str, status: str, detail: str = "") -> None:
    with MONITOR_LOCK:
        run = MONITOR_RUNS.setdefault(repo_id, {"repoId": repo_id, "steps": []})
        for item in run["steps"]:
            if item["step"] == step:
                item.update({"status": status, "detail": detail})
                break
        else:
            run["steps"].append({"step": step, "status": status, "detail": detail})
        run["updatedAt"] = _now()


def get_monitor_run(repo_id: str) -> dict:
    with MONITOR_LOCK:
        current = MONITOR_RUNS.get(repo_id)
        if current:
            return json.loads(json.dumps(current))
    path = _run_path(repo_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"repoId": repo_id, "status": "not_started", "steps": []}


def run_monitoring(repo_id: str) -> None:
    run = {
        "repoId": repo_id,
        "status": "running",
        "startedAt": _now(),
        "updatedAt": _now(),
        "steps": [],
        "results": {},
    }
    with MONITOR_LOCK:
        MONITOR_RUNS[repo_id] = run

    try:
        from langgraph_orchestrator import run_langgraph_monitoring
        state = run_langgraph_monitoring(repo_id, on_step=lambda step, status, detail: _set_step(repo_id, step, status, detail))
        _set_step(repo_id, "triage_escalation", "done", "Escalation decisions recorded with evidence")
        with MONITOR_LOCK:
            MONITOR_RUNS[repo_id]["results"]["investigations"] = state.get("investigations", [])
            MONITOR_RUNS[repo_id]["results"]["health"] = state.get("health", {})
            MONITOR_RUNS[repo_id]["results"]["health_investigation"] = state.get("health_investigation", {})
            MONITOR_RUNS[repo_id]["results"]["brief"] = state.get("brief", {})
            MONITOR_RUNS[repo_id]["status"] = "done"
            MONITOR_RUNS[repo_id]["finishedAt"] = _now()
            MONITOR_RUNS[repo_id]["updatedAt"] = _now()
            snapshot = json.loads(json.dumps(MONITOR_RUNS[repo_id]))
        _run_path(repo_id).write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    except Exception as exc:
        with MONITOR_LOCK:
            MONITOR_RUNS[repo_id]["status"] = "error"
            MONITOR_RUNS[repo_id]["error"] = str(exc)
            MONITOR_RUNS[repo_id]["updatedAt"] = _now()
            snapshot = json.loads(json.dumps(MONITOR_RUNS[repo_id]))
        _run_path(repo_id).write_text(json.dumps(snapshot, indent=2), encoding="utf-8")


def start_monitoring(repo_id: str) -> bool:
    with MONITOR_LOCK:
        current = MONITOR_RUNS.get(repo_id, {})
        if current.get("status") == "running":
            return False
        MONITOR_RUNS[repo_id] = {"repoId": repo_id, "status": "queued", "steps": []}
    threading.Thread(target=run_monitoring, args=(repo_id,), daemon=True).start()
    return True
