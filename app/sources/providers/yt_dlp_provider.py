import glob
import logging
import os

from yt_dlp import YoutubeDL

from ...player.models import Track
from ..base import MusicSource

logger = logging.getLogger(__name__)


class YtDlpProvider(MusicSource):
    """Provider using yt-dlp for search, audio resolution and downloads.

    Notes:
    - Only extracts metadata and direct audio URLs exposed by yt-dlp.
    - Do NOT use this to bypass DRM or access restricted content.
    """

    def __init__(self, *, search_prefix: str = "ytsearch", search_limit: int = 5) -> None:
        self.search_prefix = search_prefix
        self.search_limit = search_limit
        self.ydl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "nocheckcertificate": True,
            "skip_download": True,
        }

    def _search_query(self, query: str) -> str:
        return f"{self.search_prefix}{self.search_limit}:{query}"

    async def search(self, query: str) -> list[Track]:
        q = self._search_query(query)
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

    async def resolve_playlist(self, url: str) -> list[Track]:
        """Resolve a playlist/album URL into Track objects.

        Returns a single-element list when the URL is a plain track. Raises
        when the URL cannot be extracted.
        """
        with YoutubeDL(self.ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            raise RuntimeError("yt-dlp returned no info")

        entries = (info.get("entries") or []) if info.get("_type") == "playlist" else [info]

        results: list[Track] = []
        for item in entries:
            if not item or item.get("_type") == "playlist":
                continue
            results.append(
                Track(
                    id=item.get("id") or item.get("webpage_url"),
                    title=item.get("title") or "Unknown",
                    artist=item.get("artist") or item.get("uploader"),
                    album=item.get("album"),
                    duration=item.get("duration"),
                    thumbnail=item.get("thumbnail"),
                    source="yt-dlp",
                    source_id=item.get("id"),
                    source_url=item.get("webpage_url") or item.get("url"),
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

    def download(self, source_id: str, dest_dir: str, *, max_filesize: int = 50 * 1024 * 1024) -> str:
        """Download the best audio stream to `dest_dir` and return the resulting file path.

        Blocking; call via `asyncio.to_thread` from async code.
        Raises `RuntimeError` when no audio file could be produced.
        """
        url = source_id if source_id.startswith("http") else f"https://www.youtube.com/watch?v={source_id}"
        opts = dict(self.ydl_opts)
        opts["format"] = "bestaudio/best"
        opts["skip_download"] = False
        opts["outtmpl"] = os.path.join(dest_dir, "%(id)s.%(ext)s")
        opts["max_filesize"] = max_filesize

        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

        if info is None:
            raise RuntimeError("yt-dlp download returned no info")

        requested = info.get("requested_downloads") or []
        for item in requested:
            fp = item.get("filepath")
            if fp and os.path.exists(fp):
                return fp
        fp = info.get("filepath")
        if fp and os.path.exists(fp):
            return fp

        files = sorted(glob.glob(os.path.join(dest_dir, "*")))
        if files:
            return files[0]

        raise RuntimeError("yt-dlp did not produce a file")
