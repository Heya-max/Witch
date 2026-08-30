from .yt_dlp_provider import YtDlpProvider


class SpotifyProvider(YtDlpProvider):
    """Search Spotify tracks via yt-dlp's `spsearch` extractor."""

    def __init__(self) -> None:
        super().__init__(search_prefix="spsearch", search_limit=5)


class SoundCloudProvider(YtDlpProvider):
    """Search SoundCloud tracks via yt-dlp's `scsearch` extractor."""

    def __init__(self) -> None:
        super().__init__(search_prefix="scsearch", search_limit=5)


class DeezerProvider(YtDlpProvider):
    """Search Deezer tracks via yt-dlp's `dzsearch` extractor."""

    def __init__(self) -> None:
        super().__init__(search_prefix="dzsearch", search_limit=5)
