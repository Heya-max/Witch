from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.db.models import Base, QueueEntry
from app.player.models import Track
from app.player.queue import Queue, QueueFullError, purge_stale_persisted_entries
from sqlalchemy import text
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
async def test_in_memory_queue_max_size():
    q = Queue(max_size=2)
    assert await q.add(Track(id="a", title="A")) == 0
    assert await q.add(Track(id="b", title="B")) == 1
    with pytest.raises(QueueFullError):
        await q.add(Track(id="c", title="C"))
    with pytest.raises(QueueFullError):
        await q.add_next(Track(id="d", title="D"))


@pytest.mark.asyncio
async def test_persisted_queue_max_size(session_factory):
    q = Queue(session_factory=session_factory, chat_id=3, max_size=2)
    assert await q.add(Track(id="a", title="A")) == 0
    assert await q.add(Track(id="b", title="B")) == 1
    with pytest.raises(QueueFullError):
        await q.add(Track(id="c", title="C"))


@pytest.mark.asyncio
async def test_uncapped_queue_by_default():
    q = Queue()
    for i in range(5):
        await q.add(Track(id=f"t{i}", title=f"T{i}"))
    assert await q.size() == 5


@pytest.mark.asyncio
async def test_queue_full_error_carries_limits():
    err = QueueFullError(7, 25)
    assert err.limit == 25
    assert err.chat_id == 7
    assert "full" in str(err)


@pytest.mark.asyncio
async def test_purge_stale_persisted_entries(session_factory):
    async with session_factory() as s:
        s.add(
            QueueEntry(
                chat_id=9,
                track_id="old",
                title="Old",
                position=0,
                created_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
        s.add(QueueEntry(chat_id=9, track_id="new", title="New", position=1))
        await s.commit()

    removed = await purge_stale_persisted_entries(session_factory, 86400)
    assert removed == 1

    q = Queue(session_factory=session_factory, chat_id=9)
    assert [t.id for t in await q.list()] == ["new"]


@pytest.mark.asyncio
async def test_purge_disabled_when_ttl_zero(session_factory):
    async with session_factory() as s:
        s.add(
            QueueEntry(
                chat_id=9,
                track_id="old",
                title="Old",
                position=0,
                created_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
        await s.commit()

    assert await purge_stale_persisted_entries(session_factory, 0) == 0
    q = Queue(session_factory=session_factory, chat_id=9)
    assert await q.size() == 1


@pytest.mark.asyncio
async def test_corrupt_payload_rows_are_skipped_and_deleted(session_factory):
    async with session_factory() as s:
        s.add(
            QueueEntry(
                chat_id=5,
                track_id="bad",
                title="Bad",
                position=0,
                payload={"no": "id", "title": "Not a track"},
            )
        )
        s.add(QueueEntry(chat_id=5, track_id="", title="Empty id", position=1))
        s.add(
            QueueEntry(
                chat_id=5,
                track_id="good",
                title="Good",
                position=2,
                payload={"id": "good", "title": "Good"},
            )
        )
        await s.commit()

    q = Queue(session_factory=session_factory, chat_id=5)
    # only the clean row surfaces; the corrupt rows are deleted on read
    assert [t.id for t in await q.list()] == ["good"]
    assert await q.size() == 1

    async with session_factory() as s:
        remaining = await s.execute(text("SELECT count(*) FROM queue_entries WHERE chat_id = 5"))
        assert remaining.scalar_one() == 1


@pytest.mark.asyncio
async def test_pop_next_skips_poisoned_front_rows(session_factory):
    async with session_factory() as s:
        s.add(QueueEntry(chat_id=6, track_id="bad", title="Bad", position=0, payload={"junk": True}))
        s.add(
            QueueEntry(
                chat_id=6,
                track_id="good",
                title="Good",
                position=1,
                payload={"id": "good", "title": "Good"},
            )
        )
        await s.commit()

    q = Queue(session_factory=session_factory, chat_id=6)
    first = await q.pop_next()
    assert first is not None
    assert first.id == "good"
    assert await q.pop_next() is None


@pytest.mark.asyncio
async def test_legacy_rows_without_payload_reconstruct(session_factory):
    # rows written before the Track.payload column was populated must still work
    async with session_factory() as s:
        s.add(QueueEntry(chat_id=8, track_id="legacy", title="Legacy song", position=0, requested_by=11))
        await s.commit()

    q = Queue(session_factory=session_factory, chat_id=8)
    tracks = await q.list()
    assert len(tracks) == 1
    assert tracks[0].id == "legacy"
    assert tracks[0].title == "Legacy song"
    assert tracks[0].requested_by == 11
