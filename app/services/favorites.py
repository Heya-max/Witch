import logging

from sqlalchemy import select

from ..db.models import FavoriteEntry
from ..player.models import Track

logger = logging.getLogger(__name__)


def _payload(track: Track) -> dict:
    """Serialize a track for storage (metadata is dropped to keep it lean)."""
    data = track.to_dict()
    data.pop("metadata", None)
    return data


async def add_favorite(session_factory, user_id: int, track: Track) -> bool:
    """Persist a favorite; returns False when the track was already saved."""
    async with session_factory() as s:
        if track.id:
            stmt = select(FavoriteEntry).where(
                FavoriteEntry.user_id == user_id,
                FavoriteEntry.source_id == track.id,
            )
            existing = (await s.execute(stmt)).scalar_one_or_none()
            if existing is not None:
                return False
        s.add(FavoriteEntry(user_id=user_id, source_id=track.id, payload=_payload(track)))
        await s.commit()
        return True


async def list_favorites(session_factory, user_id: int) -> list[Track]:
    async with session_factory() as s:
        stmt = (
            select(FavoriteEntry)
            .where(FavoriteEntry.user_id == user_id)
            .order_by(FavoriteEntry.created_at.desc(), FavoriteEntry.id.desc())
        )
        rows = (await s.execute(stmt)).scalars().all()
    return [Track.from_dict(r.payload) for r in rows]


async def remove_favorite(session_factory, user_id: int, index: int) -> bool:
    """Remove the 1-based favorite; returns False for an out-of-range index."""
    async with session_factory() as s:
        stmt = (
            select(FavoriteEntry)
            .where(FavoriteEntry.user_id == user_id)
            .order_by(FavoriteEntry.created_at.desc(), FavoriteEntry.id.desc())
        )
        rows = list((await s.execute(stmt)).scalars().all())
        if index < 0 or index >= len(rows):
            return False
        await s.delete(rows[index])
        await s.commit()
        return True


async def favs_text(session_factory, user_id: int) -> str:
    """Return a plain-text listing for the /favs command."""
    tracks = await list_favorites(session_factory, user_id)
    if not tracks:
        return "⭐ No favorites yet. Use /fav <query> to save one."
    lines = [f"{i + 1}. {t.title}" for i, t in enumerate(tracks)]
    return "⭐ Your favorites:\n" + "\n".join(lines)
