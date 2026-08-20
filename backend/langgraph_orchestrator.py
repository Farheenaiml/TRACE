"""LangGraph workflow for repository monitoring and investigation."""

import json
from pathlib import Path
from typing import Any, Callable, TypedDict

from agent import run_scan
from brief import generate_weekly_brief
from health import compute_health, investigate_health_trend


class MonitorState(TypedDict, total=False):
    repo_id: str
    issues: list[dict]
    investigations: list[dict]
    health: dict
    health_investigation: dict
    brief: dict
    subtask_summary: dict


def _issues_for(repo_id: str) -> list[dict]:
    path = Path(__file__).resolve().parent.parent / "data" / "raw" / f"{repo_id.split('/')[-1]}_issues.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [item for item in raw if item.get("type") == "issue"]


def _load_activity(state: MonitorState) -> dict[str, Any]:
    return {"issues": _issues_for(state["repo_id"])}


def _investigate(state: MonitorState) -> dict[str, Any]:
    return {"investigations": run_scan(state.get("issues", [])) if state.get("issues") else []}


def _subtask_summary(state: MonitorState, subtask: str) -> dict[str, Any]:
    investigations = state.get("investigations", [])
    if subtask == "duplicate_investigation":
        value = sum(bool(item.get("duplicates")) for item in investigations)
        detail = f"Checked {len(investigations)} issue(s); {value} had related issues"
    elif subtask == "importance_triage":
        values = [item.get("importance", {}).get("score", 0) for item in investigations]
        value = sum(score >= 20 for score in values)
        detail = f"Assessed importance for {len(investigations)} issue(s); {value} had meaningful risk"
    elif subtask == "missing_information":
        value = sum(item.get("follow_up", {}).get("needs_follow_up", False) for item in investigations)
        detail = f"Found {value} issue(s) missing reproduction or environment details"
    elif subtask == "security_check":
        value = sum(item.get("security_sensitive", False) for item in investigations)
        detail = f"Detected {value} security-sensitive issue(s)"
    else:
        value = sum(bool(item.get("investigation_timeline")) for item in investigations)
        detail = f"Historical repository investigation completed for {value} issue(s)"
    return {"subtask_summary": {**state.get("subtask_summary", {}), subtask: {"count": value, "detail": detail}}}


def _health(state: MonitorState) -> dict[str, Any]:
    return {
        "health": compute_health(state["repo_id"]),
        "health_investigation": investigate_health_trend(state["repo_id"]),
        "subtask_summary": {
            **state.get("subtask_summary", {}),
            "health_impact": {
                "status": compute_health(state["repo_id"]).get("health_status"),
                "detail": investigate_health_trend(state["repo_id"]).get("conclusion", ""),
            },
        },
    }


def _brief(state: MonitorState) -> dict[str, Any]:
    return {"brief": generate_weekly_brief(state["repo_id"])}


def run_langgraph_monitoring(repo_id: str, on_step: Callable[[str, str, str], None] | None = None) -> dict:
    """Run the monitoring DAG with LangGraph and return its final state."""
    from langgraph.graph import END, START, StateGraph

    def node(name: str, function):
        def wrapped(state: MonitorState):
            if on_step:
                on_step(name, "running", "")
            result = function(state)
            if on_step:
                detail = ""
                if name == "load_activity":
                    detail = f"Loaded {len(result.get('issues', []))} issues"
                elif name == "investigate":
                    detail = f"Investigated {len(result.get('investigations', []))} issues"
                elif name == "health_impact":
                    detail = result.get("subtask_summary", {}).get("health_impact", {}).get("detail", "")
                elif name in result.get("subtask_summary", {}):
                    detail = result["subtask_summary"][name].get("detail", "")
                on_step(name, "done", detail)
            return result
        return wrapped

    graph = StateGraph(MonitorState)
    graph.add_node("load_activity", node("load_activity", _load_activity))
    graph.add_node("investigate", node("investigate", _investigate))
    for subtask in ("duplicate_investigation", "importance_triage", "missing_information", "security_check", "historical_investigation"):
        graph.add_node(subtask, node(subtask, lambda state, current=subtask: _subtask_summary(state, current)))
    graph.add_node("health", node("health_impact", _health))
    graph.add_node("brief", node("weekly_brief", _brief))
    graph.add_edge(START, "load_activity")
    graph.add_edge("load_activity", "investigate")
    graph.add_edge("investigate", "duplicate_investigation")
    graph.add_edge("duplicate_investigation", "importance_triage")
    graph.add_edge("importance_triage", "missing_information")
    graph.add_edge("missing_information", "security_check")
    graph.add_edge("security_check", "historical_investigation")
    graph.add_edge("historical_investigation", "health")
    graph.add_edge("health", "brief")
    graph.add_edge("brief", END)
    return graph.compile().invoke({"repo_id": repo_id})
