import ast
import re
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

# Import the functions to test
import ingest

def test_is_excluded_path():
    assert ingest.is_excluded_path("node_modules/lodash/index.js") is True
    assert ingest.is_excluded_path(".git/config") is True
    assert ingest.is_excluded_path("__pycache__/main.cpython-310.pyc") is True
    assert ingest.is_excluded_path(".venv/lib/site-packages/requests/api.py") is True
    assert ingest.is_excluded_path("build/index.js") is True
    assert ingest.is_excluded_path("package-lock.json") is True
    assert ingest.is_excluded_path("src/logo.png") is True
    assert ingest.is_excluded_path("src/components/Button.tsx") is False
    assert ingest.is_excluded_path("backend/ingest.py") is False


def test_parse_python_structure():
    content = """
import os
import sys
from pathlib import Path
from requests import get as http_get

class DatabaseConnector:
    def __init__(self):
        pass
    def connect(self):
        pass

def run_query(sql):
    return None
"""
    imports, functions, classes = ingest.parse_python_structure(content)
    assert "os" in imports
    assert "sys" in imports
    assert "pathlib.Path" in imports
    assert "requests.get" in imports
    assert "DatabaseConnector" in classes
    assert "run_query" in functions
    # Verify we only parse top-level functions/classes, not nested methods
    assert "__init__" not in functions
    assert "connect" not in functions


def test_parse_regex_structure_js():
    content = """
import { useState, useEffect } from 'react';
import axios from 'axios';
const config = require('./config');

export class AuthPanel extends React.Component {
    render() {
        return null;
    }
}

function fetchUser(id) {
    return axios.get(`/user/${id}`);
}

const formatName = (user) => {
    return user.name;
};
"""
    imports, functions, classes = ingest.parse_regex_structure(content)
    assert any("react" in imp for imp in imports)
    assert any("axios" in imp for imp in imports)
    assert any("config" in imp for imp in imports)
    assert "AuthPanel" in classes
    assert "fetchUser" in functions
    assert "formatName" in functions


def test_classify_doc():
    assert ingest.classify_doc("README.md") == "readme"
    assert ingest.classify_doc("readme.txt") == "readme"
    assert ingest.classify_doc("docs/architecture/design.md") == "architecture"
    assert ingest.classify_doc("docs/ADR-001-storage.md") == "architecture"
    assert ingest.classify_doc("docs/api/endpoints.md") == "api_docs"
    assert ingest.classify_doc("api-reference.md") == "api_docs"
    assert ingest.classify_doc("docs/guide.md") == "other_markdown"
    assert ingest.classify_doc("CONTRIBUTING.md") == "other_markdown"


@patch('ingest.github_request')
def test_fetch_repo_metadata(mock_request):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "name": "TRACE",
        "description": "Decision intelligence platform",
        "owner": {"login": "Farheenaiml", "type": "Organization"},
        "stargazers_count": 42,
        "forks_count": 7,
        "language": "Python",
        "topics": ["ai", "neo4j", "qdrant"],
        "license": {"spdx_id": "MIT"},
        "default_branch": "main"
    }
    mock_request.return_value = mock_response

    metadata = ingest.fetch_repo_metadata("Farheenaiml", "TRACE")
    
    assert metadata["name"] == "TRACE"
    assert metadata["description"] == "Decision intelligence platform"
    assert metadata["owner"]["login"] == "Farheenaiml"
    assert metadata["owner"]["type"] == "Organization"
    assert metadata["stargazers_count"] == 42
    assert metadata["forks_count"] == 7
    assert metadata["primary_language"] == "Python"
    assert metadata["topics"] == ["ai", "neo4j", "qdrant"]
    assert metadata["license"] == "MIT"
    assert metadata["default_branch"] == "main"


@patch('ingest.github_request')
def test_fetch_commits(mock_request):
    # Mock listing call and detail call
    mock_list_response = MagicMock()
    mock_list_response.json.return_value = [{"sha": "abc1234commitsha"}]
    
    mock_detail_response = MagicMock()
    mock_detail_response.json.return_value = {
        "parents": [{"sha": "parentsha123"}],
        "author": {"login": "octocat"},
        "commit": {
            "message": "Fix memory leak in auth\n\nAvoid caching sessions indefinitely.",
            "author": {"date": "2026-08-20T12:00:00Z"}
        },
        "html_url": "https://github.com/octocat/Spoon-Knife/commit/abc1234commitsha",
        "files": [
            {
                "filename": "auth.py",
                "status": "modified",
                "additions": 10,
                "deletions": 2,
                "patch": "@@ -1,5 +1,13 @@..."
            }
        ]
    }
    
    mock_request.side_effect = [mock_list_response, mock_detail_response]

    commits = ingest.fetch_commits("octocat", "Spoon-Knife", limit=1)
    
    assert len(commits) == 1
    c = commits[0]
    assert c["id"] == "abc1234commitsha"
    assert c["title"] == "Fix memory leak in auth"
    assert c["body"] == "Fix memory leak in auth\n\nAvoid caching sessions indefinitely."
    assert c["author"] == "octocat"
    assert c["parents"] == ["parentsha123"]
    assert len(c["files"]) == 1
    assert c["files"][0]["filename"] == "auth.py"
    assert c["files"][0]["patch"] == "@@ -1,5 +1,13 @@..."
    assert c["diff_truncated"] is False


@patch('ingest.github_request')
def test_fetch_pull_requests(mock_request):
    # Mock list pulls, detail pulls, reviews, comments, files
    mock_list = MagicMock()
    mock_list.json.return_value = [{"number": 42}]
    
    mock_detail = MagicMock()
    mock_detail.json.return_value = {
        "title": "Add OAuth support",
        "body": "Closes #15\nImplements refresh token rotation.",
        "user": {"login": "aditi"},
        "created_at": "2026-08-20T10:00:00Z",
        "updated_at": "2026-08-20T11:00:00Z",
        "state": "closed",
        "labels": [{"name": "security"}],
        "html_url": "https://github.com/octocat/Spoon-Knife/pull/42",
        "merged_at": "2026-08-20T11:00:00Z",
        "merge_commit_sha": "mergecommitsha123",
        "requested_reviewers": [{"login": "marcus"}]
    }
    
    mock_reviews = MagicMock()
    mock_reviews.json.return_value = [
        {
            "user": {"login": "marcus"},
            "state": "APPROVED",
            "body": "Looks great!"
        }
    ]
    
    mock_comments = MagicMock()
    mock_comments.json.return_value = [
        {
            "user": {"login": "rin"},
            "body": "Wait, did we check refresh token expiration?",
            "created_at": "2026-08-20T10:30:00Z"
        }
    ]
    
    mock_files = MagicMock()
    mock_files.json.return_value = [
        {"filename": "auth.py"},
        {"filename": "config.json"}
    ]
    
    mock_request.side_effect = [mock_list, mock_detail, mock_reviews, mock_comments, mock_files]
    
    prs = ingest.fetch_pull_requests("octocat", "Spoon-Knife", limit=1)
    
    assert len(prs) == 1
    pr = prs[0]
    assert pr["id"] == "42"
    assert pr["title"] == "Add OAuth support"
    assert pr["author"] == "aditi"
    assert pr["merge_commit_sha"] == "mergecommitsha123"
    assert pr["requested_reviewers"] == ["marcus"]
    assert len(pr["reviews"]) == 1
    assert pr["reviews"][0]["reviewer"] == "marcus"
    assert pr["reviews"][0]["state"] == "APPROVED"
    assert len(pr["comments"]) == 1
    assert pr["comments"][0]["author"] == "rin"
    assert pr["changed_files"] == ["auth.py", "config.json"]


@patch('ingest.github_request')
def test_fetch_issues(mock_request):
    mock_list = MagicMock()
    mock_list.json.return_value = [
        {
            "number": 15,
            "title": "OAuth security issue",
            "body": "Tokens are not encrypted in DB.",
            "user": {"login": "bob"},
            "state": "closed",
            "created_at": "2026-08-19T10:00:00Z",
            "updated_at": "2026-08-20T11:00:00Z",
            "labels": [{"name": "bug"}],
            "comments": 2,
            "html_url": "https://github.com/octocat/Spoon-Knife/issues/15",
            "milestone": {"title": "Release 1.0", "due_on": "2026-09-01T00:00:00Z"}
        }
    ]
    
    mock_detail = MagicMock()
    mock_detail.json.return_value = {
        "closed_by": {"login": "marcus"}
    }
    
    mock_timeline = MagicMock()
    mock_timeline.json.return_value = [
        {
            "event": "referenced",
            "commit_id": "commitsha789"
        },
        {
            "event": "cross-referenced",
            "source": {
                "type": "issue",
                "issue": {
                    "number": 42,
                    "pull_request": {"url": "https://api.github.com/repos/octocat/Spoon-Knife/pulls/42"}
                }
            }
        }
    ]
    
    mock_request.side_effect = [mock_list, mock_detail, mock_timeline]
    
    # We pass a mock list of commits and PRs to check text pattern fallbacks
    mock_commits_db = [{"id": "commitsha999", "body": "Resolves #15"}]
    mock_prs_db = [{"id": "99", "body": "This closes #15"}]
    
    issues = ingest.fetch_issues("octocat", "Spoon-Knife", mock_commits_db, mock_prs_db, limit=1)
    
    assert len(issues) == 1
    issue = issues[0]
    assert issue["id"] == "15"
    assert issue["author"] == "bob"
    assert issue["opened_by"] == "bob"
    assert issue["closed_by"] == "marcus"
    assert issue["milestone"]["title"] == "Release 1.0"
    
    # Linked items from timeline
    assert {"id": "commitsha789", "link_source": "timeline"} in issue["linked_commits"]
    assert {"id": "42", "link_source": "timeline"} in issue["linked_prs"]
    
    # Linked items from text patterns
    assert {"id": "commitsha999", "link_source": "text_pattern"} in issue["linked_commits"]
    assert {"id": "99", "link_source": "text_pattern"} in issue["linked_prs"]

if __name__ == "__main__":
    print("Running unit tests...")
    test_is_excluded_path()
    test_parse_python_structure()
    test_parse_regex_structure_js()
    test_classify_doc()
    
    # Run mocked network tests
    test_fetch_repo_metadata()
    print("fetch_repo_metadata test passed!")
    
    test_fetch_commits()
    print("fetch_commits test passed!")
    
    test_fetch_pull_requests()
    print("fetch_pull_requests test passed!")
    
    test_fetch_issues()
    print("fetch_issues test passed!")
    
    print("All unit tests passed successfully!")

