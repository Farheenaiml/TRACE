"""
TRACE — Expanded ingestion pipeline.
Pulls metadata, commits, pull requests, issues, discussions, source files, and documentation.
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
API_BASE = "https://api.github.com"
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def get_github_headers(graphql: bool = False) -> dict:
    headers = {
        "Accept": "application/json" if graphql else "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def github_request(url: str, headers: dict, method: str = "GET", params: dict = None, json_data: dict = None, timeout: int = 15) -> requests.Response:
    """
    A rate-limit-aware request wrapper for the GitHub REST and GraphQL APIs.
    Handles network errors, primary rate limits, and secondary rate limits with exponential backoff.
    """
    backoff = 2
    while True:
        try:
            if method == "GET":
                response = requests.get(url, headers=headers, params=params, timeout=timeout)
            elif method == "POST":
                response = requests.post(url, headers=headers, json=json_data, timeout=timeout)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
        except requests.exceptions.RequestException as e:
            print(f"[ingest] Network error: {e}. Retrying in {backoff} seconds...")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue

        remaining = response.headers.get("X-RateLimit-Remaining")
        reset_time = response.headers.get("X-RateLimit-Reset")

        # Primary rate limit check
        if response.status_code == 403 and remaining == "0":
            if reset_time:
                sleep_time = max(float(reset_time) - time.time(), 0) + 2
                print(f"[ingest] Primary rate limit hit. Sleeping for {sleep_time:.1f} seconds until reset...")
                time.sleep(sleep_time)
                continue

        # Secondary rate limit check (typically 403 or 429)
        if response.status_code in (403, 429):
            body = response.text.lower()
            if "secondary rate limit" in body or "abuse" in body or response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    sleep_time = float(retry_after) + 2
                    print(f"[ingest] Secondary rate limit hit (Retry-After). Sleeping for {sleep_time:.1f} seconds...")
                else:
                    sleep_time = backoff
                    print(f"[ingest] Secondary rate limit hit. Sleeping for {sleep_time:.1f} seconds...")
                    backoff = min(backoff * 2, 60)
                time.sleep(sleep_time)
                continue

        if response.status_code == 403:
            try:
                msg = response.json().get("message", "")
            except Exception:
                msg = response.text
            raise RuntimeError(f"GitHub API returned 403: {msg}")

        response.raise_for_status()
        return response


def fetch_repo_metadata(owner: str, repo: str) -> dict:
    """Fetch repository metadata and save to data/raw/{repo}_metadata.json."""
    print(f"[ingest] Fetching repository metadata for {owner}/{repo} ...")
    headers = get_github_headers()
    url = f"{API_BASE}/repos/{owner}/{repo}"
    
    response = github_request(url, headers)
    data = response.json()
    
    license_data = data.get("license")
    license_name = None
    if license_data:
        license_name = license_data.get("spdx_id") or license_data.get("name")
        
    metadata = {
        "name": data["name"],
        "description": data.get("description"),
        "owner": {
            "login": data["owner"]["login"],
            "type": data["owner"]["type"]
        },
        "stargazers_count": data.get("stargazers_count", 0),
        "forks_count": data.get("forks_count", 0),
        "primary_language": data.get("language"),
        "topics": data.get("topics", []),
        "license": license_name,
        "default_branch": data.get("default_branch", "main")
    }
    
    out_path = DATA_DIR / f"{repo}_metadata.json"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
        
    print(f"[ingest] Saved repository metadata to {out_path}")
    return metadata


def fetch_commits(owner: str, repo: str, limit: int = 20) -> list[dict]:
    """Fetch recent commits with file diffs and save them."""
    print(f"[ingest] Fetching last {limit} commits from {owner}/{repo} ...")
    headers = get_github_headers()
    
    commits_list = []
    page = 1
    per_page = min(limit, 100)
    
    while len(commits_list) < limit:
        url = f"{API_BASE}/repos/{owner}/{repo}/commits"
        params = {"page": page, "per_page": per_page}
        response = github_request(url, headers, params=params)
        page_commits = response.json()
        if not page_commits:
            break
        commits_list.extend(page_commits)
        if len(page_commits) < per_page:
            break
        page += 1
        
    commits_list = commits_list[:limit]
    total_commits = len(commits_list)
    print(f"[ingest] Found {total_commits} commits. Fetching detailed file diffs for each...")
    
    normalized_commits = []
    for idx, c in enumerate(commits_list):
        sha = c["sha"]
        print(f"[ingest] fetched {idx + 1}/{total_commits} commits: {sha[:7]} ...")
        
        detail_url = f"{API_BASE}/repos/{owner}/{repo}/commits/{sha}"
        detail_response = github_request(detail_url, headers)
        detail_data = detail_response.json()
        
        parents = [p["sha"] for p in detail_data.get("parents", [])]
        
        author_login = None
        if detail_data.get("author"):
            author_login = detail_data["author"].get("login")
        if not author_login:
            raw_author = detail_data["commit"].get("author")
            if raw_author:
                author_login = f"{raw_author.get('name')} <{raw_author.get('email')}>"
            else:
                author_login = "unknown"
                
        files_data = detail_data.get("files", [])
        files = []
        diff_truncated = len(files_data) >= 300
        
        for f in files_data:
            patch = f.get("patch")
            if patch is None:
                filename = f.get("filename", "")
                binary_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.zip', '.tar', '.gz', '.db', '.sqlite', '.exe', '.dll', '.so', '.dylib', '.class', '.jar', '.war', '.svg')
                if not filename.lower().endswith(binary_extensions) and f.get("status") != "removed":
                    diff_truncated = True
            
            files.append({
                "filename": f.get("filename", ""),
                "status": f.get("status", ""),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "patch": patch
            })
            
        normalized_commits.append({
            "repo": f"{owner}/{repo}",
            "type": "commit",
            "id": sha,
            "title": detail_data["commit"]["message"].split("\n")[0],
            "body": detail_data["commit"]["message"],
            "author": author_login,
            "date": detail_data["commit"]["author"]["date"],
            "url": detail_data.get("html_url", ""),
            "parents": parents,
            "files": files,
            "diff_truncated": diff_truncated
        })
        
    return normalized_commits


def fetch_pull_requests(owner: str, repo: str, limit: int = 50) -> list[dict]:
    """Fetch recent pull requests with reviews, comments, and files."""
    print(f"[ingest] Fetching pull requests from {owner}/{repo} (limit: {limit}) ...")
    headers = get_github_headers()
    
    pulls_list = []
    page = 1
    per_page = min(limit, 100)
    
    while len(pulls_list) < limit:
        url = f"{API_BASE}/repos/{owner}/{repo}/pulls"
        params = {"state": "all", "page": page, "per_page": per_page}
        response = github_request(url, headers, params=params)
        page_pulls = response.json()
        if not page_pulls:
            break
        pulls_list.extend(page_pulls)
        if len(page_pulls) < per_page:
            break
        page += 1
        
    pulls_list = pulls_list[:limit]
    total_prs = len(pulls_list)
    print(f"[ingest] Found {total_prs} PRs. Fetching details for each...")
    
    normalized_prs = []
    for idx, pr in enumerate(pulls_list):
        num = pr["number"]
        print(f"[ingest] fetched {idx + 1}/{total_prs} PR #{num} ...")
        
        detail_url = f"{API_BASE}/repos/{owner}/{repo}/pulls/{num}"
        detail_response = github_request(detail_url, headers)
        detail_data = detail_response.json()
        
        # Pull request reviews
        reviews_url = f"{API_BASE}/repos/{owner}/{repo}/pulls/{num}/reviews"
        reviews_response = github_request(reviews_url, headers)
        reviews_data = reviews_response.json()
        reviews = []
        for r in reviews_data:
            reviewer = r.get("user", {}).get("login") if r.get("user") else "unknown"
            reviews.append({
                "reviewer": reviewer,
                "state": r.get("state", ""),
                "body": r.get("body") or ""
            })
            
        # Regular PR conversation comments (treated as issue comments)
        comments_url = f"{API_BASE}/repos/{owner}/{repo}/issues/{num}/comments"
        comments_response = github_request(comments_url, headers)
        comments_data = comments_response.json()
        comments = []
        for c in comments_data:
            author = c.get("user", {}).get("login") if c.get("user") else "unknown"
            comments.append({
                "author": author,
                "body": c.get("body") or "",
                "date": c.get("created_at", "")
            })
            
        # Files list
        files_url = f"{API_BASE}/repos/{owner}/{repo}/pulls/{num}/files"
        files_response = github_request(files_url, headers)
        files_data = files_response.json()
        changed_files = [f.get("filename") for f in files_data if f.get("filename")]
        
        requested_reviewers = [r.get("login") for r in detail_data.get("requested_reviewers", []) if r.get("login")]
        labels = [l.get("name") for l in detail_data.get("labels", [])]
        author_login = detail_data.get("user", {}).get("login") if detail_data.get("user") else "unknown"
        
        normalized_prs.append({
            "repo": f"{owner}/{repo}",
            "type": "pull_request",
            "id": str(num),
            "title": detail_data.get("title", ""),
            "body": detail_data.get("body") or "",
            "author": author_login,
            "date": detail_data.get("created_at", ""),
            "updated_at": detail_data.get("updated_at", ""),
            "state": detail_data.get("state", ""),
            "labels": labels,
            "url": detail_data.get("html_url", ""),
            "merged_at": detail_data.get("merged_at"),
            "merge_commit_sha": detail_data.get("merge_commit_sha"),
            "requested_reviewers": requested_reviewers,
            "reviews": reviews,
            "comments": comments,
            "changed_files": changed_files
        })
        
    return normalized_prs


def resolve_links_from_timeline(owner: str, repo: str, number: int) -> tuple[list[dict], list[dict]]:
    """Query timeline events and find linked PRs and commits."""
    headers = get_github_headers()
    headers["Accept"] = "application/vnd.github.mockingbird-preview+json"
    url = f"{API_BASE}/repos/{owner}/{repo}/issues/{number}/timeline"
    
    response = github_request(url, headers)
    events = response.json()
    
    linked_prs = []
    linked_commits = []
    
    for ev in events:
        event_type = ev.get("event")
        
        if event_type == "referenced" and ev.get("commit_id"):
            sha = ev["commit_id"]
            if not any(item["id"] == sha for item in linked_commits):
                linked_commits.append({
                    "id": sha,
                    "link_source": "timeline"
                })
                
        if event_type == "cross-referenced" and ev.get("source", {}).get("type") == "issue":
            source_issue = ev["source"].get("issue", {})
            if "pull_request" in source_issue:
                pr_num = str(source_issue.get("number"))
                if pr_num and not any(item["id"] == pr_num for item in linked_prs):
                    linked_prs.append({
                        "id": pr_num,
                        "link_source": "timeline"
                    })
                    
    return linked_prs, linked_commits


def fetch_issues(owner: str, repo: str, all_commits: list, all_prs: list, limit: int = 50) -> list[dict]:
    """Fetch expanded issues resolving milestones and linked items."""
    print(f"[ingest] Fetching issues from {owner}/{repo} (limit: {limit}) ...")
    headers = get_github_headers()
    
    issues_list = []
    page = 1
    per_page = 100
    
    while len(issues_list) < limit:
        url = f"{API_BASE}/repos/{owner}/{repo}/issues"
        params = {"state": "all", "sort": "updated", "direction": "desc", "page": page, "per_page": per_page}
        response = github_request(url, headers, params=params)
        page_issues = response.json()
        if not page_issues:
            break
            
        for item in page_issues:
            if "pull_request" in item:
                continue
            issues_list.append(item)
            if len(issues_list) >= limit:
                break
                
        if len(page_issues) < per_page:
            break
        page += 1
        
    issues_list = issues_list[:limit]
    total_issues = len(issues_list)
    print(f"[ingest] Found {total_issues} issues. Resolving details, milestones, and links...")
    
    normalized_issues = []
    for idx, item in enumerate(issues_list):
        num = item["number"]
        print(f"[ingest] fetched {idx + 1}/{total_issues} Issue #{num} ...")
        
        opened_by = item.get("user", {}).get("login") if item.get("user") else "unknown"
        
        closed_by = None
        if item.get("state") == "closed":
            detail_url = f"{API_BASE}/repos/{owner}/{repo}/issues/{num}"
            detail_response = github_request(detail_url, headers)
            detail_data = detail_response.json()
            if detail_data.get("closed_by"):
                closed_by = detail_data["closed_by"].get("login")
                
        milestone = None
        milestone_data = item.get("milestone")
        if milestone_data:
            milestone = {
                "title": milestone_data.get("title", ""),
                "due_on": milestone_data.get("due_on")
            }
            
        # Resolve links via timeline API
        try:
            linked_prs, linked_commits = resolve_links_from_timeline(owner, repo, num)
        except Exception as e:
            print(f"[ingest] Error fetching timeline for issue #{num}: {e}. Surfacing error as required.")
            raise
            
        # Text pattern matching fallbacks
        pr_pattern = re.compile(rf"\b(closes|fixes|resolves)\s+#{num}\b", re.IGNORECASE)
        for pr in all_prs:
            pr_body = pr.get("body") or ""
            pr_num = str(pr.get("id"))
            if pr_pattern.search(pr_body):
                if not any(item["id"] == pr_num for item in linked_prs):
                    linked_prs.append({
                        "id": pr_num,
                        "link_source": "text_pattern"
                    })
                    
        commit_pattern = re.compile(rf"#{num}\b")
        for commit in all_commits:
            commit_msg = commit.get("body") or ""
            commit_sha = commit.get("id")
            if commit_pattern.search(commit_msg):
                if not any(item["id"] == commit_sha for item in linked_commits):
                    linked_commits.append({
                        "id": commit_sha,
                        "link_source": "text_pattern"
                    })
                    
        labels = [l.get("name") for l in item.get("labels", [])]
        
        normalized_issues.append({
            "repo": f"{owner}/{repo}",
            "type": "issue",
            "id": str(num),
            "title": item.get("title", ""),
            "body": item.get("body") or "",
            "author": opened_by,
            "opened_by": opened_by,
            "closed_by": closed_by,
            "state": item.get("state", ""),
            "date": item.get("created_at", ""),
            "updated_at": item.get("updated_at", ""),
            "labels": labels,
            "comments_count": item.get("comments", 0),
            "url": item.get("html_url", ""),
            "milestone": milestone,
            "linked_prs": linked_prs,
            "linked_commits": linked_commits
        })
        
    return normalized_issues


def fetch_discussions(owner: str, repo: str) -> list[dict]:
    """Fetch GitHub Discussions via GraphQL."""
    if not GITHUB_TOKEN:
        print("[ingest] GITHUB_TOKEN not set. Discussions require authentication. Skipping discussions gracefully.")
        return []

    print(f"[ingest] Fetching GitHub Discussions for {owner}/{repo} via GraphQL...")
    headers = get_github_headers(graphql=True)
    
    query = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        discussions(first: 50) {
          nodes {
            id
            number
            title
            body
            url
            createdAt
            author {
              login
            }
            comments(first: 100) {
              nodes {
                author {
                  login
                }
                body
                createdAt
              }
            }
          }
        }
      }
    }
    """
    
    url = "https://api.github.com/graphql"
    json_data = {"query": query, "variables": {"owner": owner, "name": repo}}
    
    try:
        response = github_request(url, headers, method="POST", json_data=json_data)
        res_json = response.json()
        
        # Check for GraphQL errors
        if "errors" in res_json:
            err_msgs = [e.get("message", "") for e in res_json["errors"]]
            discussions_disabled = any("discussion" in m.lower() or "disabled" in m.lower() for m in err_msgs)
            if discussions_disabled:
                print(f"[ingest] Discussions appear to be disabled or unsupported for {owner}/{repo}. Skipping.")
                return []
            else:
                print(f"[ingest] GraphQL error(s): {err_msgs}")
                return []
                
        repo_data = res_json.get("data", {}).get("repository")
        if not repo_data or not repo_data.get("discussions"):
            print(f"[ingest] No discussions data found for {owner}/{repo}.")
            return []
            
        nodes = repo_data["discussions"].get("nodes", [])
        normalized_discussions = []
        
        for node in nodes:
            if not node:
                continue
            author_login = node.get("author", {}).get("login") if node.get("author") else "unknown"
            
            replies = []
            participants = set()
            if author_login and author_login != "unknown":
                participants.add(author_login)
                
            comments_nodes = node.get("comments", {}).get("nodes", []) if node.get("comments") else []
            for comment in comments_nodes:
                if not comment:
                    continue
                comment_author = comment.get("author", {}).get("login") if comment.get("author") else "unknown"
                replies.append({
                    "author": comment_author,
                    "body": comment.get("body") or "",
                    "created_at": comment.get("createdAt") or ""
                })
                if comment_author and comment_author != "unknown":
                    participants.add(comment_author)
                    
            normalized_discussions.append({
                "repo": f"{owner}/{repo}",
                "type": "discussion",
                "id": str(node.get("number") or node.get("id")),
                "title": node.get("title", ""),
                "body": node.get("body") or "",
                "author": author_login,
                "date": node.get("createdAt") or "",
                "url": node.get("url", ""),
                "replies": replies,
                "participants": list(participants)
            })
            
        return normalized_discussions
        
    except Exception as e:
        print(f"[ingest] Failed to fetch discussions: {e}. Skipping discussions gracefully.")
        return []


def is_excluded_path(path: str) -> bool:
    """Helper to check if a source file path is in the exclusion list."""
    parts = path.lower().split('/')
    excluded_dirs = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'build', 'dist', 'bin', 'obj', '.idea', '.vscode'}
    if any(p in excluded_dirs for p in parts):
        return True
    lockfiles = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock', 'gemfile.lock', 'cargo.lock', 'composer.lock'}
    if parts[-1] in lockfiles:
        return True
    binary_extensions = (
        '.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.zip', '.tar', '.gz',
        '.db', '.sqlite', '.exe', '.dll', '.so', '.dylib', '.class', '.jar', '.war',
        '.svg', '.mp3', '.mp4', '.wav', '.woff', '.woff2', '.ttf', '.eot'
    )
    if parts[-1].endswith(binary_extensions):
        return True
    return False


def parse_python_structure(content: str) -> tuple[list[str], list[str], list[str]]:
    """Use Python's built-in AST module to parse imports, classes, and functions."""
    import ast
    imports = []
    functions = []
    classes = []
    try:
        tree = ast.parse(content)
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    imports.append(n.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for n in node.names:
                    imports.append(f"{module}.{n.name}" if module else n.name)
            elif isinstance(node, ast.FunctionDef):
                functions.append(node.name)
            elif isinstance(node, ast.ClassDef):
                classes.append(node.name)
    except Exception:
        # Fallback to regex if parsing fails (e.g. invalid syntax)
        return parse_regex_structure(content)
    return imports[:30], functions[:30], classes[:30]


def parse_regex_structure(content: str) -> tuple[list[str], list[str], list[str]]:
    """Use regex patterns to parse imports, classes, and functions for non-Python languages (like JS, TS, etc.)."""
    imports = []
    functions = []
    classes = []
    
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            continue
            
        # Match imports
        if re.search(r'\b(?:import|require)\b', line):
            imports.append(line)
            
        # Match functions
        func_match = re.search(r'\bfunction\s+([a-zA-Z0-9_$]+)', line)
        if func_match:
            functions.append(func_match.group(1))
        else:
            arrow_match = re.search(r'\b(?:const|let|var)\s+([a-zA-Z0-9_$]+)\s*=\s*(?:\([^)]*\)|[a-zA-Z0-9_$]+)\s*=>', line)
            if arrow_match:
                functions.append(arrow_match.group(1))
                
        # Match classes
        class_match = re.search(r'\bclass\s+([a-zA-Z0-9_$]+)', line)
        if class_match:
            classes.append(class_match.group(1))
            
    return imports[:20], functions[:20], classes[:20]


def fetch_source_files(owner: str, repo: str, default_branch: str) -> list[dict]:
    """
    Fetch structural summary of source code files using GitHub Trees API.
    Only processes files < 100 KB and skips binaries, lockfiles, node_modules, etc.
    """
    print(f"[ingest] Fetching Git Tree recursively for {owner}/{repo} (branch: {default_branch}) ...")
    headers = get_github_headers()
    url = f"{API_BASE}/repos/{owner}/{repo}/git/trees/{default_branch}"
    params = {"recursive": "1"}
    
    response = github_request(url, headers, params=params)
    tree_data = response.json()
    if tree_data.get("truncated"):
        print("[ingest] WARNING: Git Tree response is truncated due to repository size.")
        
    tree = tree_data.get("tree", [])
    source_files = []
    
    # Process only blobs under 100 KB and exclude unwanted folders/files
    filtered_blobs = []
    for item in tree:
        if item.get("type") == "blob":
            path = item.get("path", "")
            size = item.get("size", 0)
            if size <= 100000 and not is_excluded_path(path):
                filtered_blobs.append(item)
                
    total_blobs = len(filtered_blobs)
    print(f"[ingest] Found {total_blobs} source files to fetch and parse structural summaries for...")
    
    # Limit source code files ingestion to avoid hitting rate limits or taking too long
    # We will fetch up to 30 files for a typical ingestion run (can be adjusted)
    max_files = 30
    if total_blobs > max_files:
        print(f"[ingest] Ingestion limit: capping structural file analysis at {max_files} files.")
        filtered_blobs = filtered_blobs[:max_files]
        total_blobs = max_files

    for idx, item in enumerate(filtered_blobs):
        path = item["path"]
        sha = item["sha"]
        print(f"[ingest] fetching source file {idx + 1}/{total_blobs}: {path} ...")
        
        blob_url = f"{API_BASE}/repos/{owner}/{repo}/git/blobs/{sha}"
        blob_response = github_request(blob_url, headers)
        blob_data = blob_response.json()
        
        content = ""
        if blob_data.get("encoding") == "base64":
            try:
                content = base64.b64decode(blob_data["content"]).decode("utf-8", errors="replace")
            except Exception as e:
                print(f"[ingest] Error decoding base64 content for {path}: {e}")
                
        # Parse structural details based on extension
        # Comment: Python files undergo AST parsing using the python ast module.
        # Other source files (JS, TS, C, Java, Go, etc.) undergo regex-based parsing fallback.
        ext = Path(path).suffix.lower()
        if ext == ".py":
            imports, functions, classes = parse_python_structure(content)
        else:
            imports, functions, classes = parse_regex_structure(content)
            
        summary_body = (
            f"File: {path}\n"
            f"Folder: {str(Path(path).parent).replace('\\', '/')}\n"
            f"Extension: {ext}\n"
            f"Imports/Dependencies: {', '.join(imports) if imports else 'None'}\n"
            f"Top-level Functions: {', '.join(functions) if functions else 'None'}\n"
            f"Top-level Classes: {', '.join(classes) if classes else 'None'}"
        )
        
        source_files.append({
            "repo": f"{owner}/{repo}",
            "type": "source_file",
            "id": path,
            "title": path,
            "body": summary_body,
            "author": "system",
            "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "url": f"https://github.com/{owner}/{repo}/blob/{default_branch}/{path}",
            "folder_path": str(Path(path).parent).replace('\\', '/'),
            "extension": ext,
            "imports": imports,
            "functions": functions,
            "classes": classes
        })
        
    return source_files


def classify_doc(path: str) -> str:
    """Classify document file by rough type based on filename/path patterns."""
    path_lower = path.lower()
    filename = Path(path).name.lower()
    if "readme" in filename:
        return "readme"
    if any(k in path_lower for k in ("architecture", "design", "adr", "decision")):
        return "architecture"
    if any(k in path_lower for k in ("docs/api", "api-docs", "api/")) or "api" in filename:
        return "api_docs"
    return "other_markdown"


def fetch_wiki_docs(owner: str, repo: str) -> list[dict]:
    """Clone GitHub Wiki shallow via HTTPS and return parsed markdown files."""
    wiki_docs = []
    wiki_url = f"https://github.com/{owner}/{repo}.wiki.git"
    temp_dir = tempfile.mkdtemp()
    
    try:
        print(f"[ingest] Attempting shallow clone of Wiki: {wiki_url} ...")
        # Run git clone with a timeout of 15 seconds
        res = subprocess.run(
            ["git", "clone", "--depth", "1", wiki_url, temp_dir],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15
        )
        
        if res.returncode == 0:
            print("[ingest] Wiki cloned successfully. Reading markdown files...")
            wiki_path = Path(temp_dir)
            for md_file in wiki_path.rglob("*.md"):
                rel_path = md_file.relative_to(wiki_path)
                try:
                    content = md_file.read_text(encoding="utf-8", errors="replace")
                except Exception as e:
                    print(f"[ingest] Error reading wiki file {rel_path}: {e}")
                    continue
                    
                wiki_docs.append({
                    "repo": f"{owner}/{repo}",
                    "type": "documentation",
                    "id": f"wiki/{rel_path.as_posix()}",
                    "title": md_file.stem.replace('-', ' '),
                    "body": content,
                    "author": "system",
                    "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "url": f"https://github.com/{owner}/{repo}/wiki/{md_file.stem}",
                    "doc_type": classify_doc(rel_path.as_posix())
                })
        else:
            print(f"[ingest] Wiki clone failed (wiki probably disabled or private). Reason: {res.stderr.strip()}")
    except Exception as e:
        print(f"[ingest] Error cloning Wiki: {e}. Skipping Wiki gracefully.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        
    return wiki_docs


def fetch_documentation(owner: str, repo: str, default_branch: str) -> list[dict]:
    """Fetch README, docs/ directory markdown files, and shallow-clone Wiki."""
    print(f"[ingest] Fetching documentation for {owner}/{repo} ...")
    headers = get_github_headers()
    docs = []
    
    # 1. Root README
    readme_url = f"{API_BASE}/repos/{owner}/{repo}/readme"
    try:
        response = github_request(readme_url, headers)
        readme_data = response.json()
        
        content = ""
        if readme_data.get("encoding") == "base64":
            try:
                content = base64.b64decode(readme_data["content"]).decode("utf-8", errors="replace")
            except Exception as e:
                print(f"[ingest] Error decoding README base64: {e}")
                
        docs.append({
            "repo": f"{owner}/{repo}",
            "type": "documentation",
            "id": readme_data.get("path", "README.md"),
            "title": readme_data.get("name", "README.md"),
            "body": content,
            "author": "system",
            "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "url": readme_data.get("html_url", ""),
            "doc_type": "readme"
        })
    except Exception as e:
        print(f"[ingest] Could not fetch root README: {e}")
        
    # 2. Markdown files inside docs/ folder via Git Trees API
    try:
        tree_url = f"{API_BASE}/repos/{owner}/{repo}/git/trees/{default_branch}"
        tree_response = github_request(tree_url, headers, params={"recursive": "1"})
        tree_data = tree_response.json()
        
        tree = tree_data.get("tree", [])
        doc_blobs = []
        for item in tree:
            if item.get("type") == "blob":
                path = item.get("path", "")
                if (path.lower().startswith("docs/") or path.lower().endswith((".md", ".rst"))) and not is_excluded_path(path):
                    # Skip root readme as we fetched it already
                    if Path(path).name.lower().startswith("readme"):
                        continue
                    doc_blobs.append(item)
                    
        print(f"[ingest] Found {len(doc_blobs)} documentation files in Git tree. Fetching...")
        
        # Cap doc files to avoid rate limits
        max_docs = 20
        if len(doc_blobs) > max_docs:
            print(f"[ingest] Capping Git tree documentation files fetching at {max_docs} files.")
            doc_blobs = doc_blobs[:max_docs]
            
        for idx, item in enumerate(doc_blobs):
            path = item["path"]
            sha = item["sha"]
            print(f"[ingest] fetching doc {idx + 1}/{len(doc_blobs)}: {path} ...")
            
            blob_url = f"{API_BASE}/repos/{owner}/{repo}/git/blobs/{sha}"
            blob_response = github_request(blob_url, headers)
            blob_data = blob_response.json()
            
            content = ""
            if blob_data.get("encoding") == "base64":
                try:
                    content = base64.b64decode(blob_data["content"]).decode("utf-8", errors="replace")
                except Exception as e:
                    print(f"[ingest] Error decoding doc file {path}: {e}")
                    
            docs.append({
                "repo": f"{owner}/{repo}",
                "type": "documentation",
                "id": path,
                "title": Path(path).name,
                "body": content,
                "author": "system",
                "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "url": f"https://github.com/{owner}/{repo}/blob/{default_branch}/{path}",
                "doc_type": classify_doc(path)
            })
            
    except Exception as e:
        print(f"[ingest] Error fetching docs directory files: {e}")
        
    # 3. GitHub Wiki cloning shallow
    wiki_docs = fetch_wiki_docs(owner, repo)
    docs.extend(wiki_docs)
    
    return docs


def main():
    parser = argparse.ArgumentParser(description="Pull recent commits, PRs, issues, discussions, source files, and docs from a GitHub repo.")
    parser.add_argument("repo", help="Repository in owner/repo format, e.g. torvalds/linux")
    parser.add_argument("--limit", type=int, default=20, help="Number of items to pull")
    parser.add_argument("--max-commits", type=int, default=20, help="Max commits to fetch details for")
    
    parser.add_argument("--include-code", action="store_true", default=True, help="Include source code files (default)")
    parser.add_argument("--no-code", dest="include_code", action="store_false")
    parser.add_argument("--include-docs", action="store_true", default=True, help="Include documentation files (default)")
    parser.add_argument("--no-docs", dest="include_docs", action="store_false")
    parser.add_argument("--include-discussions", action="store_true", default=True, help="Include GitHub Discussions (default)")
    parser.add_argument("--no-discussions", dest="include_discussions", action="store_false")
    
    args = parser.parse_args()

    if "/" not in args.repo:
        sys.exit("Repo must be in owner/repo format, e.g. facebook/react")
    owner, repo = args.repo.split("/", 1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Category 1: Repository metadata
    metadata = fetch_repo_metadata(owner, repo)
    default_branch = metadata.get("default_branch", "main")

    # Category 2: Commits
    print(f"Fetching expanded commits from {owner}/{repo} (max commits: {args.max_commits}) ...")
    commits = fetch_commits(owner, repo, args.max_commits)
    commits_path = DATA_DIR / f"{repo}_commits.json"
    with open(commits_path, "w", encoding="utf-8") as f:
        json.dump(commits, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(commits)} commits -> {commits_path}")

    # Category 3 & 4: PRs & Issues
    print(f"Fetching pull requests from {owner}/{repo} (limit: {args.limit}) ...")
    prs = fetch_pull_requests(owner, repo, args.limit)
    
    print(f"Fetching issues from {owner}/{repo} (limit: {args.limit}) ...")
    issues = fetch_issues(owner, repo, commits, prs, args.limit)
    
    combined_items = prs + issues
    issues_path = DATA_DIR / f"{repo}_issues.json"
    with open(issues_path, "w", encoding="utf-8") as f:
        json.dump(combined_items, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(combined_items)} combined PRs & issues -> {issues_path}")

    # Category 5: Discussions
    if args.include_discussions:
        discussions = fetch_discussions(owner, repo)
        discussions_path = DATA_DIR / f"{repo}_discussions.json"
        with open(discussions_path, "w", encoding="utf-8") as f:
            json.dump(discussions, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(discussions)} discussions -> {discussions_path}")

    # Category 6: Source Files
    if args.include_code:
        source_files = fetch_source_files(owner, repo, default_branch)
        code_path = DATA_DIR / f"{repo}_code.json"
        with open(code_path, "w", encoding="utf-8") as f:
            json.dump(source_files, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(source_files)} source files -> {code_path}")

    # Category 7: Documentation
    if args.include_docs:
        docs = fetch_documentation(owner, repo, default_branch)
        docs_path = DATA_DIR / f"{repo}_docs.json"
        with open(docs_path, "w", encoding="utf-8") as f:
            json.dump(docs, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(docs)} doc files -> {docs_path}")


if __name__ == "__main__":
    main()
