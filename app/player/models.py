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
    # Stable key (page URL / ID) used to re-resolve a fresh playable URL at
    # play time. Signed stream URLs (e.g. SoundCloud) expire within minutes,
    # so `source_url` may hold a stale playable link captured at enqueue time.
    resolve_key: str | None = None
    requested_by: int | None = None
    metadata: dict | None = None

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def try_from_dict(cls, data: dict) -> "Track | None":
        """Like ``from_dict`` but never raises on malformed payloads.

        Returns ``None`` for non-dict inputs, payloads missing an ``id``, or
        any content that cannot be reconstructed. Used when loading persisted
        rows so one corrupt entry cannot crash the whole queue.
        """
        if not isinstance(data, dict):
            return None
        try:
            track = cls.from_dict(data)
        except Exception:
            return None
        if not getattr(track, "id", None):
            return None
        return track
