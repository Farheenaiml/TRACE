import sys
from unittest.mock import MagicMock
sys.modules['sentence_transformers'] = MagicMock()

import json
import os
import shutil
import pytest
from unittest.mock import patch
from pathlib import Path

# Import the helpers from main
import main

def test_parse_github_repo():
    # Valid urls
    assert main.parse_github_repo("https://github.com/facebook/react") == ("facebook", "react")
    assert main.parse_github_repo("github.com/facebook/react") == ("facebook", "react")
    assert main.parse_github_repo("facebook/react") == ("facebook", "react")
    assert main.parse_github_repo("https://github.com/facebook/react.git") == ("facebook", "react")
    assert main.parse_github_repo("git@github.com:facebook/react.git") == ("facebook", "react")
    assert main.parse_github_repo("facebook/react/") == ("facebook", "react")
    
    # Reject invalid shapes
    with pytest.raises(ValueError):
        main.parse_github_repo("facebook")
    with pytest.raises(ValueError):
        main.parse_github_repo("http://gitlab.com/facebook/react")


@patch('main.get_status_file_path')
def test_write_read_status(mock_get_path, tmp_path):
    # Set up temp path for test status file
    status_file = tmp_path / "react_ingest_status.json"
    mock_get_path.return_value = status_file
    
    # Ensure empty initial state
    if "facebook/react" in main.REPO_STATUSES:
        del main.REPO_STATUSES["facebook/react"]
        
    # Write status
    main.write_ingest_status("facebook/react", "ingesting", "fetching_issues", error=None)
    
    # Verify in-memory state updated
    assert main.REPO_STATUSES["facebook/react"]["status"] == "ingesting"
    assert main.REPO_STATUSES["facebook/react"]["step"] == "fetching_issues"
    
    # Verify file saved
    assert status_file.exists()
    file_data = json.loads(status_file.read_text())
    assert file_data["status"] == "ingesting"
    assert file_data["step"] == "fetching_issues"
    
    # Clear memory cache and read from file
    del main.REPO_STATUSES["facebook/react"]
    status_read = main.read_ingest_status("facebook/react")
    assert status_read["status"] == "ingesting"
    assert status_read["step"] == "fetching_issues"


@patch('main.get_status_file_path')
def test_read_status_fallback(mock_get_path, tmp_path):
    status_file = tmp_path / "react_ingest_status.json"
    mock_get_path.return_value = status_file
    
    # Clear cache
    if "facebook/react" in main.REPO_STATUSES:
        del main.REPO_STATUSES["facebook/react"]
        
    # Mock data raw dir path lookup in read_ingest_status
    def mock_exists_impl(self):
        return "react_issues.json" in str(self)
        
    with patch('pathlib.Path.exists', mock_exists_impl):
        status = main.read_ingest_status("facebook/react")
        assert status["status"] == "ready"
        assert status["step"] == "done"


if __name__ == "__main__":
    print("Running main server unit tests...")
    test_parse_github_repo()
    print("parse_github_repo test passed!")
    
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmpdir:
        test_write_read_status(None, Path(tmpdir))
        print("write_read_status test passed!")
        
    test_read_status_fallback(None, None)
    print("read_status_fallback test passed!")
    
    print("All server unit tests passed successfully!")
