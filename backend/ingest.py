"""
TRACE — Week 1 starter script.

Pulls the most recent commits from a GitHub repository and saves them
as normalized JSON. This is intentionally small: the goal this week is
just to prove the GitHub API connection works end to end, not to build
the full ingestion pipeline (that's Week 2).

Usage:
    python ingest.py <owner>/<repo> [--limit 20]

Example:
    python ingest.py torvalds/linux --limit 20
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # optional for public repos, but avoids low rate limits
API_BASE = "https://api.github.com"
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def fetch_commits(owner: str, repo: str, limit: int = 20) -> list[dict]:
    """Fetch the most recent `limit` commits for a repo."""
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    url = f"{API_BASE}/repos/{owner}/{repo}/commits"
    params = {"per_page": min(limit, 100)}

    response = requests.get(url, headers=headers, params=params, timeout=15)

    if response.status_code == 403:
        remaining = response.headers.get("X-RateLimit-Remaining")
        raise RuntimeError(
            f"GitHub API rate-limited (remaining={remaining}). "
            "Add a GITHUB_TOKEN to your .env file to raise the limit from 60/hr to 5,000/hr."
        )
    response.raise_for_status()

    raw_commits = response.json()

    # Normalize into the consistent shape the rest of TRACE will expect
    normalized = []
    for c in raw_commits[:limit]:
        normalized.append({
            "repo": f"{owner}/{repo}",
            "type": "commit",
            "id": c["sha"],
            "title": c["commit"]["message"].split("\n")[0],
            "body": c["commit"]["message"],
            "author": c["commit"]["author"]["name"],
            "date": c["commit"]["author"]["date"],
            "url": c["html_url"],
        })
    return normalized


def fetch_issues(owner: str, repo: str, limit: int = 50, state: str = "open") -> list[dict]:
    """
    Fetch issues for a repo (GitHub's issues endpoint also returns PRs —
    we tag and separate them, since RepoGuardian needs real issues only
    for triage, but PRs are useful context too).
    """
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    url = f"{API_BASE}/repos/{owner}/{repo}/issues"
    params = {"per_page": min(limit, 100), "state": state, "sort": "updated", "direction": "desc"}

    response = requests.get(url, headers=headers, params=params, timeout=15)

    if response.status_code == 403:
        remaining = response.headers.get("X-RateLimit-Remaining")
        raise RuntimeError(
            f"GitHub API rate-limited (remaining={remaining}). "
            "Add a GITHUB_TOKEN to your .env file to raise the limit from 60/hr to 5,000/hr."
        )
    response.raise_for_status()

    raw_items = response.json()

    normalized = []
    for item in raw_items[:limit]:
        is_pr = "pull_request" in item
        normalized.append({
            "repo": f"{owner}/{repo}",
            "type": "pull_request" if is_pr else "issue",
            "id": str(item["number"]),
            "title": item["title"],
            "body": item.get("body") or "",
            "author": item["user"]["login"] if item.get("user") else "unknown",
            "date": item["created_at"],
            "updated_at": item["updated_at"],
            "state": item["state"],
            "labels": [l["name"] for l in item.get("labels", [])],
            "comments": item.get("comments", 0),
            "url": item["html_url"],
        })
    return normalized


def main():
    parser = argparse.ArgumentParser(description="Pull recent commits and/or issues from a GitHub repo.")
    parser.add_argument("repo", help="Repository in owner/repo format, e.g. torvalds/linux")
    parser.add_argument("--limit", type=int, default=20, help="Number of items to pull")
    parser.add_argument("--issues", action="store_true", help="Fetch issues instead of commits")
    parser.add_argument("--state", default="open", choices=["open", "closed", "all"], help="Issue state filter (only used with --issues)")
    args = parser.parse_args()

    if "/" not in args.repo:
        sys.exit("Repo must be in owner/repo format, e.g. facebook/react")
    owner, repo = args.repo.split("/", 1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.issues:
        print(f"Fetching {args.state} issues/PRs from {owner}/{repo} ...")
        items = fetch_issues(owner, repo, args.limit, args.state)
        out_path = DATA_DIR / f"{repo}_issues.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(items)} issues/PRs -> {out_path}")
        if items:
            print("\nSample item:")
            print(json.dumps(items[0], indent=2))
        return

    print(f"Fetching last {args.limit} commits from {owner}/{repo} ...")
    commits = fetch_commits(owner, repo, args.limit)

    out_path = DATA_DIR / f"{repo}_commits.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(commits, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(commits)} commits -> {out_path}")
    print("\nSample commit:")
    print(json.dumps(commits[0], indent=2))


if __name__ == "__main__":
    main()
