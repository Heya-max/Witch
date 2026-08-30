import os
import sys

import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class FakeMessage:
    def __init__(self, chat_id=1, user_id=7, text=""):
        self.chat = type("C", (), {"id": chat_id})()
        self.from_user = type("U", (), {"id": user_id})()
        self.text = text
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)
        return text


class FakeClient:
    def __init__(self):
        self.sent = []

    async def send_audio(self, chat_id, file, **kwargs):
        self.sent.append((chat_id, file, kwargs, os.path.exists(file)))


class FakePlayer:
    def __init__(self, current):
        self.current = current


class FakePlayerManager:
    def __init__(self, player):
        self._player = player

    async def get_player(self, chat_id):
        return self._player


class FakeDownloader:
    def __init__(self, size=16, name="song.m4a"):
        self.size = size
        self.name = name

    def download(self, source, dest_dir, max_filesize=50 * 1024 * 1024):
        path = os.path.join(dest_dir, self.name)
        with open(path, "wb") as fh:
            fh.write(b"\0" * self.size)
        return path


@pytest.mark.asyncio
async def test_download_handler_sends_audio(monkeypatch):
    import app.bot.handlers.media as media_mod
    from app.player.models import Track

    track = Track(id="abc", title="Test Song", artist="Artist", source="yt-dlp")
    delivered = []

    async def fake_resolve(client, message, source):
        return track, "http://audio/x", None, [track]

    async def fake_deliver(client, chat_id, t):
        delivered.append((chat_id, t))

    monkeypatch.setattr(media_mod, "_resolve_track", fake_resolve)
    monkeypatch.setattr(media_mod, "deliver_audio", fake_deliver)

    client = FakeClient()
    msg = FakeMessage(text="/download test song")
    await media_mod.download_handler(client, msg)

    assert delivered
    assert delivered[0][0] == 1
    assert delivered[0][1].title == "Test Song"
    assert any("Download sent" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_download_handler_requires_query(monkeypatch):
    import app.bot.handlers.media as media_mod

    called = []

    async def fake_deliver(client, chat_id, t):
        called.append(1)

    monkeypatch.setattr(media_mod, "deliver_audio", fake_deliver)
    client = FakeClient()
    msg = FakeMessage(text="/download")
    await media_mod.download_handler(client, msg)
    assert not called
    assert any("Usage" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_deliver_audio_sends_audio_message(monkeypatch):
    import app.bot.handlers.media as media_mod
    from app.player.models import Track

    def fake_downloader():
        return FakeDownloader()

    monkeypatch.setattr(media_mod, "MAX_AUDIO_SIZE", 1024 * 1024)
    monkeypatch.setattr(media_mod, "_downloader", fake_downloader)

    track = Track(id="x", title="Nice Track", artist="A B", duration=90)
    client = FakeClient()
    await media_mod.deliver_audio(client, 123, track)
    assert len(client.sent) == 1
    chat_id, path, kwargs, existed = client.sent[0]
    assert chat_id == 123
    assert existed
    assert kwargs["title"] == "Nice Track"
    assert kwargs["performer"] == "A B"


@pytest.mark.asyncio
async def test_deliver_audio_raises_when_too_big(monkeypatch):
    import app.bot.handlers.media as media_mod
    from app.player.models import Track

    def fake_downloader():
        return FakeDownloader(size=64)

    monkeypatch.setattr(media_mod, "MAX_AUDIO_SIZE", 32)
    monkeypatch.setattr(media_mod, "_downloader", fake_downloader)

    track = Track(id="x", title="Big", artist="B")
    client = FakeClient()
    with pytest.raises(ValueError):
        await media_mod.deliver_audio(client, 1, track)
    assert not client.sent


@pytest.mark.asyncio
async def test_lyrics_handler_formats_query(monkeypatch):
    import app.bot.handlers.media as media_mod

    calls = []

    async def fake_fetch(artist, title):
        calls.append((artist, title))
        return "Is this the real life?\nIs this just fantasy?"

    monkeypatch.setattr(media_mod, "fetch_lyrics", fake_fetch)

    client = FakeClient()
    msg = FakeMessage(text="/lyrics Queen - Bohemian Rhapsody")
    await media_mod.lyrics_handler(client, msg)
    assert calls == [("Queen", "Bohemian Rhapsody")]
    assert any("Queen" in r and "Bohemian Rhapsody" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_lyrics_handler_uses_current_track(monkeypatch):
    import app.bot.handlers.media as media_mod
    from app.player.models import Track

    calls = []

    async def fake_fetch(artist, title):
        calls.append((artist, title))
        return "lyrics here"

    monkeypatch.setattr(media_mod, "fetch_lyrics", fake_fetch)

    current = Track(id="1", title="Running Up That Hill", artist="Kate Bush")
    client = FakeClient()
    client.player_manager = FakePlayerManager(FakePlayer(current))
    msg = FakeMessage(text="/lyrics")
    await media_mod.lyrics_handler(client, msg)
    assert calls == [("Kate Bush", "Running Up That Hill")]


@pytest.mark.asyncio
async def test_lyrics_handler_reports_not_found(monkeypatch):
    import app.bot.handlers.media as media_mod

    async def fake_fetch(artist, title):
        return None

    monkeypatch.setattr(media_mod, "fetch_lyrics", fake_fetch)
    client = FakeClient()
    msg = FakeMessage(text="/lyrics No One - This Song")
    await media_mod.lyrics_handler(client, msg)
    assert any("No lyrics" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_fetch_lyrics_caches_results(monkeypatch):
    import app.bot.handlers.media as media_mod

    calls = {"n": 0}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"lyrics": "hello world"}

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            calls["n"] += 1
            return FakeResponse()

    monkeypatch.setattr(media_mod.httpx, "AsyncClient", FakeAsyncClient)
    first = await media_mod.fetch_lyrics("A", "B")
    second = await media_mod.fetch_lyrics("A", "B")
    assert first == "hello world"
    assert second == "hello world"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_fetch_lyrics_caches_miss_without_artist(monkeypatch):
    import app.bot.handlers.media as media_mod

    class BoomClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            raise AssertionError("no artist means no API call")

    monkeypatch.setattr(media_mod.httpx, "AsyncClient", BoomClient)
    assert await media_mod.fetch_lyrics(None, "Only A Title") is None


def test_register_adds_lyrics_and_download_handlers(monkeypatch):
    import app.bot.handlers.media as media_mod

    added = []

    class FakeApp:
        def add_handler(self, handler, *args, **kwargs):
            added.append(handler)

    media_mod.register(FakeApp())
    assert added
    assert all(callable(h.callback) for h in added)
