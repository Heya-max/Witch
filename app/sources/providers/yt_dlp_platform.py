from .yt_dlp_provider import YtDlpProvider


class SoundCloudProvider(YtDlpProvider):
    """Search SoundCloud tracks via yt-dlp's `scsearch` extractor."""

    def __init__(self) -> None:
        super().__init__(search_prefix="scsearch", search_limit=5)
