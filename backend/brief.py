"""
Weekly brief generator for TRACE.

Pulls the latest health snapshot vs last week's, agent scan decision counts,
and the top 3 most-discussed open issues, then passes this digest to the LLM
(Groq -> Ollama -> deterministic fallback) asking for a 4-6 sentence plain-
English summary a maintainer could read in 30 seconds.
"""

import json
import os
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")


def _load_json(path: Path) -> Optional[list | dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def generate_weekly_brief(repo_id: str) -> dict:
    raw_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
    from repo_storage import data_path
    generated_at = datetime.now(timezone.utc).isoformat()

    # --- 1. Health history: this week vs last week ---
    history_path = data_path(repo_id, "health_history.json")
    history = _load_json(history_path) or []
    if not isinstance(history, list):
        history = []

    this_week_snap: dict = history[-1] if history else {}
    last_week_snap: dict = {}
    if len(history) >= 2:
        # Walk backwards to find a snapshot ~7 days old
        now_ts = datetime.now(timezone.utc)
        for snap in reversed(history[:-1]):
            try:
                snap_ts_str = snap.get("timestamp", "").replace("Z", "+00:00")
                snap_ts = datetime.fromisoformat(snap_ts_str)
                if (now_ts - snap_ts).days >= 5:
                    last_week_snap = snap
                    break
            except Exception:
                continue
        if not last_week_snap:
            last_week_snap = history[-2] if len(history) >= 2 else {}

    open_now = this_week_snap.get("open_issue_count", "N/A")
    open_prev = last_week_snap.get("open_issue_count", "N/A")

    # --- 2. Agent scan results: decisions by type ---
    results_path = data_path(repo_id, "agent_results.json")
    if not results_path.exists():
        results_path = data_path(repo_id, "issues_agent_results.json")

    agent_data = _load_json(results_path)
    results_list: list = []
    if isinstance(agent_data, dict):
        results_list = agent_data.get("results", [])
    elif isinstance(agent_data, list):
        results_list = agent_data

    decision_counts: dict[str, int] = {}
    for r in results_list:
        d = r.get("decision", "unknown")
        decision_counts[d] = decision_counts.get(d, 0) + 1

    # --- 3. Top 3 most-discussed open issues ---
    issues_path = data_path(repo_id, "issues.json")
    issues_raw = _load_json(issues_path) or []
    if not isinstance(issues_raw, list):
        issues_raw = []

    open_issues = [i for i in issues_raw if isinstance(i, dict) and i.get("state") == "open"]
    top_discussed = sorted(open_issues, key=lambda i: i.get("comments", 0), reverse=True)[:3]
    top_discussed_summaries = [
        {"title": i.get("title", "Untitled"), "comments": i.get("comments", 0), "url": i.get("url", "#")}
        for i in top_discussed
    ]

    # --- 4. Compose raw_stats summary ---
    raw_stats = {
        "open_issue_count_this_week": open_now,
        "open_issue_count_last_week": open_prev,
        "decision_counts": decision_counts,
        "top_discussed_issues": top_discussed_summaries,
    }

    # --- 5. LLM prompt ---
    decision_str = ", ".join(f"{v} {k}" for k, v in decision_counts.items()) if decision_counts else "no data"
    top_discussed_str = "\n".join(
        f"  - \"{t['title']}\" ({t['comments']} comments)"
        for t in top_discussed_summaries
    ) or "  - No open issues found."

    prev_count_str = f"{open_prev}" if open_prev != "N/A" else "unknown (first snapshot)"
    prompt_user = (
        f"Repository: {repo_id}\n\n"
        f"Open issues this week: {open_now}  (last week: {prev_count_str})\n"
        f"Agent triage decisions from latest scan: {decision_str}\n\n"
        f"Top 3 most-discussed open issues:\n{top_discussed_str}\n\n"
        "Write a concise 4-6 sentence plain-English brief that a maintainer could read in 30 seconds. "
        "Mention the backlog change, what the triage scan found, and highlight any hot issues. "
        "Speak directly to the maintainer. Do not use bullet points, headers, or Markdown — prose only. "
        "Respond with a JSON object with a single key 'summary_text' containing the brief."
    )
    system_prompt = (
        "You are TRACE, an AI decision intelligence engine that summarizes repository health for maintainers. "
        "Be factual, brief, and actionable. Do not invent data not present in the input."
    )

    # --- 6. LLM calls: Groq -> Ollama -> deterministic fallback ---
    summary_text: Optional[str] = None

    if GROQ_API_KEY:
        try:
            res = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt_user},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.3,
                },
                timeout=15,
            )
            res.raise_for_status()
            content = json.loads(res.json()["choices"][0]["message"]["content"])
            summary_text = content.get("summary_text")
        except Exception as e:
            print(f"Groq brief generation failed, trying Ollama: {e}")

    if summary_text is None:
        try:
            res = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": "llama3.2",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt_user},
                    ],
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0.3},
                },
                timeout=20,
            )
            res.raise_for_status()
            content = json.loads(res.json()["message"]["content"])
            summary_text = content.get("summary_text")
        except Exception as e:
            print(f"Ollama brief generation failed or unavailable: {e}")

    # Deterministic fallback
    if not summary_text:
        delta = ""
        if open_now != "N/A" and open_prev != "N/A":
            try:
                diff = int(open_now) - int(open_prev)
                delta = f" — a change of {'+' if diff >= 0 else ''}{diff} from last week"
            except Exception:
                pass
        top_title = top_discussed_summaries[0]["title"] if top_discussed_summaries else "no issues recorded"
        escalate_count = decision_counts.get("escalate", 0)
        summary_text = (
            f"This week, {repo_id} has {open_now} open issues{delta}. "
            f"The latest RepoGuardian scan reviewed {len(results_list)} issues and flagged {escalate_count} for immediate escalation. "
            f"The most actively discussed open issue is \"{top_title}\". "
            f"Triage breakdown: {decision_str}. "
            "Review the escalated items and consider closing any confirmed duplicates to reduce noise in the backlog."
        )

    return {
        "generated_at": generated_at,
        "summary_text": summary_text,
        "raw_stats": raw_stats,
    }
