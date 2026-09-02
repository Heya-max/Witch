"""Coverage for the untested favorites.py handler paths."""

import app.bot.handlers.favorites as fav_mod
import pytest
import pytest_asyncio
from app.db.models import Base
from app.player.models import Track
from app.services.favorites import add_favorite, list_favorites
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield factory
    await engine.dispose()


def _track(track_id: str, title: str, source="yt-dlp", source_url=None):
    return Track(
        id=track_id,
        title=title,
        source=source,
        source_url=source_url or f"http://audio/{track_id}",
    )


class FakeMessage:
    def __init__(self, chat_id=1, user_id=None, text="/favs"):
        self.chat = type("C", (), {"id": chat_id})
        self.from_user = type("U", (), {"id": user_id}) if user_id else None
        self.text = text
        self.replies = []
        self.reply_markup = None

    async def reply_text(self, text, reply_markup=None):
        self.replies.append(text)
        self.reply_markup = reply_markup
        return text


class FakeEditedMessage:
    def __init__(self, chat_id=1):
        self.chat = type("C", (), {"id": chat_id})
        self.edited = None
        self.markup = None

    async def edit_text(self, text, reply_markup=None):
        self.edited = text
        self.markup = reply_markup
        return text


_MISSING = object()


class FakeQuery:
    def __init__(self, data, chat_id=1, message=_MISSING, user_id=7):
        self.data = data
        self.message = FakeEditedMessage(chat_id) if message is _MISSING else message
        self.from_user = type("U", (), {"id": user_id}) if user_id else None
        self.answered = None
        self.alerted = None

    async def answer(self, text=None, show_alert=False):
        self.answered = text
        self.alerted = show_alert


def _client(factory=None, player_manager=None):
    attrs = {"player_manager": player_manager}
    if factory is not None:
        attrs["db_session_factory"] = factory
    return type("C", (), attrs)


@pytest.mark.asyncio
async def test_render_favs_without_factory():
    msg = FakeMessage(user_id=7)
    await fav_mod.favs_handler(_client(), msg)
    assert any("temporarily unavailable" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_fav_usage_error():
    msg = FakeMessage(user_id=7, text="/fav")
    await fav_mod.fav_handler(_client(), msg)
    assert any("Usage: /fav" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_fav_anonymous_rejected():
    msg = FakeMessage(text="/fav something")
    await fav_mod.fav_handler(_client(), msg)
    assert any("Users only" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_fav_raises_value_error(monkeypatch, session_factory):
    async def refusal(client, message, input_source):
        raise ValueError("That doesn't look like an audio source")

    monkeypatch.setattr(fav_mod, "_resolve_track", refusal)
    msg = FakeMessage(user_id=7, text="/fav https://junk.example/page")
    await fav_mod.fav_handler(_client(session_factory), msg)
    assert any("audio source" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_fav_surfaces_generic_error(monkeypatch, session_factory):
    async def broken(client, message, input_source):
        raise RuntimeError("search down")

    monkeypatch.setattr(fav_mod, "_resolve_track", broken)
    msg = FakeMessage(user_id=7, text="/fav query")
    await fav_mod.fav_handler(_client(session_factory), msg)
    assert any("Could not save favorite" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_favs_anonymous_rejected():
    msg = FakeMessage()
    await fav_mod.favs_handler(_client(), msg)
    assert any("Users only" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_unfav_usage_error():
    msg = FakeMessage(user_id=7, text="/unfav")
    await fav_mod.unfav_handler(_client(), msg)
    assert any("Usage: /unfav" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_unfav_anonymous_rejected():
    msg = FakeMessage(text="/unfav 1")
    await fav_mod.unfav_handler(_client(), msg)
    assert any("Users only" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_unfav_without_factory():
    msg = FakeMessage(user_id=7, text="/unfav 1")
    await fav_mod.unfav_handler(_client(), msg)
    assert any("temporarily unavailable" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_unfav_removes_success(session_factory):
    await add_favorite(session_factory, 7, _track("a", "Alpha"))
    msg = FakeMessage(user_id=7, text="/unfav 1")
    await fav_mod.unfav_handler(_client(session_factory), msg)
    assert any("Removed favorite" in r for r in msg.replies)
    assert await list_favorites(session_factory, 7) == []


@pytest.mark.asyncio
async def test_resolve_fav_playable_ytdlp_success(monkeypatch):
    class FakeProvider:
        async def resolve_audio(self, ref):
            return "http://audio/m3u8"

    monkeypatch.setattr(fav_mod, "YtDlpProvider", FakeProvider)
    track = _track("x", "X", source="yt-dlp", source_url="http://sc/x")
    assert await fav_mod._resolve_fav_playable(track) == "http://audio/m3u8"


@pytest.mark.asyncio
async def test_resolve_fav_playable_ytdlp_error(monkeypatch):
    class FailingProvider:
        async def resolve_audio(self, ref):
            raise RuntimeError("resolve down")

    monkeypatch.setattr(fav_mod, "YtDlpProvider", FailingProvider)
    track = _track("x", "X", source="yt-dlp", source_url="http://sc/x")
    assert await fav_mod._resolve_fav_playable(track) is None


@pytest.mark.asyncio
async def test_resolve_fav_playable_plain_source():
    track = _track("x", "X", source="simple", source_url="http://a/1")
    assert await fav_mod._resolve_fav_playable(track) == "http://a/1"


@pytest.mark.asyncio
async def test_fav_callback_invalid_selection(session_factory):
    q = FakeQuery("fav", user_id=7)
    await fav_mod.fav_callback(_client(session_factory), q)
    assert q.answered == "Invalid selection."


@pytest.mark.asyncio
async def test_fav_callback_bytes_data(session_factory):
    await add_favorite(session_factory, 7, _track("a", "Alpha", source="simple"))
    q = FakeQuery(b"fav:z:0", user_id=7)
    await fav_mod.fav_callback(_client(session_factory), q)
    assert q.answered is None and q.alerted is False


@pytest.mark.asyncio
async def test_fav_callback_anonymous_rejected(session_factory):
    q = FakeQuery("fav:p:0", user_id=None)
    await fav_mod.fav_callback(_client(session_factory), q)
    assert q.answered == "Not allowed."


@pytest.mark.asyncio
async def test_fav_callback_without_factory():
    q = FakeQuery("fav:p:0", user_id=7)
    await fav_mod.fav_callback(_client(), q)
    assert q.answered == "Database unavailable."


@pytest.mark.asyncio
async def test_fav_callback_out_of_range(session_factory):
    q = FakeQuery("fav:p:0", user_id=7)
    await fav_mod.fav_callback(_client(session_factory), q)
    assert q.answered == "No favorite at that position."


@pytest.mark.asyncio
async def test_fav_callback_play_without_message(session_factory):
    await add_favorite(session_factory, 7, _track("a", "Alpha", source="simple"))
    q = FakeQuery("fav:p:0", user_id=7, message=None)
    await fav_mod.fav_callback(_client(session_factory), q)
    assert q.answered == "Cannot play from here."


@pytest.mark.asyncio
async def test_fav_callback_play_unresolvable(monkeypatch, session_factory):
    await add_favorite(session_factory, 7, _track("a", "Alpha", source="yt-dlp"))

    class FailingProvider:
        async def resolve_audio(self, ref):
            raise RuntimeError("resolve down")

    monkeypatch.setattr(fav_mod, "YtDlpProvider", FailingProvider)
    q = FakeQuery("fav:p:0", user_id=7)
    await fav_mod.fav_callback(_client(session_factory), q)
    assert q.answered == "Could not resolve audio for this track."


@pytest.mark.asyncio
async def test_fav_callback_play_without_player_manager(session_factory):
    await add_favorite(session_factory, 7, _track("a", "Alpha", source="simple"))
    q = FakeQuery("fav:p:0", user_id=7)
    await fav_mod.fav_callback(_client(session_factory), q)
    assert q.answered == "Playback not configured."


@pytest.mark.asyncio
async def test_fav_callback_enqueue_failure(session_factory):
    await add_favorite(session_factory, 7, _track("a", "Alpha", source="simple"))

    class BrokenPM:
        async def get_player(self, chat_id):
            raise RuntimeError("boom")

    q = FakeQuery("fav:p:0", user_id=7)
    await fav_mod.fav_callback(_client(session_factory, player_manager=BrokenPM()), q)
    assert q.answered == "Failed to start playback. Try again later."


@pytest.mark.asyncio
async def test_fav_callback_unknown_kind(session_factory):
    await add_favorite(session_factory, 7, _track("a", "Alpha", source="simple"))
    q = FakeQuery("fav:x:0", user_id=7)
    await fav_mod.fav_callback(_client(session_factory), q)
    assert q.answered is None and q.alerted is False


@pytest.mark.asyncio
async def test_re_render_topics_without_factory():
    msg = FakeEditedMessage(chat_id=1)
    await fav_mod._re_render_topics(_client(), msg, 7)
    assert msg.edited is None


@pytest.mark.asyncio
async def test_re_render_topics_when_empty(session_factory):
    await add_favorite(session_factory, 7, _track("a", "Alpha"))
    msg = FakeEditedMessage(chat_id=1)
    q = FakeQuery("fav:r:0", user_id=7, message=msg)
    await fav_mod.fav_callback(_client(session_factory), q)
    assert "No favorites yet" in msg.edited


def test_fav_filter_matches():
    assert fav_mod._fav_filter(None, None, FakeQuery("fav:p:0")) is True
    assert fav_mod._fav_filter(None, None, FakeQuery(b"fav:r:0")) is True
    assert fav_mod._fav_filter(None, None, FakeQuery("other")) is False


def test_fav_register_adds_handlers():
    added = []

    class FakeApp:
        def add_handler(self, handler):
            added.append(handler)

    fav_mod.register(FakeApp())
    assert len(added) == 4
    assert all(getattr(h, "callback", None) or getattr(h, "handler", None) for h in added)
