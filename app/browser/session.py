from pathlib import Path


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def storage_state_exists(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0

