import logging

from yt_dlp import YoutubeDL

from ...player.models import Track
from ..base import MusicSource

logger = logging.getLogger(__name__)


class YtDlpProvider(MusicSource):
    """Provider using yt-dlp for search and audio resolution.

    Notes:
    - Only extracts metadata and direct audio URLs exposed by yt-dlp.
    - Do NOT use this to bypass DRM or access restricted content.
    """

    def __init__(self) -> None:
        self.ydl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "nocheckcertificate": True,
            "skip_download": True,
        }

    async def search(self, query: str) -> list[Track]:
        # Use ytsearch to find videos
        q = f"ytsearch5:{query}"
        with YoutubeDL(self.ydl_opts) as ydl:
            try:
                info = ydl.extract_info(q, download=False)
            except Exception as e:
                logger.exception("yt-dlp search failed: %s", e)
                return []

        entries = info.get("entries") or []
        results: list[Track] = []
        for item in entries:
            results.append(
                Track(
                    id=item.get("id") or item.get("webpage_url"),
                    title=item.get("title") or "Unknown",
                    duration=item.get("duration"),
                    thumbnail=item.get("thumbnail"),
                    source="yt-dlp",
                    source_id=item.get("id"),
                    source_url=item.get("webpage_url"),
                    metadata=item,
                )
            )
        return results

    async def get_metadata(self, source_id: str) -> Track:
        url = source_id
        # if source_id looks like an id, construct a youtube url
        if not source_id.startswith("http"):
            url = f"https://www.youtube.com/watch?v={source_id}"

        with YoutubeDL(self.ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        return Track(
            id=info.get("id") or info.get("webpage_url"),
            title=info.get("title"),
            duration=info.get("duration"),
            thumbnail=info.get("thumbnail"),
            source="yt-dlp",
            source_id=info.get("id"),
            source_url=info.get("webpage_url"),
            metadata=info,
        )

    async def resolve_audio(self, source_id: str) -> str:
        # Accept either a video id or a full url
        url = source_id if source_id.startswith("http") else f"https://www.youtube.com/watch?v={source_id}"
        opts = dict(self.ydl_opts)
        opts["format"] = "bestaudio/best"

        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # yt-dlp may return 'url' for direct playable stream or 'formats'
        if info is None:
            raise RuntimeError("yt-dlp returned no info")

        # If single URL present in info
        if info.get("url"):
            return info["url"]

        formats = info.get("formats") or []
        # pick the best audio format available
        best = None
        for f in formats:
            if f.get("acodec") and f.get("acodec") != "none":
                best = f
                break

        if best and best.get("url"):
            return best["url"]

        raise RuntimeError("No playable audio URL found")
