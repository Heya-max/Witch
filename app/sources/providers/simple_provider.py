from ...player.models import Track
from ..base import MusicSource


class SimpleProvider(MusicSource):
    """Very small provider that treats http(s) URLs as playable sources and
    returns minimal metadata for plain queries.
    """

    async def search(self, query: str) -> list[Track]:
        # If it's a URL we return a single direct result
        if query.startswith("http://") or query.startswith("https://"):
            return [Track(id=query, title=query, source_url=query, source="url")]

        # Otherwise return a dummy result (real providers should call APIs)
        fake_id = f"local:{query}"
        return [Track(id=fake_id, title=f"Search: {query}", source="simple")]

    async def get_metadata(self, source_id: str) -> Track:
        return Track(id=source_id, title=source_id, source="simple")

    async def resolve_audio(self, source_id: str) -> str:
        # For URLs, return as-is; for fake ids, raise NotImplemented
        if source_id.startswith("http://") or source_id.startswith("https://"):
            return source_id
        raise NotImplementedError("SimpleProvider can only resolve http(s) urls")
