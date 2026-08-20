import json
from intelligence_pipeline import RepositoryIntelligencePipeline

def run_tests():
    pipeline = RepositoryIntelligencePipeline("test-owner/test-repo")
    
    # 1. Test Text Cleaning
    raw_text = """
    ### Description
    <!-- Please add a description of the bug -->
    This is the description of the issue.
    
    This issue has been automatically marked as stale because it has not had recent activity.
    It will be closed if no further activity occurs.
    
    ### Logs
    Some crash logs here.
    """
    cleaned = pipeline.clean_text(raw_text)
    print("=== Cleaned Text ===")
    print(repr(cleaned))
    print()

    # 2. Test Cross Reference Parsing
    message = "fixes #12 and refs #43. Closes #111 and references 5bd067d3dd8742deb9e280e7e3bb988b4379a81a."
    refs = pipeline.resolve_cross_references(message, "sha123", "commit")
    print("=== Resolved Cross References ===")
    print(json.dumps(refs, indent=2))
    
if __name__ == "__main__":
    run_tests()
