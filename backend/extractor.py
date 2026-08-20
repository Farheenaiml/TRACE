"""
TRACE — Week 1 starter script (Member 2).

Reads the JSON that ingest.py (Member 1) produces, and flags which
sentences in each commit message actually explain WHY a change was
made — as opposed to sentences that just describe WHAT changed.

This is a deliberately simple rule-based stub for Week 1. In Week 3
this gets replaced with a real trained classifier (see the roadmap) —
but the INPUT and OUTPUT shape stays the same, so nothing downstream
needs to change when the real model drops in.

Usage:
    python extractor.py <path/to/commits.json>

Example:
    python extractor.py ../data/raw/sample_commits.json
"""

import argparse
import json
import re
from pathlib import Path

# Words/phrases that tend to signal a sentence is explaining reasoning,
# not just describing an action. This list is intentionally simple —
# it exists to prove the pipeline, not to be accurate. Week 3 replaces
# this with a trained model.
RATIONALE_MARKERS = [
    "because", "since", "so that", "in order to", "to avoid",
    "to prevent", "to fix", "to reduce", "to close", "flagged",
    "gives us", "reduces", "caused", "allows", "lets us",
]


def split_sentences(text: str) -> list[str]:
    """Very small sentence splitter — good enough for commit messages."""
    text = text.replace("\n", " ")
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in sentences if s.strip()]


def is_rationale_sentence(sentence: str) -> bool:
    lower = sentence.lower()
    return any(marker in lower for marker in RATIONALE_MARKERS)


def extract_rationale(records: list[dict]) -> list[dict]:
    """For each commit, issue, or pull request, pull out the sentences that look like rationale."""
    results = []
    for r in records:
        body = r.get("body") or ""
        title = r.get("title") or ""
        text_to_split = f"{title}\n{body}" if title and body else (body or title)
        
        sentences = split_sentences(text_to_split)
        rationale_sentences = [s for s in sentences if is_rationale_sentence(s)]

        results.append({
            "source_id": str(r.get("id", "")),
            "source_type": r.get("type", "commit"),
            "source_url": r.get("url", "#"),
            "repo": r.get("repo", ""),
            "title": title,
            "body": body,
            "author": r.get("author", "unknown"),
            "date": r.get("date") or r.get("updated_at", ""),
            "updated_at": r.get("updated_at", ""),
            "has_rationale": len(rationale_sentences) > 0,
            "rationale_sentences": rationale_sentences,
        })
    return results


def main():
    parser = argparse.ArgumentParser(description="Extract rationale sentences from ingested commits, issues, or PRs.")
    parser.add_argument("input_file", help="Path to a JSON file produced by ingest.py")
    args = parser.parse_args()

    in_path = Path(args.input_file)
    records = json.loads(in_path.read_text(encoding="utf-8"))

    print(f"Loaded {len(records)} records from {in_path}")
    extracted = extract_rationale(records)

    flagged = [r for r in extracted if r["has_rationale"]]
    print(f"Flagged {len(flagged)}/{len(records)} records as containing rationale.\n")

    for r in extracted:
        marker = "✅ RATIONALE" if r["has_rationale"] else "—  no rationale"
        stype = r["source_type"].upper()
        print(f"[{marker}] [{stype}] #{r['source_id']}")
        for s in r["rationale_sentences"]:
            print(f"      → {s}")

    out_path = in_path.parent / f"{in_path.stem}_extracted.json"
    out_path.write_text(json.dumps(extracted, indent=2), encoding="utf-8")
    print(f"\nSaved extraction results -> {out_path}")


if __name__ == "__main__":
    main()
