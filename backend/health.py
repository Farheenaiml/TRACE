"""
Project Health Analysis module for TRACE.

Calculates repository health metrics:
- open_issue_count: count of currently open issues
- backlog_growth_rate: percentage change in open issues since previous snapshot
- duplicate_rate: percentage of scanned issues classified as duplicate by RepoGuardian agent
- active_contributor_count: distinct issue/commit authors in the last 30 days
- security_flag_count: count of security-sensitive issues flagged by RepoGuardian agent
- avg_response_time_hours: time-to-last-activity (updated_at - date) proxy calculation

NOTE ON METRIC ACCURACY / PROXIES:
The raw ingested GitHub dataset does not contain explicit maintainer first-response
timestamps. As an honest proxy, avg_response_time_hours measures "time to last activity"
(updated_at minus created date). This represents the overall activity window per issue
rather than first-response latency.
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path


def parse_iso_date(dt_str: str) -> datetime:
    """Parse ISO date strings safely."""
    if not dt_str:
        return datetime.now(timezone.utc)
    try:
        dt_str = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(dt_str)
    except Exception:
        return datetime.now(timezone.utc)


def compute_health(repo_id: str) -> dict:
    repo_slug = repo_id.split('/')[-1]
    raw_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    issues_path = raw_dir / f"{repo_slug}_issues.json"
    
    results_path = raw_dir / f"{repo_slug}_agent_results.json"
    if not results_path.exists():
        alt_path = raw_dir / f"{repo_slug}_issues_agent_results.json"
        if alt_path.exists():
            results_path = alt_path

    history_path = raw_dir / f"{repo_slug}_health_history.json"

    if not issues_path.exists():
        return {
            "repoId": repo_id,
            "health_status": "insufficient_data",
            "health_score": 0,
            "health_confidence": "low",
            "health_reasons": [{
                "metric": "issues_dataset",
                "value": 0,
                "impact": 0,
                "message": "No persisted issue activity is available yet; ingest repository issues to calculate health."
            }],
            "health_dashboard": {
                "issue_backlog": {"value": 0, "change_percent": 0.0},
                "pr_backlog": {"value": 0, "change_percent": 0.0},
                "response_time": {"value_hours": 0.0, "change_percent": 0.0},
                "duplicate_rate": {"value_percent": 0.0, "change_percent": 0.0},
                "active_contributors": {"value": 0, "change_percent": 0.0},
            },
            "projected_backlog_next_week": None,
            "projected_contributor_trend": None,
            "forecast_status": "insufficient_history",
            "open_issue_count": 0,
            "open_pr_count": 0,
            "backlog_growth_rate": 0.0,
            "pr_backlog_growth_rate": 0.0,
            "duplicate_rate": 0.0,
            "duplicate_rate_growth": 0.0,
            "active_contributor_count": 0,
            "contributor_activity_growth": 0.0,
            "security_flag_count": 0,
            "avg_response_time_hours": 0.0,
            "response_time_growth_rate": 0.0,
            "response_time_label": "Time to last activity (proxy for maintainer response time)",
            "history": []
        }

    issues_raw = json.loads(issues_path.read_text(encoding="utf-8"))
    issues = [i for i in issues_raw if isinstance(i, dict)]

    open_issues = [i for i in issues if i.get("state") == "open"]
    open_issue_count = len(open_issues)
    open_pr_count = len([i for i in open_issues if i.get("type") == "pull_request"])

    # 1. Proxy: Time to last activity (updated_at - date) in hours
    # PROXY METRIC: Maintainer first-response timestamp is not in the raw dataset.
    # We compute (updated_at - date) as an activity window proxy.
    time_diffs_hours = []
    for i in issues:
        c_date = parse_iso_date(i.get("date") or i.get("created_at"))
        u_date = parse_iso_date(i.get("updated_at") or i.get("date"))
        diff_seconds = max(0, (u_date - c_date).total_seconds())
        time_diffs_hours.append(diff_seconds / 3600.0)

    avg_response_time_hours = (
        sum(time_diffs_hours) / len(time_diffs_hours)
        if time_diffs_hours else 0.0
    )

    # 2. Active contributors in last 30 days based on issue dates
    now = datetime.now(timezone.utc)
    all_dates = [parse_iso_date(i.get("date")) for i in issues]
    latest_ref_date = max(all_dates) if all_dates else now
    thirty_days_ago = latest_ref_date - timedelta(days=30)

    active_authors = {
        i.get("author")
        for i in issues
        if i.get("author") and i.get("author") != "unknown"
        and parse_iso_date(i.get("date")) >= thirty_days_ago
    }
    active_contributor_count = len(active_authors)

    # 3. Agent results: duplicate rate & security flag count
    duplicate_rate = 0.0
    security_flag_count = 0
    if results_path.exists():
        try:
            agent_data = json.loads(results_path.read_text(encoding="utf-8"))
            results_list = agent_data.get("results", []) if isinstance(agent_data, dict) else agent_data
            if isinstance(results_list, list) and len(results_list) > 0:
                dup_count = sum(1 for r in results_list if r.get("decision") == "duplicate")
                duplicate_rate = (dup_count / len(results_list)) * 100.0
                security_flag_count = sum(1 for r in results_list if r.get("security_sensitive") is True)
        except Exception as e:
            print(f"Error reading agent results for health: {e}")

    # 4. History persistence & backlog growth rate
    history = []
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []

    current_snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "open_issue_count": open_issue_count,
        "open_pr_count": open_pr_count,
        "avg_response_time_hours": avg_response_time_hours,
        "duplicate_rate": duplicate_rate,
        "active_contributor_count": active_contributor_count
    }
    history.append(current_snapshot)
    
    try:
        history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"Error writing health history: {e}")

    backlog_growth_rate = 0.0
    pr_backlog_growth_rate = 0.0
    response_time_growth_rate = 0.0
    duplicate_rate_growth = 0.0
    contributor_activity_growth = 0.0
    if len(history) >= 2:
        previous = history[-2]

        def percent_change(current, previous_value):
            if previous_value and previous_value > 0:
                return ((current - previous_value) / previous_value) * 100.0
            return 100.0 if current > 0 else 0.0

        backlog_growth_rate = percent_change(open_issue_count, previous.get("open_issue_count", 0))
        pr_backlog_growth_rate = percent_change(open_pr_count, previous.get("open_pr_count", 0))
        response_time_growth_rate = percent_change(avg_response_time_hours, previous.get("avg_response_time_hours", 0))
        duplicate_rate_growth = percent_change(duplicate_rate, previous.get("duplicate_rate", 0))
        contributor_activity_growth = percent_change(active_contributor_count, previous.get("active_contributor_count", 0))

    # 5. Linear forecasting — requires at least 3 history snapshots.
    # Uses ordinary least-squares (no external ML deps) to project one week ahead.
    projected_backlog_next_week = None
    projected_contributor_trend = None
    forecast_status = "insufficient_history"

    # Gather recent history snapshots with parsed timestamps
    valid_snaps = []
    for snap in history:
        try:
            ts = parse_iso_date(snap.get("timestamp", ""))
            cnt = snap.get("open_issue_count")
            if isinstance(cnt, (int, float)):
                valid_snaps.append((ts, float(cnt)))
        except Exception:
            continue

    if len(valid_snaps) >= 3:
        # Express time as days since oldest snapshot
        t0 = valid_snaps[0][0]
        xs = [(snap[0] - t0).total_seconds() / 86400.0 for snap in valid_snaps]
        ys = [snap[1] for snap in valid_snaps]
        n = len(xs)
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        numerator = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
        denominator = sum((xs[i] - mean_x) ** 2 for i in range(n))

        if denominator != 0:
            slope = numerator / denominator
            intercept = mean_y - slope * mean_x
            x_next_week = xs[-1] + 7.0
            projected_backlog_next_week = max(0, round(slope * x_next_week + intercept))
            # Contributor trend: slope direction using open_issue_count as proxy
            # (positive slope = more issues = could mean declining maintainer bandwidth)
            if slope > 0.1:
                projected_contributor_trend = "declining"
            elif slope < -0.1:
                projected_contributor_trend = "growing"
            else:
                projected_contributor_trend = "stable"
            forecast_status = "ok"
        else:
            forecast_status = "insufficient_variance"

    # Explainable rule-based health score. The score is a risk score, not an
    # ML prediction: every point is backed by an observed metric or forecast.
    health_score = 0
    health_reasons = []

    if backlog_growth_rate >= 20:
        health_score += 30
        health_reasons.append({
            "metric": "backlog_growth_rate",
            "value": round(backlog_growth_rate, 1),
            "impact": 30,
            "message": "The open-issue backlog is growing rapidly."
        })
    elif backlog_growth_rate > 5:
        health_score += 15
        health_reasons.append({
            "metric": "backlog_growth_rate",
            "value": round(backlog_growth_rate, 1),
            "impact": 15,
            "message": "The open-issue backlog is increasing."
        })

    if duplicate_rate >= 30:
        health_score += 20
        health_reasons.append({
            "metric": "duplicate_rate",
            "value": round(duplicate_rate, 1),
            "impact": 20,
            "message": "A large share of investigated issues appear duplicated."
        })
    elif duplicate_rate >= 15:
        health_score += 10
        health_reasons.append({
            "metric": "duplicate_rate",
            "value": round(duplicate_rate, 1),
            "impact": 10,
            "message": "Duplicate issue noise is elevated."
        })

    if security_flag_count > 0:
        health_score += 25
        health_reasons.append({
            "metric": "security_flag_count",
            "value": security_flag_count,
            "impact": 25,
            "message": "Security-sensitive issues require maintainer attention."
        })

    if active_contributor_count == 0 and issues:
        health_score += 15
        health_reasons.append({
            "metric": "active_contributor_count",
            "value": active_contributor_count,
            "impact": 15,
            "message": "No active issue contributors were observed in the latest activity window."
        })
    elif projected_contributor_trend == "declining":
        health_score += 15
        health_reasons.append({
            "metric": "projected_contributor_trend",
            "value": projected_contributor_trend,
            "impact": 15,
            "message": "The backlog trend suggests contributor or maintainer activity may be declining."
        })

    if avg_response_time_hours >= 168:
        health_score += 10
        health_reasons.append({
            "metric": "avg_response_time_hours",
            "value": round(avg_response_time_hours, 1),
            "impact": 10,
            "message": "Issues show a long activity window before their latest update."
        })
    elif avg_response_time_hours >= 72:
        health_score += 5
        health_reasons.append({
            "metric": "avg_response_time_hours",
            "value": round(avg_response_time_hours, 1),
            "impact": 5,
            "message": "Issue activity windows are becoming extended."
        })

    if health_score >= 60:
        health_status = "deteriorating"
    elif health_score >= 30:
        health_status = "needs_attention"
    else:
        health_status = "healthy"

    health_confidence = "high" if len(history) >= 3 else ("medium" if len(history) >= 2 else "low")

    return {
        "repoId": repo_id,
        "health_status": health_status,
        "health_score": health_score,
        "health_confidence": health_confidence,
        "health_reasons": health_reasons,
        "open_issue_count": open_issue_count,
        "open_pr_count": open_pr_count,
        "backlog_growth_rate": round(backlog_growth_rate, 1),
        "pr_backlog_growth_rate": round(pr_backlog_growth_rate, 1),
        "duplicate_rate": round(duplicate_rate, 1),
        "duplicate_rate_growth": round(duplicate_rate_growth, 1),
        "active_contributor_count": active_contributor_count,
        "contributor_activity_growth": round(contributor_activity_growth, 1),
        "security_flag_count": security_flag_count,
        "avg_response_time_hours": round(avg_response_time_hours, 1),
        "response_time_growth_rate": round(response_time_growth_rate, 1),
        "response_time_label": "Time to last activity (proxy for maintainer response time)",
        "health_dashboard": {
            "issue_backlog": {"value": open_issue_count, "change_percent": round(backlog_growth_rate, 1)},
            "pr_backlog": {"value": open_pr_count, "change_percent": round(pr_backlog_growth_rate, 1)},
            "response_time": {"value_hours": round(avg_response_time_hours, 1), "change_percent": round(response_time_growth_rate, 1)},
            "duplicate_rate": {"value_percent": round(duplicate_rate, 1), "change_percent": round(duplicate_rate_growth, 1)},
            "active_contributors": {"value": active_contributor_count, "change_percent": round(contributor_activity_growth, 1)},
        },
        "projected_backlog_next_week": projected_backlog_next_week,
        "projected_contributor_trend": projected_contributor_trend,
        "forecast_status": forecast_status,
        "history": history
    }


def investigate_health_trend(repo_id: str) -> dict:
    """Explain why health is changing using observed metric trends."""
    health = compute_health(repo_id)
    dashboard = health.get("health_dashboard", {})
    findings = []

    issue_change = dashboard.get("issue_backlog", {}).get("change_percent", 0)
    pr_change = dashboard.get("pr_backlog", {}).get("change_percent", 0)
    response_change = dashboard.get("response_time", {}).get("change_percent", 0)
    duplicate_change = dashboard.get("duplicate_rate", {}).get("change_percent", 0)
    contributor_change = dashboard.get("active_contributors", {}).get("change_percent", 0)

    if issue_change > 0:
        findings.append(f"Issue backlog increased {round(issue_change, 1)}%.")
    if pr_change > 0:
        findings.append(f"Open PR backlog increased {round(pr_change, 1)}%.")
    if response_change > 0:
        findings.append(f"Response-time proxy increased {round(response_change, 1)}%.")
    if duplicate_change > 0:
        findings.append(f"Duplicate reports increased {round(duplicate_change, 1)}%.")
    if contributor_change < 0:
        findings.append(f"Active contributor activity decreased {abs(round(contributor_change, 1))}%.")
    if health.get("security_flag_count", 0) > 0:
        findings.append(f"{health['security_flag_count']} security-sensitive issue(s) were detected.")

    if not findings:
        conclusion = "No deteriorating health trend was identified from the available snapshots."
    elif health.get("health_status") == "deteriorating":
        conclusion = "Maintainer attention may be becoming a bottleneck because multiple repository pressure signals are increasing."
    else:
        conclusion = "The repository shows emerging pressure signals that should be monitored in the next snapshot."

    return {
        "repoId": repo_id,
        "status": health.get("health_status"),
        "findings": findings,
        "conclusion": conclusion,
        "evidence": health.get("health_dashboard", {}),
    }
