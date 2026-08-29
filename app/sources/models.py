from dataclasses import dataclass


@dataclass
class SearchResult:
    id: str
    title: str
    duration: int | None = None
    thumbnail: str | None = None
    source: str | None = None
