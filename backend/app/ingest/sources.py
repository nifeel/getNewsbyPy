from dataclasses import dataclass
from pathlib import Path

import yaml

from app.charts_db import charts_configured
from app.config import settings
from app.fj_settings import get_effective_fj_url
from app.proxy_settings import get_effective_proxy

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class SourceConfig:
    id: str
    name: str
    region: str
    kind: str
    access: str
    enabled: bool
    url: str
    poll_interval_minutes: int
    lang: str
    note: str = ""

    @property
    def fetch_url(self) -> str:
        if self.access == "rsshub":
            return settings.rsshub_base.rstrip("/") + self.url
        return self.url

    @property
    def runnable(self) -> bool:
        if not self.enabled:
            return False
        if settings.active_region not in {"all", self.region}:
            return False
        if self.kind == "chart" and not charts_configured():
            return False
        if self.access == "proxy":
            return bool(get_effective_proxy())
        if self.access == "fj_readonly":
            return bool(get_effective_fj_url())
        return True


def load_sources() -> list[SourceConfig]:
    raw = yaml.safe_load((DATA_DIR / "sources.yaml").read_text(encoding="utf-8"))
    items: list[SourceConfig] = []
    for row in raw.get("sources", []):
        items.append(
            SourceConfig(
                id=row["id"],
                name=row["name"],
                region=row["region"],
                kind=row["kind"],
                access=row["access"],
                enabled=bool(row.get("enabled", True)),
                url=row["url"],
                poll_interval_minutes=int(row.get("poll_interval_minutes", 15)),
                lang=row.get("lang", "zh"),
                note=row.get("note", "") or "",
            )
        )
    return items


def get_source_map() -> dict[str, SourceConfig]:
    return {item.id: item for item in load_sources()}
