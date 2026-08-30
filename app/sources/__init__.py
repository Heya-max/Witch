import logging

from .providers.simple_provider import SimpleProvider
from .providers.yt_dlp_platform import SoundCloudProvider
from .providers.yt_dlp_provider import YtDlpProvider
from .validation import is_http_url, validate_stream_url

logger = logging.getLogger(__name__)


def get_default_providers():
    # Order: platform-specific search first (may be blocked/unavailable and
    # fall through), YouTube via yt-dlp next (best general coverage), and the
    # plain URL passthrough last.
    return [SoundCloudProvider(), YtDlpProvider(), SimpleProvider()]


async def resolve_playable(track) -> str:
    """Freshly resolve `track` to a playable URL at play time.

    Signed stream URLs (e.g. SoundCloud) expire within minutes of being issued;
    resolving only when the track is about to play keeps the URL fresh. Falls
    back to the best stored key when resolution fails so playback can proceed
    with whatever was captured at enqueue time.

    Every resolved URL is validated as audio-like; a provider that returns a
    non-media URL (a webpage, an expired link) is skipped, and if nothing
    produces a usable stream a ``ValueError`` is raised so junk can never be
    handed to the voice manager.
    """
    key = track.resolve_key or track.source_url or track.source_id or track.id or ""
    if not key:
        return key
    for provider in get_default_providers():
        try:
            url = await provider.resolve_audio(key)
            if url:
                return validate_stream_url(url)
        except ValueError:
            logger.warning(
                "provider %s resolved a non-audio URL for %s; skipping",
                type(provider).__name__,
                key,
            )
            continue
        except Exception:
            continue
    if is_http_url(key):
        return validate_stream_url(key)
    raise ValueError(f"could not resolve a playable audio source for {key!r}")
