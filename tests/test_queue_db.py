import pytest
import pytest_asyncio
from app.db.models import Base
from app.player.models import Track
from app.player.queue import Queue
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest_asyncio.fixture
async def session_factory():
    # Single shared in-memory SQLite DB across sessions
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_db_queue_add_pop_and_persist_metadata(session_factory):
    q = Queue(session_factory=session_factory, chat_id=42)
    t1 = Track(
        id="yt:abc",
        title="First Song",
        artist="Artist A",
        duration=210,
        source="yt-dlp",
        source_url="http://a/1",
        requested_by=7,
    )
    t2 = Track(id="yt:def", title="Second Song", source="yt-dlp", source_url="http://a/2")

    assert await q.add(t1) == 0
    assert await q.add(t2) == 1
    assert await q.size() == 2

    # a second Queue instance shares the same DB rows (persistence)
    q2 = Queue(session_factory=session_factory, chat_id=42)
    assert await q2.size() == 2

    first = await q.pop_next()
    assert first.id == "yt:abc"
    assert first.title == "First Song"
    assert first.artist == "Artist A"
    assert first.duration == 210
    assert first.requested_by == 7

    second = await q2.pop_next()
    assert second.id == "yt:def"
    assert await q2.pop_next() is None


@pytest.mark.asyncio
async def test_db_queue_order_operations(session_factory):
    q = Queue(session_factory=session_factory, chat_id=1)
    for i in range(4):
        await q.add(Track(id=f"t{i}", title=f"Track {i}"))

    # add_next puts an item at the front
    await q.add_next(Track(id="front", title="Front"))
    ids = [t.id for t in await q.list()]
    assert ids == ["front", "t0", "t1", "t2", "t3"]

    # move(1, 3): remove item at index 1 and insert at index 3 (shift semantics)
    assert await q.move(1, 3) is True
    ids = [t.id for t in await q.list()]
    assert ids == ["front", "t1", "t2", "t0", "t3"]

    # remove(position)
    removed = await q.remove(2)
    assert removed.id == "t2"
    ids = [t.id for t in await q.list()]
    assert ids == ["front", "t1", "t0", "t3"]

    # get_current / get_next
    assert (await q.get_current()).id == "front"
    assert (await q.get_next()).id == "t1"

    # clear
    await q.clear()
    assert await q.size() == 0
    assert await q.list() == []


@pytest.mark.asyncio
async def test_db_queue_shuffle(session_factory):
    q = Queue(session_factory=session_factory, chat_id=3)
    expected = {f"t{i}" for i in range(6)}
    for i in range(6):
        await q.add(Track(id=f"t{i}", title=f"Track {i}"))

    await q.shuffle()
    actual = {t.id for t in await q.list()}
    assert actual == expected
