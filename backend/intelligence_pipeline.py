import re
from typing import List, Dict, Any

class RepositoryIntelligencePipeline:
    def __init__(self, repo_id: str):
        self.repo_id = repo_id

    def clean_text(self, text: str) -> str:
        if not text:
            return ""
        # 1. Strip HTML/Markdown comments (e.g. <!-- comments -->)
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        # 2. Strip common issue template headers
        text = re.sub(r"(?i)###\s*(description|reproduction steps|actual behavior|expected behavior|steps to reproduce|environment|context|reproduce|logs|formatting_tips|alerts|code and diffs|mermaid diagrams|tables|file links and media|carousels|critical rules)", "", text)
        # 3. Strip stale bot responses or common bot boilerplate comments
        stale_pattern = r"(?i)(this issue/pr has been automatically marked|stale due to|no recent activity|will be closed in|please add a comment)"
        if re.search(stale_pattern, text):
            # Parse line by line to filter out bot templates
            lines = text.split("\n")
            cleaned_lines = [l for l in lines if not re.search(stale_pattern, l)]
            text = "\n".join(cleaned_lines)
        # 4. Collapse consecutive newlines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def resolve_cross_references(self, text: str, source_id: str, source_type: str) -> List[Dict[str, Any]]:
        """
        Parses text for issue references (e.g. #123, fixes #12), commit SHAs (40 hex chars).
        Returns a list of structured references relationships.
        """
        refs = []
        if not text:
            return refs

        # 1. Match Issue/PR number references (e.g. #123)
        # Check if preceded by transition words like "fix", "close", "resolve"
        issue_pattern_ref = r"(?i)(?:fixes|closes|resolves|reverts|addresses)?\s*#(\d+)"
        matches = re.finditer(issue_pattern_ref, text)
        for m in matches:
            ref_num = m.group(1)
            full_match = m.group(0).lower()
            rel_type = "REFERENCES"
            if "fix" in full_match or "close" in full_match or "resolve" in full_match:
                rel_type = "FIXES"
            elif "revert" in full_match:
                rel_type = "REVERTS"
            
            refs.append({
                "source_id": source_id,
                "source_type": source_type,
                "target_id": f"{self.repo_id}:{ref_num}",
                "target_type": "Issue_or_PR",
                "relationship": rel_type
            })

        # 2. Match 40-char commit SHAs
        sha_pattern = r"\b([a-f0-9]{40})\b"
        sha_matches = re.finditer(sha_pattern, text)
        for m in sha_matches:
            target_sha = m.group(1)
            if target_sha != source_id:
                refs.append({
                    "source_id": source_id,
                    "source_type": source_type,
                    "target_id": target_sha,
                    "target_type": "Commit",
                    "relationship": "REFERENCES_COMMIT"
                })

        return refs

    def normalize_pipeline(self, commits: List[Dict], discussions: List[Dict], issues: List[Dict]) -> Dict[str, Any]:
        """
        Runs the full normalization pipeline on raw ingested elements.
        """
        normalized_commits = []
        normalized_discussions = []
        normalized_issues = []
        cross_references = []

        # Process Commits
        for c in commits:
            cleaned_message = self.clean_text(c.get("message", ""))
            c_copy = dict(c)
            c_copy["message"] = cleaned_message
            c_copy["body"] = cleaned_message
            normalized_commits.append(c_copy)
            cross_references.extend(self.resolve_cross_references(cleaned_message, c["sha"], "commit"))

        # Process Discussions
        for d in discussions:
            d_copy = dict(d)
            d_copy["body"] = self.clean_text(d.get("body", ""))
            
            cleaned_comments = []
            for comment in d.get("comments", []):
                cmt_body = self.clean_text(comment.get("body", ""))
                if cmt_body:
                    cleaned_cmt = dict(comment)
                    cleaned_cmt["body"] = cmt_body
                    cleaned_comments.append(cleaned_cmt)
                    cross_references.extend(self.resolve_cross_references(cmt_body, comment["id"], "comment"))
            
            d_copy["comments"] = cleaned_comments
            normalized_discussions.append(d_copy)
            cross_references.extend(self.resolve_cross_references(d_copy["body"], d["id"], "discussion"))

        # Process Issues & PRs
        for i in issues:
            i_copy = dict(i)
            i_copy["body"] = self.clean_text(i.get("body", ""))
            
            cleaned_comments = []
            for comment in i.get("comments_list", []):
                cmt_body = self.clean_text(comment.get("body", ""))
                if cmt_body:
                    cleaned_cmt = dict(comment)
                    cleaned_cmt["body"] = cmt_body
                    cleaned_comments.append(cleaned_cmt)
                    cross_references.extend(self.resolve_cross_references(cmt_body, comment["id"], "comment"))
            
            i_copy["comments_list"] = cleaned_comments
            normalized_issues.append(i_copy)
            
            source_type = i["type"]
            cross_references.extend(self.resolve_cross_references(i_copy["body"], f"{self.repo_id}:{i['id']}", source_type))

        return {
            "commits": normalized_commits,
            "discussions": normalized_discussions,
            "issues": normalized_issues,
            "cross_references": cross_references
        }
