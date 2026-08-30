import logging
import os

from pyrogram import Client, enums, filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ...player.models import Track
from ...player.queue import QueueFullError
from ...sources import get_default_providers
from ...sources.validation import is_http_url, looks_like_audio

logger = logging.getLogger(__name__)

QUEUE_PAGE_SIZE = 10
MAX_PICK_RESULTS = 5


async def _is_privileged(client: Client, message: Message) -> bool:
    """Return True if the sender is the bot owner or an admin/creator in the chat."""
    user = message.from_user
    if user is None:
        return False

    settings = getattr(client, "settings", None)
    owner_id = getattr(settings, "BOT_OWNER_ID", None) if settings is not None else None
    if owner_id is None:
        owner_id = os.environ.get("BOT_OWNER_ID")

    if owner_id is not None and str(user.id) == str(owner_id):
        return True

    try:
        member = await client.get_chat_member(message.chat.id, user.id)
    except Exception:
        logger.debug("could not verify chat member %s in chat %s", user.id, message.chat.id)
        return False

    return member.status in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER)


async def join_handler(client: Client, message: Message) -> None:
    chat_id = message.chat.id
    try:
        vm = getattr(client, "voice", None)
        if vm is None:
            await message.reply_text("❌ Voice support not configured on the bot.")
            return

        # Acquire a short lock for join operations to avoid concurrent joins
        locks = getattr(client, "locks", None)
        lock_name = f"lock:join:chat:{chat_id}"
        token = None
        try:
            if locks is not None:
                token = await locks.acquire(lock_name, ttl=10, retries=2, backoff_base=0.1)
                if token is None:
                    await message.reply_text("⏳ Another join/leave is in progress in this chat. Try again shortly.")
                    # metrics
                    metrics = getattr(client, "metrics", None)
                    if metrics is not None:
                        metrics.inc("locks.acquire_failed.join")
                    return

            await message.reply_text("⏳ Joining voice chat...")
            await vm.join(chat_id)
            await message.reply_text("✅ Joined the voice chat.")
        finally:
            if locks is not None and token is not None:
                try:
                    ok = await locks.release(lock_name, token)
                    metrics = getattr(client, "metrics", None)
                    if metrics is not None:
                        metrics.inc("locks.released.join" if ok else "locks.release_failed.join")
                except Exception:
                    logger.exception("Failed to release join lock %s", lock_name)
                    metrics = getattr(client, "metrics", None)
                    if metrics is not None:
                        metrics.inc("locks.release_exception.join")
    except Exception:
        await message.reply_text("❌ Failed to join the voice chat. Check logs.")


async def leave_handler(client: Client, message: Message) -> None:
    chat_id = message.chat.id
    try:
        vm = getattr(client, "voice", None)
        if vm is None:
            await message.reply_text("❌ Voice support not configured on the bot.")
            return

        # Acquire a short lock for leave operations to avoid concurrent joins/leaves
        locks = getattr(client, "locks", None)
        lock_name = f"lock:join:chat:{chat_id}"
        token = None
        try:
            if locks is not None:
                token = await locks.acquire(lock_name, ttl=10, retries=2, backoff_base=0.1)
                if token is None:
                    await message.reply_text("⏳ Another join/leave is in progress in this chat. Try again shortly.")
                    metrics = getattr(client, "metrics", None)
                    if metrics is not None:
                        metrics.inc("locks.acquire_failed.leave")
                    return

            await message.reply_text("⏳ Leaving voice chat...")
            await vm.leave(chat_id)
            await message.reply_text("✅ Left the voice chat.")
        finally:
            if locks is not None and token is not None:
                try:
                    ok = await locks.release(lock_name, token)
                    metrics = getattr(client, "metrics", None)
                    if metrics is not None:
                        metrics.inc("locks.released.leave" if ok else "locks.release_failed.leave")
                except Exception:
                    logger.exception("Failed to release join lock %s", lock_name)
                    metrics = getattr(client, "metrics", None)
                    if metrics is not None:
                        metrics.inc("locks.release_exception.leave")
    except Exception:
        await message.reply_text("❌ Failed to leave the voice chat. Check logs.")


async def vc_status_handler(client: Client, message: Message) -> None:
    """Debug: report who is currently in the group voice chat."""
    vm = getattr(client, "voice", None)
    if vm is None:
        await message.reply_text("❌ Voice support not configured on the bot.")
        return
    try:
        participants = await vm.get_participants(message.chat.id)
    except Exception:
        logger.exception("failed to fetch participants")
        await message.reply_text("❌ Failed to read voice-chat participants.")
        return
    lines = [f"🧪 Voice chat participants ({len(participants)}):"]
    for p in participants:
        uid = getattr(p, "user_id", None)
        muted = getattr(p, "muted", None)
        src = getattr(p, "source", None)
        lines.append(f"• id={uid} muted={muted} source={src}")
    await message.reply_text("\n".join(lines) if lines else "📭 No participants.")


def register(app: Client) -> None:
    app.add_handler(MessageHandler(join_handler, filters=filters.command("join")))
    app.add_handler(MessageHandler(leave_handler, filters=filters.command("leave")))
    app.add_handler(MessageHandler(vc_status_handler, filters=filters.command("vc")))
    app.add_handler(MessageHandler(play_handler, filters=filters.command("play")))
    app.add_handler(MessageHandler(playnext_handler, filters=filters.command("playnext")))
    app.add_handler(MessageHandler(stop_handler, filters=filters.command("stop")))
    app.add_handler(MessageHandler(queue_handler, filters=filters.command("queue")))
    app.add_handler(MessageHandler(now_playing_handler, filters=filters.command(["nowplaying", "np"])))
    app.add_handler(MessageHandler(skip_handler, filters=filters.command("skip")))
    app.add_handler(MessageHandler(clear_handler, filters=filters.command("clear")))
    app.add_handler(MessageHandler(remove_handler, filters=filters.command("rm")))
    app.add_handler(MessageHandler(move_handler, filters=filters.command("move")))
    app.add_handler(MessageHandler(shuffle_handler, filters=filters.command("shuffle")))
    app.add_handler(MessageHandler(pause_handler, filters=filters.command("pause")))
    app.add_handler(MessageHandler(resume_handler, filters=filters.command("resume")))
    app.add_handler(MessageHandler(volume_handler, filters=filters.command("volume")))
    app.add_handler(CallbackQueryHandler(inline_callback))


async def play_handler(client: Client, message: Message) -> None:
    chat_id = message.chat.id
    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text("❌ Usage: /play <url_or_query>")
        return

    input_source = parts[1].strip()
    vm = getattr(client, "voice", None)
    if vm is None:
        await message.reply_text("❌ Voice support not configured on the bot.")
        return

    # Rate limiting: per-user and per-chat
    rl = getattr(client, "rate_limiter", None)
    try:
        if rl is not None and message.from_user is not None:
            user_key = f"ratelimit:play:user:{message.from_user.id}"
            chat_key = f"ratelimit:play:chat:{chat_id}"
            user_allowed = await rl.allow(user_key, limit=5, period=60)
            if not user_allowed:
                await message.reply_text("⏱️ You're making /play requests too quickly. Try again later.")
                return
            chat_allowed = await rl.allow(chat_key, limit=30, period=60)
            if not chat_allowed:
                await message.reply_text("⏱️ This chat is rate-limited for new /play requests. Try later.")
                return
    except Exception:
        # If rate limiter fails (redis down), continue without blocking the request
        pass

    await message.reply_text("⏳ Preparing playback...")

    pm = getattr(client, "player_manager", None)

    # Expand playlist/album URLs into a batch of tracks before searching.
    playlist_tracks = await _maybe_playlist(input_source)
    if playlist_tracks:
        if pm is None:
            await message.reply_text("❌ Queue support is required for playlists.")
            return
        player = await pm.get_player(chat_id)
        count = 0
        try:
            for t in playlist_tracks:
                if message.from_user is not None:
                    t.requested_by = message.from_user.id
                await player.enqueue(t)
                count += 1
        except QueueFullError:
            logger.info(
                "playlist enqueue hit queue limit chat=%s enqueued=%s/%s",
                chat_id,
                count,
                len(playlist_tracks),
            )
        if count == 1:
            await message.reply_text(f"✅ Enqueued: {playlist_tracks[0].title}")
        elif count:
            await message.reply_text(f"✅ Enqueued {count} of {len(playlist_tracks)} tracks from the playlist.")
        else:
            await message.reply_text("❌ The queue is full; nothing was enqueued.")
        return

    # Resolve track metadata + playable URL via providers
    try:
        chosen_track, playable_url, preferred_provider, search_results = await _resolve_track(client, message, input_source)
    except ValueError as e:
        await message.reply_text(f"❌ {e}")
        return

    # If multiple search results and a player manager is available, offer a picker
    if await _offer_picker(client, message, chat_id, preferred_provider, search_results):
        return

    # Enqueue via PlayerManager if available
    try:
        if pm is None:
            # fallback to direct play via voice manager
            locks = getattr(client, "locks", None)
            lock_name = f"lock:play:chat:{chat_id}"
            token = None
            try:
                if locks is not None:
                    token = await locks.acquire(lock_name, ttl=15, retries=3, backoff_base=0.15)
                    if token is None:
                        await message.reply_text("⏳ Another playback is starting in this chat. Try again in a moment.")
                        metrics = getattr(client, "metrics", None)
                        if metrics is not None:
                            metrics.inc("locks.acquire_failed.play")
                        return

                await vm.play(chat_id, playable_url)
                await message.reply_text(f"✅ Playing: {chosen_track.title}")
            finally:
                if locks is not None and token is not None:
                    try:
                        ok = await locks.release(lock_name, token)
                        metrics = getattr(client, "metrics", None)
                        if metrics is not None:
                            metrics.inc("locks.released.play" if ok else "locks.release_failed.play")
                    except Exception:
                        logger.exception("Failed to release play lock %s", lock_name)
                        metrics = getattr(client, "metrics", None)
                        if metrics is not None:
                            metrics.inc("locks.release_exception.play")
        else:
            player = await pm.get_player(chat_id)
            pos = await player.enqueue(chosen_track)
            await message.reply_text(f"✅ Enqueued: {chosen_track.title} (position {pos + 1})")
    except QueueFullError:
        await message.reply_text("❌ The queue is full. Clear it with /clear or use /stop.")
    except ValueError as e:
        await message.reply_text(f"❌ {e}")
    except Exception:
        await message.reply_text("❌ Failed to start playback. Check logs.")


async def _resolve_track(client: Client, message: Message, input_source: str) -> tuple:
    """Search providers for `input_source` and return a resolvable track + playable URL.

    Returns `(chosen_track, playable_url, preferred_provider, search_results)`.
    """
    providers = get_default_providers()
    chosen_track = None
    playable_url = None
    preferred_provider = None
    search_results: list = []
    for p in providers:
        try:
            results = await p.search(input_source)
            if not results:
                continue
            r = results[0]
            try:
                playable = await p.resolve_audio(r.id if r.source is None else (r.source_url or r.id))
                chosen_track = r
                playable_url = playable
                preferred_provider = p
                search_results = results
                break
            except Exception:
                continue
        except Exception:
            continue

    if chosen_track is None:
        # An http(s) input that no provider could make audio out of is a
        # website / expired link, not a search query — refuse it instead of
        # handing the raw URL to the player. Plain text queries still get the
        # generic passthrough track so the search chain can try them.
        if is_http_url(input_source) and not looks_like_audio(input_source):
            raise ValueError(
                f"That doesn't look like an audio source: {input_source}. "
                "Try a song title, a YouTube/SoundCloud link, or a direct audio file URL."
            )
        # fallback: treat input as direct URL or local id
        chosen_track = Track(id=input_source, title=input_source, source="url", source_url=input_source)
        playable_url = input_source

    if message.from_user is not None:
        chosen_track.requested_by = message.from_user.id

    # Keep a stable key (page URL / id) for fresh re-resolution at play time;
    # `source_url` is then set to the playable link for the immediate-pick path.
    # Signed playable URLs expire quickly, so the player re-resolves via the key.
    if chosen_track.resolve_key is None:
        chosen_track.resolve_key = chosen_track.source_url or chosen_track.id
    chosen_track.source_url = playable_url
    return chosen_track, playable_url, preferred_provider, search_results


async def _maybe_playlist(input_source: str) -> list[Track] | None:
    """Expand a playlist/album URL into tracks; returns None when not applicable."""
    if not (input_source.startswith("http://") or input_source.startswith("https://")):
        return None
    try:
        from ...sources.providers.yt_dlp_provider import YtDlpProvider

        tracks = await YtDlpProvider().resolve_playlist(input_source)
    except Exception:
        logger.debug("input not resolvable as a playlist: %s", input_source, exc_info=True)
        return None
    return tracks or None


async def _offer_picker(
    client: Client,
    message: Message,
    chat_id: int,
    preferred_provider,
    search_results: list,
    *,
    next_play: bool = False,
    action: str = "enqueue",
) -> bool:
    """Show an inline picker when a search returned multiple results.

    `action` controls what happens when a result is chosen: `enqueue`,
    `enqueue_next`, or `download`.

    Returns True when a picker was shown (the caller should stop handling).
    """
    pm = getattr(client, "player_manager", None)
    if pm is None or preferred_provider is None or len(search_results) <= 1:
        return False

    nonce = os.urandom(4).hex()
    picks = getattr(client, "pending_picks", None)
    if picks is None:
        picks = {}
        setattr(client, "pending_picks", picks)  # noqa: B010 - mypy blocks plain attribute assignment
    picks[nonce] = {
        "chat_id": chat_id,
        "results": search_results[:MAX_PICK_RESULTS],
        "provider": preferred_provider,
        "requested_by": message.from_user.id if message.from_user is not None else None,
        "action": "enqueue_next" if next_play else action,
    }
    def _label(track: Track) -> str:
        title = track.title[:40]
        if track.duration:
            title += f" [{_fmt_duration(track.duration)}]"
        return title

    buttons = [
        [InlineKeyboardButton(f"{i + 1}. {_label(t)}", callback_data=f"pick:{nonce}:{i}")]
        for i, t in enumerate(search_results[:MAX_PICK_RESULTS])
    ]
    await message.reply_text("🎵 Search results — pick a track:", reply_markup=InlineKeyboardMarkup(buttons))
    return True


async def playnext_handler(client: Client, message: Message) -> None:
    chat_id = message.chat.id
    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text("❌ Usage: /playnext <url_or_query>")
        return

    input_source = parts[1].strip()
    pm = getattr(client, "player_manager", None)
    if pm is None:
        await message.reply_text("❌ Queue support not configured on the bot.")
        return

    await message.reply_text("⏳ Preparing...")
    try:
        playlist_tracks = await _maybe_playlist(input_source)
        if playlist_tracks:
            first = playlist_tracks[0]
            if message.from_user is not None:
                first.requested_by = message.from_user.id
            player = await pm.get_player(chat_id)
            pos = await player.enqueue_next(first)
            what = f" (position {pos + 1})" if pos is not None else ""
            await message.reply_text(f"⏭️ Queued to play next: {first.title}{what}")
            return

        chosen_track, _playable_url, preferred_provider, search_results = await _resolve_track(
            client, message, input_source
        )
        if await _offer_picker(client, message, chat_id, preferred_provider, search_results, next_play=True):
            return

        player = await pm.get_player(chat_id)
        pos = await player.enqueue_next(chosen_track)
        what = f" (position {pos + 1})" if pos is not None else ""
        await message.reply_text(f"⏭️ Queued to play next: {chosen_track.title}{what}")
    except (ValueError, QueueFullError) as e:
        await message.reply_text(f"❌ {e}")
    except Exception:
        logger.exception("failed to queue next track")
        await message.reply_text("❌ Failed to queue track. Check logs.")


def _format_queue(items, page: int, page_size: int = QUEUE_PAGE_SIZE) -> tuple[str, InlineKeyboardMarkup | None]:
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = min(max(0, page), total_pages - 1)
    chunk = items[page * page_size : (page + 1) * page_size]

    lines = []
    for i, t in enumerate(chunk, start=page * page_size + 1):
        who = f" (requested by {t.requested_by})" if t.requested_by else ""
        lines.append(f"{i}. {t.title}{who}")
    if len(items) > (page + 1) * page_size:
        lines.append(f"... and {len(items) - len(lines)} more")

    buttons = None
    if total_pages > 1:
        row = []
        if page > 0:
            row.append(InlineKeyboardButton("◀", callback_data=f"qpage:{page - 1}"))
        row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="qpage:none"))
        if page < total_pages - 1:
            row.append(InlineKeyboardButton("▶", callback_data=f"qpage:{page + 1}"))
        buttons = InlineKeyboardMarkup([row])

    text = "\n".join(lines) if lines else "📭 The queue is empty."
    return text, buttons


def _cb_data(query: CallbackQuery) -> str:
    raw = query.data or b""
    return raw.decode("utf-8") if isinstance(raw, bytes) else raw


async def inline_callback(client: Client, query: CallbackQuery) -> None:
    data = _cb_data(query)
    try:
        if data.startswith("pick:"):
            await pick_callback(client, query)
        elif data.startswith("qpage:"):
            await queue_page_callback(client, query)
        elif data.startswith("fav:"):
            # handled by the favorites module's dedicated callback handler
            return
        else:
            await query.answer()
    except Exception:
        logger.exception("inline callback failed: %s", data)
        await query.answer("Something went wrong.", show_alert=True)


async def pick_callback(client: Client, query: CallbackQuery) -> None:
    data = _cb_data(query)
    try:
        _, nonce, idx_s = data.split(":")
        idx = int(idx_s)
    except (ValueError, IndexError):
        await query.answer("Invalid selection.", show_alert=True)
        return

    picks = getattr(client, "pending_picks", None) or {}
    entry = picks.get(nonce)
    if entry is None:
        await query.answer("This selection has expired. Run /play again.", show_alert=True)
        return

    # Only the user who requested the pick may act on it, to prevent other
    # members hijacking the selection. Authorize before consuming the entry so
    # a rejected click leaves the pick available for the real requester.
    requester_id = entry.get("requested_by")
    if requester_id is not None and (query.from_user is None or query.from_user.id != requester_id):
        await query.answer("This selection wasn't meant for you.", show_alert=True)
        return

    results = entry["results"]
    provider = entry.get("provider")
    chat_id = entry["chat_id"]

    if idx < 0 or idx >= len(results):
        await query.answer("Invalid selection.", show_alert=True)
        return

    # Consume the entry only now that the request is authorized and valid.
    picks.pop(nonce, None)

    track = results[idx]
    if entry.get("requested_by") is not None:
        track.requested_by = entry["requested_by"]

    if entry.get("action") == "download":
        await _download_picked(client, query, chat_id, track)
        return

    try:
        playable = await provider.resolve_audio(track.id if track.source is None else (track.source_url or track.id))
        if track.resolve_key is None:
            track.resolve_key = track.source_url or track.id
        track.source_url = playable
    except Exception:
        await query.answer("Could not resolve audio for this track.", show_alert=True)
        return

    pm = getattr(client, "player_manager", None)
    if pm is None:
        await query.answer("Playback not configured.", show_alert=True)
        return

    try:
        player = await pm.get_player(chat_id)
        if entry.get("action") == "enqueue_next":
            pos = await player.enqueue_next(track)
            label = f"⏭️ Queued to play next: {track.title}" + (f" (position {pos + 1})" if pos is not None else "")
        else:
            pos = await player.enqueue(track)
            label = f"✅ Enqueued: {track.title} (position {pos + 1})"
        if query.message is not None:
            await query.message.edit_text(label)
        await query.answer()
    except (QueueFullError, ValueError) as e:
        await query.answer(str(e), show_alert=True)
    except Exception:
        logger.exception("failed to enqueue picked track")
        await query.answer("Failed to start playback. Check logs.", show_alert=True)


async def _download_picked(client: Client, query: CallbackQuery, chat_id: int, track: Track) -> None:
    """Download the picked track as an audio message (lazy media import)."""
    from .media import deliver_audio

    if query.message is not None:
        await query.message.edit_text(f"⬇️ Downloading: {track.title}")
    await query.answer()
    try:
        await deliver_audio(client, chat_id, track)
    except Exception:
        logger.exception("failed to download picked track")
        if query.message is not None:
            await query.message.edit_text("❌ Download failed. Check logs.")


async def queue_page_callback(client: Client, query: CallbackQuery) -> None:
    data = _cb_data(query)
    if data == "qpage:none":
        await query.answer()
        return
    try:
        page = int(data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.answer("Invalid page.", show_alert=True)
        return

    pm = getattr(client, "player_manager", None)
    if pm is None:
        await query.answer("Queue support not configured.", show_alert=True)
        return

    try:
        chat_id = query.message.chat.id
        player = await pm.get_player(chat_id)
        text, markup = _format_queue(await player.queue.list(), page)
        if query.message is not None:
            if markup is not None:
                await query.message.edit_text(text, reply_markup=markup)
            else:
                await query.message.edit_text(text)
        await query.answer()
    except Exception:
        logger.exception("queue page callback failed")
        await query.answer("Could not fetch queue. Check logs.", show_alert=True)


async def queue_handler(client: Client, message: Message) -> None:
    chat_id = message.chat.id
    pm = getattr(client, "player_manager", None)
    if pm is None:
        await message.reply_text("❌ Queue support not configured on the bot.")
        return

    try:
        player = await pm.get_player(chat_id)
        items = await player.queue.list()
        if not items:
            await message.reply_text("📭 The queue is empty.")
            return

        text, markup = _format_queue(items, 0)
        if markup is not None:
            await message.reply_text(text, reply_markup=markup)
        else:
            await message.reply_text(text)
    except Exception:
        await message.reply_text("❌ Could not fetch queue. Check logs.")


async def now_playing_handler(client: Client, message: Message) -> None:
    chat_id = message.chat.id
    pm = getattr(client, "player_manager", None)
    if pm is None:
        await message.reply_text("❌ Now-playing support not configured on the bot.")
        return

    try:
        player = await pm.get_player(chat_id)
        cur = player.current
        if not cur:
            await message.reply_text("⏸️ Nothing is playing right now.")
            return

        artist = f" — {cur.artist}" if cur.artist else ""
        album = f"\n💿 Album: {cur.album}" if cur.album else ""
        dur = f"\n⏱️ Duration: {_fmt_duration(cur.duration)}" if cur.duration else ""
        who = f"\n👤 Requested by: {cur.requested_by}" if cur.requested_by else ""
        if cur.thumbnail:
            try:
                await message.reply_photo(cur.thumbnail)
            except Exception:
                logger.debug("could not send now-playing thumbnail", exc_info=True)
        await message.reply_text(f"▶️ **Now playing:** {cur.title}{artist}{album}{dur}{who}")
    except Exception:
        await message.reply_text("❌ Could not fetch now-playing. Check logs.")


async def _admin_guard(client: Client, message: Message) -> bool:
    """Check the sender is the owner or a chat admin. Sends a denial reply if not."""
    try:
        allowed = message.from_user is not None and await _is_privileged(client, message)
    except Exception:
        await message.reply_text("❌ Permission check failed; action denied.")
        return False
    if not allowed:
        await message.reply_text("❌ You don't have permission for this action.")
    return allowed


async def remove_handler(client: Client, message: Message) -> None:
    chat_id = message.chat.id
    if not await _admin_guard(client, message):
        return

    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply_text("❌ Usage: /rm <position>")
        return
    idx = int(parts[1]) - 1

    pm = getattr(client, "player_manager", None)
    if pm is None:
        await message.reply_text("❌ Queue support not configured on the bot.")
        return

    try:
        player = await pm.get_player(chat_id)
        removed = await player.queue.remove(idx)
        if removed is None:
            await message.reply_text("❌ No track at that position.")
        else:
            await message.reply_text(f"🗑️ Removed: {removed.title}")
    except Exception:
        logger.exception("failed to remove track")
        await message.reply_text("❌ Failed to remove track. Check logs.")


async def move_handler(client: Client, message: Message) -> None:
    chat_id = message.chat.id
    if not await _admin_guard(client, message):
        return

    parts = (message.text or "").split()
    if len(parts) < 3 or not (parts[1].isdigit() and parts[2].isdigit()):
        await message.reply_text("❌ Usage: /move <from> <to>")
        return
    old, new = int(parts[1]) - 1, int(parts[2]) - 1

    pm = getattr(client, "player_manager", None)
    if pm is None:
        await message.reply_text("❌ Queue support not configured on the bot.")
        return

    try:
        player = await pm.get_player(chat_id)
        ok = await player.queue.move(old, new)
        await message.reply_text("✅ Moved track." if ok else "❌ Invalid position.")
    except Exception:
        logger.exception("failed to move track")
        await message.reply_text("❌ Failed to move track. Check logs.")


async def shuffle_handler(client: Client, message: Message) -> None:
    chat_id = message.chat.id
    if not await _admin_guard(client, message):
        return

    pm = getattr(client, "player_manager", None)
    if pm is None:
        await message.reply_text("❌ Queue support not configured on the bot.")
        return

    try:
        player = await pm.get_player(chat_id)
        await player.queue.shuffle()
        await message.reply_text("🔀 Queue shuffled.")
    except Exception:
        logger.exception("failed to shuffle queue")
        await message.reply_text("❌ Failed to shuffle the queue. Check logs.")


async def pause_handler(client: Client, message: Message) -> None:
    chat_id = message.chat.id
    if not await _admin_guard(client, message):
        return

    pm = getattr(client, "player_manager", None)
    if pm is None:
        await message.reply_text("❌ Playback support not configured on the bot.")
        return

    try:
        player = await pm.get_player(chat_id)
        await player.pause()
        await message.reply_text("⏸️ Paused playback.")
    except ValueError:
        await message.reply_text("❌ Nothing is playing.")
    except NotImplementedError:
        await message.reply_text("❌ Pause/resume is only supported on Linux (POSIX signals).")
    except Exception:
        logger.exception("failed to pause playback")
        await message.reply_text("❌ Failed to pause. Check logs.")


async def resume_handler(client: Client, message: Message) -> None:
    chat_id = message.chat.id
    if not await _admin_guard(client, message):
        return

    pm = getattr(client, "player_manager", None)
    if pm is None:
        await message.reply_text("❌ Playback support not configured on the bot.")
        return

    try:
        player = await pm.get_player(chat_id)
        await player.resume()
        await message.reply_text("▶️ Resumed playback.")
    except ValueError:
        await message.reply_text("❌ Playback is not paused.")
    except Exception:
        logger.exception("failed to resume playback")
        await message.reply_text("❌ Failed to resume. Check logs.")


async def volume_handler(client: Client, message: Message) -> None:
    chat_id = message.chat.id
    if not await _admin_guard(client, message):
        return

    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply_text("❌ Usage: /volume <0-200>")
        return
    pct = int(parts[1])
    if not 0 <= pct <= 200:
        await message.reply_text("❌ Volume must be between 0 and 200.")
        return

    pm = getattr(client, "player_manager", None)
    if pm is None:
        await message.reply_text("❌ Playback support not configured on the bot.")
        return

    try:
        player = await pm.get_player(chat_id)
        applied = await player.set_volume(pct / 100.0)
        if applied:
            await message.reply_text(f"🔊 Volume set to {pct}%.")
        else:
            await message.reply_text(f"🔊 Volume stored as {pct}%. Nothing is currently playing.")
    except Exception:
        logger.exception("failed to set volume")
        await message.reply_text("❌ Failed to set volume. Check logs.")


def _fmt_duration(seconds) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return f"{seconds}s"
    m, s = divmod(max(0, seconds), 60)
    return f"{m}:{s:02d}"


async def stop_handler(client: Client, message: Message) -> None:
    chat_id = message.chat.id
    # permission check: only chat admins or bot owner can stop/clear playback
    try:
        allowed = message.from_user is not None and await _is_privileged(client, message)
    except Exception:
        await message.reply_text("❌ Permission check failed; action denied.")
        return
    if not allowed:
        await message.reply_text("❌ You don't have permission to stop playback.")
        return

    pm = getattr(client, "player_manager", None)
    if pm is None:
        vm = getattr(client, "voice", None)
        if vm is None:
            await message.reply_text("❌ Voice support not configured on the bot.")
            return

        await message.reply_text("⏳ Stopping playback...")
        try:
            await vm.stop_playback(chat_id)
            await message.reply_text("✅ Stopped playback.")
        except Exception:
            await message.reply_text("❌ Failed to stop playback. Check logs.")
        return

    try:
        player = await pm.get_player(chat_id)
        await player.stop()
        await message.reply_text("✅ Stopped playback and cleared queue.")
    except Exception:
        await message.reply_text("❌ Failed to stop playback. Check logs.")


async def skip_handler(client: Client, message: Message) -> None:
    # simple permission + skip implementation (delegates to player.skip if present)
    chat_id = message.chat.id
    pm = getattr(client, "player_manager", None)
    if pm is None:
        await message.reply_text("❌ Skip not supported without PlayerManager.")
        return

    try:
        player = await pm.get_player(chat_id)
        # permission: reuse stop_handler's logic by checking admin/owner
        try:
            allowed = message.from_user is not None and await _is_privileged(client, message)
        except Exception:
            await message.reply_text("❌ Permission check failed; action denied.")
            return
        if not allowed:
            await message.reply_text("❌ You don't have permission to skip tracks.")
            return

        if hasattr(player, "skip"):
            await player.skip()
            await message.reply_text("⏭️ Skipped current track.")
        else:
            await message.reply_text("❌ Skip not implemented for this player.")
    except Exception:
        await message.reply_text("❌ Failed to skip. Check logs.")


async def clear_handler(client: Client, message: Message) -> None:
    chat_id = message.chat.id
    pm = getattr(client, "player_manager", None)
    if pm is None:
        await message.reply_text("❌ Clear not supported without PlayerManager.")
        return

    try:
        player = await pm.get_player(chat_id)
        # permission check (reuse logic)
        try:
            allowed = message.from_user is not None and await _is_privileged(client, message)
        except Exception:
            await message.reply_text("❌ Permission check failed; action denied.")
            return
        if not allowed:
            await message.reply_text("❌ You don't have permission to clear the queue.")
            return

        if hasattr(player, "clear"):
            await player.clear()
            await message.reply_text("🧹 Cleared the queue.")
        else:
            # fallback: stop will clear queue in usual implementations
            if hasattr(player, "stop"):
                await player.stop()
                await message.reply_text("🧹 Cleared the queue (stop fallback).")
            else:
                await message.reply_text("❌ Clear not implemented for this player.")
    except Exception:
        await message.reply_text("❌ Failed to clear queue. Check logs.")
