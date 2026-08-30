from .providers.simple_provider import SimpleProvider
from .providers.yt_dlp_platform import DeezerProvider, SoundCloudProvider, SpotifyProvider
from .providers.yt_dlp_provider import YtDlpProvider


def get_default_providers():
    # Order: platform-specific search first (may be blocked/unavailable and
    # fall through), YouTube via yt-dlp next (best general coverage), and the
    # plain URL passthrough last.
    return [SpotifyProvider(), SoundCloudProvider(), DeezerProvider(), YtDlpProvider(), SimpleProvider()]
