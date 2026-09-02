import os
import sys

import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class FakeMessage:
    def __init__(self, chat_id, user_id=None, text=None):
        self.chat = type("C", (), {"id": chat_id})
        self.from_user = type("U", (), {"id": user_id}) if user_id else None
        self.text = text
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)
        return text


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://x/y", True),
        ("http://x/y", True),
        ("x://y", False),
        ("abc", False),
        ("", False),
    ],
)
def test_is_http_url(url, expected):
    from app.sources.validation import is_http_url

    assert is_http_url(url) is expected


@pytest.mark.parametrize(
    "url,expected",
    [
        # direct audio containers
        ("https://cdn.example/song.mp3", True),
        ("https://cdn.example/song.m4a", True),
        ("https://cdn.example/audio.ogg", True),
        # HLS / DASH playlists are acceptable audio-like sources
        ("https://cdn.example/live/index.m3u8", True),
        ("https://cdn.example/dash/manifest.mpd", True),
        # known media pages (resolvable)
        ("https://www.youtube.com/watch?v=abc", True),
        ("https://youtu.be/abc", True),
        ("https://soundcloud.com/someone/track", True),
        ("https://music.apple.com/us/album/x/1", True),
        # known stream CDNs (typical yt-dlp output)
        ("https://rr2---sn-a5mekn7e.googlevideo.com/videoplayback", True),
        ("https://cf-hls-media.sndcdn.com/playlist.m3u8", True),
        ("https://d2xh.akamaized.net/media/x.mp3", True),
        # subdomain of a known media host
        ("https://embed.soundcloud.com/widget/x", True),
        # junk websites and unknown hosts
        ("https://techaistudy.com/course/py", False),
        ("https://example.com/", False),
        ("https://random-host.net/path", False),
        ("https://youtube.comm/evildomain", False),
        # non-http values are never "audio-like"
        ("abc123", False),
        ("/local/file.mp3", False),
    ],
)
def test_looks_like_audio(url, expected):
    from app.sources.validation import looks_like_audio

    assert looks_like_audio(url) is expected


def test_is_rejected_http_url_kills_website():
    from app.sources.validation import is_rejected_http_url

    assert is_rejected_http_url("https://techaistudy.com/notes") is True
    assert is_rejected_http_url("https://cdn.example/song.mp3") is False
    assert is_rejected_http_url("plain-id") is False


def test_validate_stream_url_pass_through_for_non_http():
    from app.sources.validation import validate_stream_url

    assert validate_stream_url("plain-id") == "plain-id"
    assert validate_stream_url("https://cdn.example/song.flac") == "https://cdn.example/song.flac"


def test_validate_stream_url_rejects_website():
    from app.sources.validation import validate_stream_url

    with pytest.raises(ValueError, match="refusing non-audio media URL"):
        validate_stream_url("https://techaistudy.com/")


@pytest.mark.asyncio
async def test_simple_provider_does_not_return_website_as_track():
    from app.sources.providers.simple_provider import SimpleProvider

    provider = SimpleProvider()
    results = await provider.search("https://techaistudy.com/course/py")
    assert results == []

    results = await provider.search("https://cdn.example/song.mp3")
    assert len(results) == 1
    assert results[0].source_url == "https://cdn.example/song.mp3"

    with pytest.raises(ValueError):
        await provider.resolve_audio("https://techaistudy.com/course/py")
    assert await provider.resolve_audio("https://cdn.example/song.mp3") == "https://cdn.example/song.mp3"


@pytest.mark.asyncio
async def test_resolve_track_rejects_website_input(monkeypatch):
    import app.bot.handlers.playback as playback_mod
    from app.bot.handlers.playback import _resolve_track

    # no real providers -> a website URL must be refused, not passed through
    monkeypatch.setattr(playback_mod, "get_default_providers", lambda: [])

    msg = FakeMessage(chat_id=1, user_id=10, text="/play https://techaistudy.com/course/py")
    client = type("C", (), {"player_manager": None, "voice": object()})

    with pytest.raises(ValueError, match="doesn't look like an audio source"):
        await _resolve_track(client, msg, "https://techaistudy.com/course/py")


@pytest.mark.asyncio
async def test_resolve_track_accepts_direct_audio_url(monkeypatch):
    import app.bot.handlers.playback as playback_mod
    from app.bot.handlers.playback import _resolve_track

    monkeypatch.setattr(playback_mod, "get_default_providers", lambda: [])

    msg = FakeMessage(chat_id=1, user_id=10, text="/play https://cdn.example/song.mp3")
    client = type("C", (), {"player_manager": None, "voice": object()})

    track, playable_url, *_ = await _resolve_track(client, msg, "https://cdn.example/song.mp3")
    assert playable_url == "https://cdn.example/song.mp3"
    assert track.source == "url"
    assert track.resolve_key == "https://cdn.example/song.mp3"


@pytest.mark.asyncio
async def test_resolve_playable_rejects_junk_fallback(monkeypatch):
    import app.sources as sources_mod
    from app.player.models import Track
    from app.sources.providers.simple_provider import SimpleProvider

    monkeypatch.setattr(sources_mod, "get_default_providers", lambda: [SimpleProvider()])

    with pytest.raises(ValueError):
        await sources_mod.resolve_playable(
            Track(id="junk", title="junk", source="url", source_url="https://techaistudy.com/")
        )

    url = await sources_mod.resolve_playable(
        Track(id="ok", title="ok", source="url", source_url="https://cdn.example/song.mp3")
    )
    assert url == "https://cdn.example/song.mp3"


@pytest.mark.asyncio
async def test_player_enqueue_gate_rejects_website_url():
    from app.player.manager import PlayerManager
    from app.player.models import Track

    class FakeEngine:
        async def wait_finished(self):
            return None

    class FakeVoice:
        async def play(self, chat_id, input_source, volume=1.0):
            return {"mode": "engine", "engine": FakeEngine()}

        async def stop_playback(self, chat_id):
            return

    mgr = PlayerManager(FakeVoice(), resolver=None)
    player = await mgr.get_player(1)

    bad = Track(id="junk", title="junk", source="url", source_url="https://techaistudy.com/course")
    with pytest.raises(ValueError):
        await player.enqueue(bad)

    good = Track(id="ok", title="ok", source="url", source_url="https://cdn.example/song.mp3")
    await player.enqueue(good)
    assert await player.queue.size() == 1

    # source!="url" tracks (searches, ids) are not run through the web gate
    search_track = Track(id="y1", title="y1", source="yt-dlp", source_url="http://example/1")
    await player.enqueue(search_track)
    assert await player.queue.size() == 2

    await player.shutdown()
