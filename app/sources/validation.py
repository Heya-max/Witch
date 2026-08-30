"""URL validation helpers that keep non-audio junk out of playback.

Rule of thumb: a URL is "audio-like" when it either carries a known audio
(container or HLS/DASH playlist) file extension, or its host is a known
music/streaming service or media CDN. Anything else — an unknown website
(e.g. ``techaistudy.com``), a preview page, or an expired link parked on a
random host — is rejected instead of being handed to FFmpeg/pytgcalls.
"""

from urllib.parse import urlsplit

# Direct audio container formats.
AUDIO_EXTENSIONS = frozenset(
    {
        ".mp3",
        ".m4a",
        ".aac",
        ".ogg",
        ".oga",
        ".opus",
        ".wav",
        ".flac",
        ".weba",
        ".wma",
        ".aiff",
        ".mka",
        ".mp4a",
        ".mpeg",
        ".mpga",
    }
)

# HLS / DASH playlists are still "audio-like" and should be allowed through.
STREAM_EXTENSIONS = frozenset({".m3u8", ".m3u", ".mpd"})

# Hosts whose *pages* are (at least partly) music we can resolve/stream.
KNOWN_MEDIA_HOSTS = frozenset(
    {
        "youtube.com",
        "youtu.be",
        "youtube-nocookie.com",
        "music.youtube.com",
        "soundcloud.com",
        "bandcamp.com",
        "open.spotify.com",
        "spotify.com",
        "deezer.com",
        "vimeo.com",
        "music.apple.com",
        "itunes.apple.com",
        "twitch.tv",
        "mixcloud.com",
        "audiomack.com",
    }
)

# Hosts that only ever serve direct media streams (typical yt-dlp output).
STREAM_HOSTS = frozenset(
    {
        "googlevideo.com",
        "ggpht.com",
        "ytimg.com",
        "sc-cdn.net",
        "sndcdn.com",
        "soundcloud-cdn.net",
        "media.soundcloud.com",
        "akamaized.net",
        "vimeocdn.com",
        "cloudfront.net",
        "fastly.net",
    }
)


def is_http_url(value: str) -> bool:
    """True when `value` is an http(s) URL string."""
    return isinstance(value, str) and (value.startswith("http://") or value.startswith("https://"))


def _host(value: str) -> str:
    try:
        return (urlsplit(value).netloc or "").lower()
    except ValueError:
        return ""


def _file_extension(value: str) -> str:
    try:
        path = urlsplit(value).path or ""
    except ValueError:
        return ""
    dot = path.rfind(".")
    if dot == -1:
        return ""
    return path[dot:].lower()


def _host_matches(host: str, known: frozenset[str]) -> bool:
    if not host:
        return False
    if host in known:
        return True
    return any(host.endswith("." + h) for h in known)


def _has_audio_extension(url: str) -> bool:
    return _file_extension(url) in (AUDIO_EXTENSIONS | STREAM_EXTENSIONS)


def looks_like_audio(url: str) -> bool:
    """True for direct audio files, HLS/DASH playlists, or known media pages.

    Only meaningful for http(s) URLs; other values (plain ids, local paths)
    are outside the scope of this check and return False.

    The idea: an unknown website host with no audio extension can never be a
    safe audio source, so we refuse it up front (this is what kills the
    "website reached playback" bug).
    """
    if not is_http_url(url):
        return False
    if _has_audio_extension(url):
        return True
    host = _host(url)
    return _host_matches(host, KNOWN_MEDIA_HOSTS) or _host_matches(host, STREAM_HOSTS)


def is_rejected_http_url(value: str) -> bool:
    """True when `value` is an http(s) URL that is *not* audio-like."""
    return is_http_url(value) and not looks_like_audio(value)


def validate_stream_url(url: str) -> str:
    """Raise ``ValueError`` when an http(s) media URL is not audio-like.

    Non-http values (local file paths, plain ids, ...) pass through untouched;
    they are outside the scope of web-URL validation.
    """
    if is_http_url(url) and not looks_like_audio(url):
        raise ValueError(f"refusing non-audio media URL: {url}")
    return url
