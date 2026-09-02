"""Edge-path coverage: in-memory queue operations and corrupt-row/DB branches."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.db.models import Base, QueueEntry
from app.player.models import Track
from app.player.queue import Queue, QueueFullError
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


@pytest.mark.asyncio
async def test_in_memory_remove_paths():
    q = Queue(chat_id=1)
    for i in range(3):
        await q.add(Track(id=f"t{i}", title=f"Track {i}"))

    assert (await q.remove(1)).id == "t1"
    assert [t.id for t in await q.list()] == ["t0", "t2"]
    assert (await q.remove(0)).id == "t0"
    assert [t.id for t in await q.list()] == ["t2"]
    assert (await q.remove(0)).id == "t2"
    assert (await q.remove(0)) is None
    assert (await q.remove(-1)) is None


@pytest.mark.asyncio
async def test_in_memory_shuffle_getters_capacity():
    q = Queue(chat_id=2)
    for i in range(6):
        await q.add(Track(id=f"t{i}", title=f"Track {i}"))

    await q.shuffle()
    assert {t.id for t in await q.list()} == {f"t{i}" for i in range(6)}
    assert len(await q.list()) == 6

    assert (await q.get_current()).id in {"t0", "t1", "t2", "t3", "t4", "t5"}
    assert (await q.get_next()).id in {"t0", "t1", "t2", "t3", "t4", "t5"}
    assert (await q.pop_next()).id is not None

    empty = Queue(chat_id=3)
    assert await empty.get_current() is None
    assert await empty.get_next() is None
    assert await empty.prune_stale(100) == 0

    capped = Queue(chat_id=4, max_size=2)
    assert await capped.add(Track(id="a", title="A")) == 0
    assert await capped.add(Track(id="b", title="B")) == 1
    with pytest.raises(QueueFullError):
        await capped.add(Track(id="c", title="C"))
    with pytest.raises(QueueFullError):
        await capped.add_next(Track(id="d", title="D"))


async def _insert_row(session_factory, chat_id, *, position=0, payload=None, track_id="x", title="X", created_at=None):
    async with session_factory() as s:
        s.add(
            QueueEntry(
                chat_id=chat_id,
                track_id=track_id,
                title=title,
                position=position,
                payload=payload,
                created_at=created_at or datetime.now(UTC),
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_db_get_purges_corrupt(session_factory):
    corrupt = {
        "title": "legacy corrupt row",  # missing id -> Track.try_from_dict returns None
    }
    await _insert_row(session_factory, 10, position=0, payload=corrupt)
    await _insert_row(session_factory, 10, position=1, payload={"id": "ok", "title": "Good"})

    q = Queue(session_factory=session_factory, chat_id=10)
    assert (await q.get_current()).id == "ok"
    assert await q.get_next() is None  # corrupt row skipped in the sanitized view
    # _db_get deletes the bad row but never commits, so the row is rolled back.
    assert (await q.size()) == 2


@pytest.mark.asyncio
async def test_db_get_out_of_range_and_remove_branches(session_factory):
    await _insert_row(session_factory, 11, position=0, payload={"id": "a", "title": "A"})
    await _insert_row(session_factory, 11, position=1, payload={"id": "b", "title": "B"})

    q = Queue(session_factory=session_factory, chat_id=11)
    assert (await q.get_current()).id == "a"
    assert (await q.get_next()).id == "b"

    await _insert_row(session_factory, 12, position=0, payload={"id": "c", "title": "C"})
    q2 = Queue(session_factory=session_factory, chat_id=12)
    assert (await q2.get_current()).id == "c"
    assert await q2.get_next() is None  # _db_get(1) on a single row -> OOB returns None

    assert await q2.remove(9) is None  # _db_remove position out of range
    assert (await q2.remove(0)).id == "c"


@pytest.mark.asyncio
async def test_db_remove_skips_corrupt_front(session_factory):
    await _insert_row(session_factory, 13, position=0, payload={"title": "bad"})
    await _insert_row(session_factory, 13, position=1, payload={"id": "good", "title": "Good"})

    q = Queue(session_factory=session_factory, chat_id=13)
    assert (await q.remove(0)).id == "good"
    assert await q.size() == 0


@pytest.mark.asyncio
async def test_db_move_out_of_range_and_purge(session_factory):
    await _insert_row(session_factory, 14, position=0, payload={"id": "a", "title": "A"})
    q = Queue(session_factory=session_factory, chat_id=14)
    assert await q.move(0, 5) is False  # new_idx out of range -> _db_move False

    # A failing move purges/flushes but returns before commit (delete is rolled back);
    # only the committed success path persists the purge.
    await _insert_row(session_factory, 15, position=0, payload={"title": "bad"})
    q_purge = Queue(session_factory=session_factory, chat_id=15)
    assert await q_purge.move(0, 0) is False
    assert await q_purge.size() == 1

    await _insert_row(session_factory, 17, position=0, payload={"title": "bad"})
    await _insert_row(session_factory, 17, position=1, payload={"id": "keep1", "title": "Keep1"})
    await _insert_row(session_factory, 17, position=2, payload={"id": "keep2", "title": "Keep2"})
    q_success = Queue(session_factory=session_factory, chat_id=17)
    # Purge drops "bad", leaving [keep1, keep2]; move(0, 1) swaps them in place.
    assert await q_success.move(0, 1) is True
    assert [t.id for t in await q_success.list()] == ["keep2", "keep1"]


@pytest.mark.asyncio
async def test_prune_stale_persisted(session_factory):
    old = datetime.now(UTC) - timedelta(days=2)
    await _insert_row(session_factory, 16, position=0, payload={"id": "old", "title": "Old"}, created_at=old)
    await _insert_row(session_factory, 16, position=1, payload={"id": "new", "title": "New"})

    q = Queue(session_factory=session_factory, chat_id=16)
    assert await q.prune_stale(3600) == 1
    assert await q.size() == 1

    async with session_factory() as s:
        from sqlalchemy import delete as sa_delete

        await s.execute(sa_delete(QueueEntry))
        await s.commit()

    assert await q.prune_stale(3600) == 0
