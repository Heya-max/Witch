import pytest
from app.player.models import Track
from app.player.queue import Queue


@pytest.mark.asyncio
async def test_queue_add_and_pop():
    q = Queue()
    t1 = Track(id="1", title="Song1")
    t2 = Track(id="2", title="Song2")

    pos1 = await q.add(t1)
    assert pos1 == 0
    pos2 = await q.add(t2)
    assert pos2 == 1

    size = await q.size()
    assert size == 2

    first = await q.pop_next()
    assert first.id == "1"

    second = await q.pop_next()
    assert second.id == "2"

    empty = await q.pop_next()
    assert empty is None


@pytest.mark.asyncio
async def test_queue_move_uses_shift_semantics():
    q = Queue()
    for i in range(4):
        await q.add(Track(id=f"t{i}", title=f"Track {i}"))

    # move(1, 3): remove item at index 1 and insert at index 3
    assert await q.move(1, 3) is True
    ids = [t.id for t in await q.list()]
    assert ids == ["t0", "t2", "t3", "t1"]

    # out-of-range move returns False and leaves the queue unchanged
    assert await q.move(0, 99) is False
    assert await q.move(-1, 2) is False
    ids = [t.id for t in await q.list()]
    assert ids == ["t0", "t2", "t3", "t1"]
