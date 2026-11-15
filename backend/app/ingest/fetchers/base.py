from abc import ABC, abstractmethod

from app.ingest.sources import SourceConfig
from app.ingest.types import RawItem


class BaseFetcher(ABC):
    access: str

    @abstractmethod
    async def fetch(self, source: SourceConfig) -> list[RawItem]:
        raise NotImplementedError
