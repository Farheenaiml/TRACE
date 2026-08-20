from pathlib import Path


def repository_key(repo_id: str) -> str:
    parts = [part for part in (repo_id or "").strip().split("/") if part]
    return "__".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "unknown")


def repository_slug(repo_id: str) -> str:
    parts = [part for part in (repo_id or "").strip().split("/") if part]
    return parts[-1] if parts else "unknown"


def data_path(repo_id: str, suffix: str) -> Path:
    raw_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    canonical = raw_dir / f"{repository_key(repo_id)}_{suffix}"
    legacy = raw_dir / f"{repository_slug(repo_id)}_{suffix}"
    return canonical if canonical.exists() or not legacy.exists() else legacy
