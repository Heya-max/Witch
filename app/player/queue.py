import asyncio
import random
from collections import deque
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from ..db.models import QueueEntry
from .models import Track


class QueueFullError(Exception):
    """Raised when adding a track would exceed the per-chat queue cap."""

    def __init__(self, chat_id: int, limit: int) -> None:
        super().__init__(f"the queue for chat {chat_id} is full ({limit} tracks max)")
        self.chat_id = chat_id
        self.limit = limit


class Queue:
    """Per-chat queue for Tracks.

    If a `session_factory` is provided, entries are persisted in the database
    (``QueueEntry`` model); otherwise an in-memory deque is used. Either way
    the public API is the same threaded through an asyncio lock.

    An optional `max_size` caps how many tracks a single chat may queue
    (``QueueFullError`` is raised past it) so a runaway playlist cannot wedge
    the player or balloon the database.
    """

    def __init__(
        self,
        session_factory=None,
        chat_id: int | None = None,
        max_size: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._chat_id = chat_id
        self._items: deque[Track] = deque()
        self._lock = asyncio.Lock()
        self._persisted = session_factory is not None
        self._max_size = max_size

    # ------------------------------------------------------------------ #
    # persistence helpers
    # ------------------------------------------------------------------ #
    async def _rows(self, s) -> list[QueueEntry]:
        stmt = select(QueueEntry).where(QueueEntry.chat_id == self._chat_id).order_by(QueueEntry.position)
        result = await s.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    def _row_to_track(row: QueueEntry) -> Track | None:
        """Reconstruct a Track, tolerating legacy/corrupt payload rows.

        A row with a ``payload`` must parse into a real Track; if it does not
        the row is corrupt and ``None`` is returned so callers can delete it
        instead of letting it poison the whole queue. Rows *without* a payload
        (written before the column existed) are rebuilt from the column values.
        """
        if row.payload:
            return Track.try_from_dict(row.payload)
        track_id = (row.track_id or "").strip()
        if not track_id:
            return None
        return Track(id=track_id, title=row.title or track_id, requested_by=row.requested_by)

    async def _purge_bad_rows(self, s, rows: list[QueueEntry]) -> None:
        """Delete rows that fail to parse; usually called before mutations."""
        for row in rows:
            if self._row_to_track(row) is None:
                await s.delete(row)
        if rows:
            await s.flush()

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
            clean: list[Track] = []
            for row in rows:
                track = self._row_to_track(row)
                if track is not None:
                    clean.append(track)
                else:
                    await s.delete(row)
            if 0 <= idx < len(clean):
                return clean[idx]
            return None

    async def _db_pop_next(self) -> Track | None:
        async with self._session_factory() as s:
            rows = await self._rows(s)
            track: Track | None = None
            for row in rows:
                candidate = self._row_to_track(row)
                if candidate is not None:
                    track = candidate
                    await s.delete(row)
                    break
                # poisoned row at the front: drop it and keep scanning
                await s.delete(row)
            await s.commit()
            return track

    async def _db_remove(self, position: int) -> Track | None:
        async with self._session_factory() as s:
            rows = await self._rows(s)
            clean_rows: list[QueueEntry] = []
            for row in rows:
                if self._row_to_track(row) is not None:
                    clean_rows.append(row)
                else:
                    await s.delete(row)
            if position < 0 or position >= len(clean_rows):
                return None
            row = clean_rows[position]
            track = self._row_to_track(row)
            await s.delete(row)
            await s.commit()
            return track

    async def _db_move(self, old_idx: int, new_idx: int) -> bool:
        async with self._session_factory() as s:
            rows = await self._rows(s)
            await self._purge_bad_rows(s, rows)
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
            await self._purge_bad_rows(s, rows)
            rows = await self._rows(s)
            random.shuffle(rows)
            for i, row in enumerate(rows):
                row.position = i
            await s.commit()

    async def _db_list(self) -> list[Track]:
        async with self._session_factory() as s:
            rows = await self._rows(s)
            tracks: list[Track] = []
            for row in rows:
                track = self._row_to_track(row)
                if track is not None:
                    tracks.append(track)
                else:
                    await s.delete(row)
            await s.commit()
            return tracks

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
    async def _size_unlocked(self) -> int:
        if self._persisted:
            return await self._db_size()
        return len(self._items)

    def _check_capacity(self, current: int) -> None:
        if self._max_size is not None and current >= self._max_size:
            raise QueueFullError(self._chat_id or 0, self._max_size)

    async def add(self, track: Track) -> int:
        async with self._lock:
            self._check_capacity(await self._size_unlocked())
            if self._persisted:
                return await self._db_add(track)
            self._items.append(track)
            return len(self._items) - 1

    async def add_next(self, track: Track) -> int:
        async with self._lock:
            self._check_capacity(await self._size_unlocked())
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

    async def prune_stale(self, max_age_seconds: int) -> int:
        """Delete persisted entries older than `max_age_seconds`.

        Long-dead rows (stale queue state left over from earlier runs) can
        poison playback after a restart, so they are stripped on startup.
        Returns the number of rows deleted. No-op for in-memory queues.
        """
        if not self._persisted:
            return 0
        async with self._lock:
            cutoff = datetime.now(UTC) - timedelta(seconds=max_age_seconds)
            async with self._session_factory() as s:
                result = await s.execute(
                    delete(QueueEntry).where(
                        QueueEntry.chat_id == self._chat_id,
                        QueueEntry.created_at.isnot(None),
                        QueueEntry.created_at < cutoff,
                    )
                )
                await s.commit()
                return result.rowcount or 0


async def purge_stale_persisted_entries(session_factory, max_age_seconds: int) -> int:
    """Drop every persisted queue entry older than ``max_age_seconds``.

    Runs once at startup (before any player is created) so stale rows from
    earlier runs cannot poison playback after a restart. ``max_age_seconds <= 0``
    disables it. Returns the number of rows deleted.
    """
    if max_age_seconds is None or max_age_seconds <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(seconds=max_age_seconds)
    async with session_factory() as s:
        result = await s.execute(
            delete(QueueEntry).where(
                QueueEntry.created_at.isnot(None),
                QueueEntry.created_at < cutoff,
            )
        )
        await s.commit()
        return result.rowcount or 0
