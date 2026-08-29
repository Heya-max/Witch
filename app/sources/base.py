from __future__ import annotations

from ..player.models import Track


class MusicSource:
    """Abstract music source/provider interface."""

    async def search(self, query: str) -> list[Track]:
        raise NotImplementedError()

    async def get_metadata(self, source_id: str) -> Track:
        raise NotImplementedError()

    async def resolve_audio(self, source_id: str) -> str:
        """Return a playable audio URL or path for the given source id."""
        raise NotImplementedError()
