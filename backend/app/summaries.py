from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from app.config import BACKEND_DIR

SUMMARIES_DIR = BACKEND_DIR / "data" / "summaries"
DATE_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


def summaries_dir() -> Path:
    path = SUMMARIES_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _title_from_markdown(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            if title:
                return title
    return fallback


def list_summaries() -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    folder = summaries_dir()
    for path in folder.iterdir():
        match = DATE_FILE.fullmatch(path.name)
        if not match or not path.is_file():
            continue
        day = match.group(1)
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        text = path.read_text(encoding="utf-8")
        items.append(
            {
                "date": day,
                "title": _title_from_markdown(text, day),
                "filename": path.name,
            }
        )
    items.sort(key=lambda item: item["date"], reverse=True)
    return items


def read_summary(day: str) -> dict[str, str] | None:
    raw = (day or "").strip()
    if not DATE_FILE.fullmatch(f"{raw}.md"):
        return None
    try:
        date.fromisoformat(raw)
    except ValueError:
        return None
    path = summaries_dir() / f"{raw}.md"
    try:
        resolved = path.resolve()
        resolved.relative_to(summaries_dir().resolve())
    except ValueError:
        return None
    if not resolved.is_file():
        return None
    text = resolved.read_text(encoding="utf-8")
    return {
        "date": raw,
        "title": _title_from_markdown(text, raw),
        "markdown": text,
    }
