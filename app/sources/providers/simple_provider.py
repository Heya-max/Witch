from ...player.models import Track
from ..base import MusicSource
from ..validation import is_http_url, looks_like_audio


class SimpleProvider(MusicSource):
    """Very small provider that treats http(s) URLs as playable sources and
    returns minimal metadata for plain queries.

    Only *audio-like* URLs are treated as direct sources. A plain website URL
    (e.g. ``https://techaistudy.com``) is not returned as a track so it can
    never reach playback as a direct stream.
    """

    async def search(self, query: str) -> list[Track]:
        # If it's an audio-like URL we return a single direct result
        if is_http_url(query):
            if looks_like_audio(query):
                return [Track(id=query, title=query, source_url=query, source="url")]
            # A non-audio website URL: not a direct source; fall through to
            # the other providers (which may still resolve media from it).
            return []

        # Otherwise return a dummy result (real providers should call APIs)
        fake_id = f"local:{query}"
        return [Track(id=fake_id, title=f"Search: {query}", source="simple")]

    async def get_metadata(self, source_id: str) -> Track:
        return Track(id=source_id, title=source_id, source="simple")

    async def resolve_audio(self, source_id: str) -> str:
        # Only hand audio-like URLs through; anything else is a website/expired
        # link and must not be passed to FFmpeg/pytgcalls.
        if is_http_url(source_id):
            if looks_like_audio(source_id):
                return source_id
            raise ValueError(f"refusing non-audio URL: {source_id}")
        raise NotImplementedError("SimpleProvider can only resolve http(s) urls")
