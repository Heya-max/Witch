import asyncio
import random
from collections import deque

from sqlalchemy import delete, func, select

from ..db.models import QueueEntry
from .models import Track


class Queue:
    """Per-chat queue for Tracks.

    If a `session_factory` is provided, entries are persisted in the database
    (``QueueEntry`` model); otherwise an in-memory deque is used. Either way
    the public API is the same threaded through an asyncio lock.
    """

    def __init__(
        self,
        session_factory=None,
        chat_id: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._chat_id = chat_id
        self._items: deque[Track] = deque()
        self._lock = asyncio.Lock()
        self._persisted = session_factory is not None

    # ------------------------------------------------------------------ #
    # persistence helpers
    # ------------------------------------------------------------------ #
    async def _rows(self, s) -> list[QueueEntry]:
        stmt = select(QueueEntry).where(QueueEntry.chat_id == self._chat_id).order_by(QueueEntry.position)
        result = await s.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def _row_to_track(row: QueueEntry) -> Track:
        if row.payload:
            return Track.from_dict(row.payload)
        return Track(id=row.track_id, title=row.title or row.track_id, requested_by=row.requested_by)

    async def _db_add(self, track: Track, front: bool = False) -> int:
        async with self._session_factory() as s:
            rows = await self._rows(s)
            pos = (rows[0].position - 1 if rows else 0) if front else (rows[-1].position + 1 if rows else 0)
            entry = QueueEntry(
                chat_id=self._chat_id,
                track_id=track.id,
                title=track.title,
                requested_by=track.requested_by,
                position=pos,
                payload=track.to_dict(),
            )
            s.add(entry)
            await s.commit()
            return 0 if front else len(rows)

    async def _db_get(self, idx: int) -> Track | None:
        async with self._session_factory() as s:
            rows = await self._rows(s)
            if 0 <= idx < len(rows):
                return await self._row_to_track(rows[idx])
            return None

    async def _db_pop_next(self) -> Track | None:
        async with self._session_factory() as s:
            rows = await self._rows(s)
            if not rows:
                return None
            row = rows[0]
            track = await self._row_to_track(row)
            await s.delete(row)
            await s.commit()
            return track

    async def _db_remove(self, position: int) -> Track | None:
        async with self._session_factory() as s:
            rows = await self._rows(s)
            if position < 0 or position >= len(rows):
                return None
            row = rows[position]
            track = await self._row_to_track(row)
            await s.delete(row)
            await s.commit()
            return track

    async def _db_move(self, old_idx: int, new_idx: int) -> bool:
        async with self._session_factory() as s:
            rows = await self._rows(s)
            if not (0 <= old_idx < len(rows) and 0 <= new_idx < len(rows)):
                return False
            row = rows.pop(old_idx)
            rows.insert(new_idx, row)
            for i, r in enumerate(rows):
                r.position = i
            await s.commit()
            return True

    async def _db_shuffle(self) -> None:
        async with self._session_factory() as s:
            rows = await self._rows(s)
            random.shuffle(rows)
            for i, row in enumerate(rows):
                row.position = i
            await s.commit()

    async def _db_list(self) -> list[Track]:
        async with self._session_factory() as s:
            rows = await self._rows(s)
            return [await self._row_to_track(r) for r in rows]

    async def _db_size(self) -> int:
        async with self._session_factory() as s:
            stmt = select(func.count()).select_from(QueueEntry).where(QueueEntry.chat_id == self._chat_id)
            return int((await s.execute(stmt)).scalar_one())

    async def _db_clear(self) -> None:
        async with self._session_factory() as s:
            await s.execute(delete(QueueEntry).where(QueueEntry.chat_id == self._chat_id))
            await s.commit()

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    async def add(self, track: Track) -> int:
        async with self._lock:
            if self._persisted:
                return await self._db_add(track)
            self._items.append(track)
            return len(self._items) - 1

    async def add_next(self, track: Track) -> int:
        async with self._lock:
            if self._persisted:
                return await self._db_add(track, front=True)
            self._items.appendleft(track)
            return 0

    async def remove(self, position: int) -> Track | None:
        async with self._lock:
            if self._persisted:
                return await self._db_remove(position)
            if position < 0 or position >= len(self._items):
                return None
            self._items.rotate(-position)
            item = self._items.popleft()
            self._items.rotate(position)
            return item

    async def move(self, old_idx: int, new_idx: int) -> bool:
        async with self._lock:
            if self._persisted:
                return await self._db_move(old_idx, new_idx)
            n = len(self._items)
            if not (0 <= old_idx < n and 0 <= new_idx < n):
                return False
            item = self._items[old_idx]
            del self._items[old_idx]
            self._items.insert(new_idx, item)
            return True

    async def shuffle(self) -> None:
        async with self._lock:
            if self._persisted:
                await self._db_shuffle()
                return
            lst = list(self._items)
            random.shuffle(lst)
            self._items = deque(lst)

    async def get_current(self) -> Track | None:
        async with self._lock:
            if self._persisted:
                return await self._db_get(0)
            return self._items[0] if self._items else None

    async def get_next(self) -> Track | None:
        async with self._lock:
            if self._persisted:
                return await self._db_get(1)
            return self._items[1] if len(self._items) > 1 else None

    async def pop_next(self) -> Track | None:
        async with self._lock:
            if self._persisted:
                return await self._db_pop_next()
            if not self._items:
                return None
            return self._items.popleft()

    async def list(self) -> list[Track]:
        async with self._lock:
            if self._persisted:
                return await self._db_list()
            return list(self._items)

    async def size(self) -> int:
        async with self._lock:
            if self._persisted:
                return await self._db_size()
            return len(self._items)

    async def clear(self) -> None:
        async with self._lock:
            if self._persisted:
                await self._db_clear()
                return
            self._items.clear()
