import logging

from pyrogram import Client, filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ...services.favorites import add_favorite, list_favorites, remove_favorite
from ...sources.providers.yt_dlp_provider import YtDlpProvider
from .playback import _resolve_track

logger = logging.getLogger(__name__)

FAV_MAX = 15


def _session_factory(client: Client):
    return getattr(client, "db_session_factory", None)


def _track_label(track) -> str:
    title = (track.title or "Unknown")[:35]
    return title


def _fav_buttons(n: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("▶ Play", callback_data=f"fav:p:{i}"),
            InlineKeyboardButton("❌ Remove", callback_data=f"fav:r:{i}"),
        ]
        for i in range(n)
    ]
    return InlineKeyboardMarkup(rows)


async def _render_favs(client: Client, message: Message, user_id: int) -> None:
    session_factory = _session_factory(client)
    if session_factory is None:
        await message.reply_text("❌ Favorites service is temporarily unavailable. Please try again later.")
        return
    tracks = await list_favorites(session_factory, user_id)
    if not tracks:
        await message.reply_text("⭐ No favorites yet. Use /fav <query> to save one.")
        return
    lines = "\n".join(f"{i + 1}. {_track_label(t)}" for i, t in enumerate(tracks[:FAV_MAX]))
    text = f"⭐ Your favorites:\n{lines}"
    await message.reply_text(text, reply_markup=_fav_buttons(min(len(tracks), FAV_MAX)))


async def fav_handler(client: Client, message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text("❌ Usage: /fav <query_or_url>")
        return
    if message.from_user is None:
        await message.reply_text("❌ Users only, please.")
        return
    session_factory = _session_factory(client)
    if session_factory is None:
        await message.reply_text("❌ Favorites service is temporarily unavailable. Please try again later.")
        return

    input_source = parts[1].strip()
    await message.reply_text("⏳ Resolving track...")
    try:
        chosen_track, _playable_url, _preferred, search_results = await _resolve_track(
            client, message, input_source
        )
        track = search_results[0] if search_results else chosen_track
        track.requested_by = message.from_user.id
        added = await add_favorite(session_factory, message.from_user.id, track)
        if added:
            await message.reply_text(f"⭐ Added to favorites: {track.title}")
        else:
            await message.reply_text(f"⭐ Already in favorites: {track.title}")
    except ValueError as e:
        await message.reply_text(f"❌ {e}")
    except Exception:
        logger.exception("failed to save favorite for user=%s", message.from_user.id)
        await message.reply_text("❌ Could not save favorite. Try again later.")


async def favs_handler(client: Client, message: Message) -> None:
    if message.from_user is None:
        await message.reply_text("❌ Users only, please.")
        return
    await _render_favs(client, message, message.from_user.id)


async def unfav_handler(client: Client, message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text("❌ Usage: /unfav <number> (see /favs)")
        return
    if message.from_user is None:
        await message.reply_text("❌ Users only, please.")
        return
    session_factory = _session_factory(client)
    if session_factory is None:
        await message.reply_text("❌ Favorites service is temporarily unavailable. Please try again later.")
        return

    try:
        index = int(parts[1].strip()) - 1
    except ValueError:
        await message.reply_text("❌ That isn't a valid number.")
        return

    if await remove_favorite(session_factory, message.from_user.id, index):
        await message.reply_text("🗑️ Removed favorite.")
    else:
        await message.reply_text("❌ No favorite at that position.")


async def _resolve_fav_playable(track) -> str | None:
    if track.source == "yt-dlp":
        try:
            return await YtDlpProvider().resolve_audio(track.source_url or track.id or "")
        except Exception:
            logger.exception("failed to resolve favorite audio %s", track.id)
            return None
    return track.source_url or track.id


async def fav_callback(client: Client, query: CallbackQuery) -> None:
    data = (query.data or b"")
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    try:
        _, kind, idx_s = data.split(":")
        idx = int(idx_s)
    except (ValueError, IndexError):
        await query.answer("Invalid selection.", show_alert=True)
        return

    user_id = query.from_user.id if query.from_user is not None else None
    if user_id is None:
        await query.answer("Not allowed.", show_alert=True)
        return

    session_factory = _session_factory(client)
    if session_factory is None:
        await query.answer("Database unavailable.", show_alert=True)
        return

    tracks = await list_favorites(session_factory, user_id)
    if idx < 0 or idx >= len(tracks):
        await query.answer("No favorite at that position.", show_alert=True)
        return

    if kind == "r":
        removed = await remove_favorite(session_factory, user_id, idx)
        if removed and query.message is not None:
            await _re_render_topics(client, query.message, user_id)
        await query.answer("Removed." if removed else "Nothing removed.")
        return

    if kind == "p":
        if query.message is None:
            await query.answer("Cannot play from here.", show_alert=True)
            return
        track = tracks[idx]
        playable = await _resolve_fav_playable(track)
        if not playable:
            await query.answer("Could not resolve audio for this track.", show_alert=True)
            return
        if track.resolve_key is None:
            track.resolve_key = track.source_url or track.id
        track.source_url = playable
        track.requested_by = user_id

        pm = getattr(client, "player_manager", None)
        if pm is None:
            await query.answer("Playback not configured.", show_alert=True)
            return
        try:
            player = await pm.get_player(query.message.chat.id)
            pos = await player.enqueue(track)
            await query.message.edit_text(f"✅ Enqueued: {track.title} (position {pos + 1})")
            await query.answer()
        except Exception:
            logger.exception("failed to enqueue favorite")
            await query.answer("Failed to start playback. Try again later.", show_alert=True)
        return

    await query.answer()


async def _re_render_topics(client: Client, message: Message, user_id: int) -> None:
    """Refresh a /favs reply after a removal; falls back to a plain reply."""
    session_factory = _session_factory(client)
    if session_factory is None:
        return
    tracks = await list_favorites(session_factory, user_id)
    if tracks:
        lines = "\n".join(f"{i + 1}. {_track_label(t)}" for i, t in enumerate(tracks[:FAV_MAX]))
        await message.edit_text(f"⭐ Your favorites:\n{lines}", reply_markup=_fav_buttons(min(len(tracks), FAV_MAX)))
    else:
        await message.edit_text("⭐ No favorites yet. Use /fav <query> to save one.")


def _fav_filter(_, __, query: CallbackQuery) -> bool:
    data = query.data or b""
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    return data.startswith("fav:")


def register(app: Client) -> None:
    app.add_handler(MessageHandler(fav_handler, filters=filters.command("fav")))
    app.add_handler(MessageHandler(favs_handler, filters=filters.command("favs")))
    app.add_handler(MessageHandler(unfav_handler, filters=filters.command("unfav")))
    app.add_handler(CallbackQueryHandler(fav_callback, filters=filters.create(_fav_filter)))
