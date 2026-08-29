from .providers.simple_provider import SimpleProvider
from .providers.yt_dlp_provider import YtDlpProvider


def get_default_providers():
    # Order: yt-dlp first (best user search), fallback to simple
    return [YtDlpProvider(), SimpleProvider()]
