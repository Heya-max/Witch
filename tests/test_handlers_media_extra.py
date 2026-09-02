"""Coverage for the remaining untested media.py paths (downloads, lyrics)."""

import app.bot.handlers.media as media_mod
import pytest
from app.player.models import Track


class FakeMessage:
    def __init__(self, chat_id=1, user_id=7, text=""):
        self.chat = type("C", (), {"id": chat_id})
        self.from_user = type("U", (), {"id": user_id}) if user_id else None
        self.text = text
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)
        return text


class FakeClient:
    def __init__(self, player_manager=None):
        self.sent = []
        self.player_manager = player_manager

    async def send_audio(self, chat_id, file, **kwargs):
        self.sent.append((chat_id, file, kwargs))


class BrokenPM:
    async def get_player(self, chat_id):
        raise RuntimeError("boom")


def test_audio_caption_includes_source():
    track = Track(id="x", title="Song", artist="Artist", source="yt-dlp")
    caption = media_mod._audio_caption(track)
    assert "Source: yt-dlp" in caption


def test_downloader_lazily_creates_instance(monkeypatch):
    class FakeYtDlpProvider:
        pass

    monkeypatch.setattr(media_mod, "YtDlpProvider", FakeYtDlpProvider)
    monkeypatch.setattr(media_mod, "_downloader_instance", None)
    first = media_mod._downloader()
    assert isinstance(first, FakeYtDlpProvider)
    assert media_mod._downloader() is first
    assert media_mod._downloader_instance is first


@pytest.mark.asyncio
async def test_lyrics_usage_without_query_or_track():
    msg = FakeMessage(text="/lyrics")
    await media_mod.lyrics_handler(FakeClient(), msg)
    assert any("Usage: /lyrics" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_lyrics_ignores_broken_current_track(monkeypatch):
    calls = []

    async def fake_fetch(artist, title):
        calls.append((artist, title))
        return "some lyrics"

    monkeypatch.setattr(media_mod, "fetch_lyrics", fake_fetch)
    client = FakeClient(player_manager=BrokenPM())
    msg = FakeMessage(text="/lyrics")
    await media_mod.lyrics_handler(client, msg)
    assert calls == []
    assert any("Usage: /lyrics" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_lyrics_surfaces_fetch_error(monkeypatch):
    async def failing_fetch(artist, title):
        raise RuntimeError("api down")

    monkeypatch.setattr(media_mod, "fetch_lyrics", failing_fetch)
    msg = FakeMessage(text="/lyrics Artist - Song")
    await media_mod.lyrics_handler(FakeClient(), msg)
    assert any("Couldn't fetch lyrics" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_lyrics_truncates_long_text(monkeypatch):
    async def long_fetch(artist, title):
        return "x" * 4000

    monkeypatch.setattr(media_mod, "fetch_lyrics", long_fetch)
    msg = FakeMessage(text="/lyrics Artist - Song")
    await media_mod.lyrics_handler(FakeClient(), msg)
    assert any("…" in r and len(r) < 4000 for r in msg.replies)


@pytest.mark.asyncio
async def test_fetch_lyrics_caches_network_error(monkeypatch):
    class BoomClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            raise OSError("network down")

    monkeypatch.setattr(media_mod.httpx, "AsyncClient", BoomClient)
    fresh: dict = {}
    monkeypatch.setattr(media_mod, "_lyrics_cache", fresh)
    assert await media_mod.fetch_lyrics("A", "B") is None
    assert await media_mod.fetch_lyrics("A", "B") is None


@pytest.mark.asyncio
async def test_download_user_rate_limited():
    class UserLimited:
        async def allow(self, key, limit, period):
            return "user:7" not in key  # deny the user key

    client = FakeClient(player_manager=None)
    client.rate_limiter = UserLimited()
    msg = FakeMessage(text="/download something")
    await media_mod.download_handler(client, msg)
    assert any("too quickly" in r.lower() for r in msg.replies)


@pytest.mark.asyncio
async def test_download_chat_rate_limited():
    class ChatLimited:
        async def allow(self, key, limit, period):
            return "chat:1" not in key  # deny the chat key

    client = FakeClient(player_manager=None)
    client.rate_limiter = ChatLimited()
    msg = FakeMessage(text="/download something")
    await media_mod.download_handler(client, msg)
    assert any("rate-limited" in r.lower() for r in msg.replies)


@pytest.mark.asyncio
async def test_download_stops_when_picker_shown(monkeypatch):
    delivered = []

    async def fake_resolve(client, message, source):
        track = Track(id="a", title="Song")
        return track, "http://audio/x", None, [track]

    async def fake_picker(client, message, chat_id, provider, results, **kw):
        return True

    async def fake_deliver(client, chat_id, track):
        delivered.append(chat_id)

    monkeypatch.setattr(media_mod, "_resolve_track", fake_resolve)
    monkeypatch.setattr(media_mod, "_offer_picker", fake_picker)
    monkeypatch.setattr(media_mod, "deliver_audio", fake_deliver)

    msg = FakeMessage(text="/download something")
    await media_mod.download_handler(FakeClient(), msg)
    assert delivered == []
    assert not any("Download sent" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_download_ignores_rate_limiter_failure(monkeypatch):
    class FlakyLimiter:
        async def allow(self, key, limit, period):
            raise RuntimeError("redis down")

    delivered = []

    async def fake_resolve(client, message, source):
        track = Track(id="a", title="Song")
        return track, "http://audio/x", None, [track]

    async def fake_deliver(client, chat_id, track):
        delivered.append(chat_id)

    monkeypatch.setattr(media_mod, "_resolve_track", fake_resolve)
    monkeypatch.setattr(media_mod, "deliver_audio", fake_deliver)

    client = FakeClient()
    client.rate_limiter = FlakyLimiter()
    msg = FakeMessage(text="/download something")
    await media_mod.download_handler(client, msg)
    assert delivered == [1]
    assert any("Download sent" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_download_surfaces_value_error(monkeypatch):
    async def refusing_resolve(client, message, source):
        raise ValueError("That doesn't look like an audio source")

    monkeypatch.setattr(media_mod, "_resolve_track", refusing_resolve)
    msg = FakeMessage(text="/download https://junk.example/page")
    await media_mod.download_handler(FakeClient(), msg)
    assert any("audio source" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_download_surfaces_generic_error(monkeypatch):
    async def broken_resolve(client, message, source):
        raise RuntimeError("resolve down")

    monkeypatch.setattr(media_mod, "_resolve_track", broken_resolve)
    msg = FakeMessage(text="/download something")
    await media_mod.download_handler(FakeClient(), msg)
    assert any("Download failed" in r for r in msg.replies)
