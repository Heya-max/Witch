"""Integration test against a real Postgres instance.

Picks up the GitHub Actions ``postgres`` service on localhost:5432, or the
docker-compose host mapping on localhost:5433; skips when neither is
reachable (no external services running/docker available).
"""

import pytest
import pytest_asyncio
from app.db.models import Base
from app.player.models import Track
from app.player.queue import Queue
from app.services import favorites
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

_PG_URLS = (
    # GitHub Actions `postgres` service (container is localhost:5432)
    "postgresql+asyncpg://postgres:postgres@localhost:5432/telegram_music",
    # docker-compose maps Postgres 5433:5432 on the host
    "postgresql+asyncpg://postgres:postgres@localhost:5433/telegram_music",
)


@pytest_asyncio.fixture(scope="module")
async def pg_factory():
    engine = None
    for url in _PG_URLS:
        # NullPool: each connection is tied to the event loop that created it,
        # and pytest-asyncio hands each test a fresh loop. Pooling across those
        # loops breaks asyncpg ("got result for unknown protocol state").
        try:
            engine = create_async_engine(url, poolclass=NullPool)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            break
        except Exception:
            if engine is not None:
                await engine.dispose()
            engine = None
            continue

    if engine is None:
        pytest.skip(
            "Postgres not reachable on localhost:5432/5433 (start docker compose or rely on the CI postgres service)"
        )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_queue_persists_across_instances(pg_factory):
    q = Queue(session_factory=pg_factory, chat_id=10)
    assert await q.add(Track(id="pg:2", title="Second")) == 0
    assert await q.add_next(Track(id="pg:0", title="Front")) == 0
    ids = [t.id for t in await q.list()]
    assert ids == ["pg:0", "pg:2"]

    # a fresh Queue instance sees the same rows (real persistence)
    other = Queue(session_factory=pg_factory, chat_id=10)
    assert await other.size() == 2
    popped = await other.pop_next()
    assert popped.id == "pg:0"
    assert popped.title == "Front"

    # move changes persisted order
    await q.add(Track(id="pg:3", title="Third"))
    await q.move(1, 0)
    ids = [t.id for t in await q.list()]
    assert ids == ["pg:3", "pg:2"]


@pytest.mark.asyncio
async def test_queue_prune_stale(pg_factory):
    q = Queue(session_factory=pg_factory, chat_id=11)
    await q.add(Track(id="pg:old", title="Old"))
    await q.add(Track(id="pg:new", title="New"))
    # negative max_age puts the cutoff in the future, so both freshly-created
    # rows satisfy `created_at < cutoff` and are pruned deterministically
    await q.prune_stale(-1)
    assert await q.size() == 0


@pytest.mark.asyncio
async def test_favorites_add_list_remove(pg_factory):
    track = Track(id="pg:7", title="Fav Song", artist="Artist", requested_by=7)
    assert await favorites.add_favorite(pg_factory, 7, track) is True
    # duplicate save is rejected
    assert await favorites.add_favorite(pg_factory, 7, track) is False
    listing = await favorites.list_favorites(pg_factory, 7)
    assert [t.id for t in listing] == ["pg:7"]
    assert await favorites.remove_favorite(pg_factory, 7, 0) is True
    assert await favorites.list_favorites(pg_factory, 7) == []
