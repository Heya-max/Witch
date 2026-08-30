import pytest
import pytest_asyncio
from app.db.models import Base
from app.player.models import Track
from app.services.favorites import (
    add_favorite,
    list_favorites,
    remove_favorite,
)
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
        artist="Artist",
        duration=180,
        source=source,
        source_url=source_url or f"http://audio/{track_id}",
        metadata={"x": {1: "unserialisable-ish"}},
    )


@pytest.mark.asyncio
async def test_add_list_remove_favorites(session_factory):
    user = 7
    assert await add_favorite(session_factory, user, _track("a", "Alpha")) is True
    assert await add_favorite(session_factory, user, _track("b", "Beta")) is True
    assert await add_favorite(session_factory, user, _track("a", "Alpha again")) is False  # dedupe by id

    tracks = await list_favorites(session_factory, user)
    assert len(tracks) == 2
    titles = {t.title for t in tracks}
    assert titles == {"Alpha", "Beta"}
    # metadata is dropped from the stored payload
    assert all(t.metadata is None for t in tracks)


@pytest.mark.asyncio
async def test_favorites_per_user(session_factory):
    await add_favorite(session_factory, 1, _track("a", "Song A"))
    await add_favorite(session_factory, 2, _track("b", "Song B"))
    assert [t.title for t in await list_favorites(session_factory, 1)] == ["Song A"]
    assert [t.title for t in await list_favorites(session_factory, 2)] == ["Song B"]


@pytest.mark.asyncio
async def test_remove_favorite_index(session_factory):
    user = 3
    await add_favorite(session_factory, user, _track("a", "Alpha"))
    await add_favorite(session_factory, user, _track("b", "Beta"))
    # newest first: index 0 is "b"
    assert await remove_favorite(session_factory, user, 0) is True
    assert [t.id for t in await list_favorites(session_factory, user)] == ["a"]
    assert await remove_favorite(session_factory, user, 0) is True
    assert await list_favorites(session_factory, user) == []
    assert await remove_favorite(session_factory, user, 5) is False


class FakeMessage:
    def __init__(self, chat_id, user_id=None, text="/favs"):
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
    def __init__(self, chat_id=9):
        self.chat = type("C", (), {"id": chat_id})
        self.edited = None
        self.markup = None

    async def edit_text(self, text, reply_markup=None):
        self.edited = text
        self.markup = reply_markup
        return text


class FakeQuery:
    def __init__(self, data, chat_id=9, message=None, user_id=None):
        self.data = data
        self.answered = None
        self.message = message or FakeEditedMessage(chat_id)
        self.from_user = type("U", (), {"id": user_id}) if user_id else None

    async def answer(self, text=None, show_alert=False):
        self.answered = text
        self.alerted = show_alert


def _client(factory):
    return type("C", (), {"db_session_factory": factory})


@pytest.mark.asyncio
async def test_favs_empty_returns_hint(session_factory):
    from app.bot.handlers.favorites import favs_handler

    msg = FakeMessage(chat_id=1, user_id=7)
    await favs_handler(_client(session_factory), msg)
    assert any("No favorites yet" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_favs_lists_with_buttons(session_factory):
    from app.bot.handlers.favorites import favs_handler

    user = 7
    await add_favorite(session_factory, user, _track("a", "Alpha"))
    await add_favorite(session_factory, user, _track("b", "Beta"))

    msg = FakeMessage(chat_id=1, user_id=user)
    await favs_handler(_client(session_factory), msg)
    text = msg.replies[0]
    assert "1. Beta" in text and "2. Alpha" in text
    assert msg.reply_markup is not None
    buttons = [b.callback_data for row in msg.reply_markup.inline_keyboard for b in row]
    assert buttons == ["fav:p:0", "fav:r:0", "fav:p:1", "fav:r:1"]


@pytest.mark.asyncio
async def test_fav_needs_session_factory():
    from app.bot.handlers.favorites import fav_handler

    client = type("C", (), {})
    msg = FakeMessage(chat_id=1, user_id=7, text="/fav some song")
    await fav_handler(client, msg)
    assert any("Database is unavailable" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_fav_saves_first_result(monkeypatch, session_factory):
    import app.bot.handlers.playback as pb
    from app.bot.handlers.favorites import fav_handler
    from app.player.models import Track

    provider_results = [Track(id="abc", title="Saved Song", source="yt-dlp", source_url="http://a/1")]

    class FakeProvider:
        async def search(self, query):
            return list(provider_results)

        async def resolve_audio(self, ref):
            return "http://audio/abc"

    monkeypatch.setattr(pb, "get_default_providers", lambda: [FakeProvider()])

    msg = FakeMessage(chat_id=1, user_id=7, text="/fav query")
    await fav_handler(_client(session_factory), msg)
    assert any("Added to favorites" in r for r in msg.replies)
    tracks = await list_favorites(session_factory, 7)
    assert tracks and tracks[0].id == "abc"

    # second time -> already saved, dedupe
    msg2 = FakeMessage(chat_id=1, user_id=7, text="/fav query")
    await fav_handler(_client(session_factory), msg2)
    assert any("Already in favorites" in r for r in msg2.replies)


@pytest.mark.asyncio
async def test_unfav_invalid_number(session_factory):
    from app.bot.handlers.favorites import unfav_handler

    msg = FakeMessage(chat_id=1, user_id=7, text="/unfav notnum")
    await unfav_handler(_client(session_factory), msg)
    assert any("isn't a valid number" in r for r in msg.replies)

    msg2 = FakeMessage(chat_id=1, user_id=7, text="/unfav 99")
    await unfav_handler(_client(session_factory), msg2)
    assert any("No favorite at that position" in r for r in msg2.replies)


@pytest.mark.asyncio
async def test_fav_callback_remove(monkeypatch, session_factory):
    from app.bot.handlers.favorites import fav_callback

    user = 7
    await add_favorite(session_factory, user, _track("a", "Alpha"))
    await add_favorite(session_factory, user, _track("b", "Beta"))

    message = FakeEditedMessage(chat_id=1)
    query = FakeQuery("fav:r:0", chat_id=1, message=message, user_id=user)
    await fav_callback(_client(session_factory), query)
    assert query.answered == "Removed."
    assert [t.id for t in await list_favorites(session_factory, user)] == ["a"]
    assert "1. Alpha" in message.edited


@pytest.mark.asyncio
async def test_fav_callback_play(monkeypatch, session_factory):
    from app.bot.handlers.favorites import fav_callback

    user = 7
    await add_favorite(session_factory, user, _track("a", "Alpha", source="simple", source_url="http://a/1"))

    class FakePlayer:
        def __init__(self):
            self.enqueued = []

        async def enqueue(self, track):
            self.enqueued.append(track)
            return len(self.enqueued)

    class FakePM:
        def __init__(self):
            self.player = FakePlayer()

        async def get_player(self, chat_id):
            return self.player

    pm = FakePM()
    client = type("C", (), {"db_session_factory": session_factory, "player_manager": pm})
    message = FakeEditedMessage(chat_id=1)
    query = FakeQuery("fav:p:0", chat_id=1, message=message, user_id=user)
    await fav_callback(client, query)
    assert pm.player.enqueued
    assert pm.player.enqueued[0].title == "Alpha"
    assert message.edited and "Alpha" in message.edited
