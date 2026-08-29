from dataclasses import dataclass, fields


@dataclass
class Track:
    id: str
    title: str
    artist: str | None = None
    album: str | None = None
    duration: int | None = None  # seconds
    thumbnail: str | None = None
    source: str | None = None
    source_id: str | None = None
    source_url: str | None = None
    requested_by: int | None = None
    metadata: dict | None = None

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
