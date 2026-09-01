import asyncio
import logging
import os
import shutil
import tempfile
import time
from urllib.parse import quote

import httpx
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler

from ...player.models import Track
from ...sources.providers.yt_dlp_provider import YtDlpProvider
from .playback import _offer_picker, _resolve_track

logger = logging.getLogger(__name__)

MAX_AUDIO_SIZE = 50 * 1024 * 1024
LYRICS_CACHE_TTL = 3600.0
LYRICS_API_BASE = os.environ.get("LYRICS_API_BASE", "https://api.lyrics.ovh/v1")

_lyrics_cache: dict[tuple[str, str], tuple[float, str | None]] = {}
_downloader_instance: YtDlpProvider | None = None


def _downloader() -> YtDlpProvider:
    """Return a lazily-created YtDlpProvider for downloads (sync API)."""
    global _downloader_instance
    if _downloader_instance is None:
        _downloader_instance = YtDlpProvider()
    return _downloader_instance


def _audio_caption(track: Track) -> str:
    caption = f"🎧 **{track.title}**"
    if track.artist:
        caption += f" — {track.artist}"
    if track.source:
        caption += f"\nSource: {track.source}"
    return caption


async def deliver_audio(client: Client, chat_id: int, track: Track) -> None:
    """Download `track`'s audio and send it as an audio message to `chat_id`.

    Downloads into a temp dir and removes it once sent.
    """
    tmp = tempfile.mkdtemp(prefix="witch_audio_")
    try:
        source = track.source_url or track.source_id or track.id
        dl = _downloader()
        path = await asyncio.to_thread(dl.download, source, tmp)
        if os.path.getsize(path) > MAX_AUDIO_SIZE:
            raise ValueError("track exceeds the Telegram file size limit")
        ext = os.path.splitext(path)[1]
        await client.send_audio(
            chat_id,
            path,
            title=track.title,
            performer=track.artist or "",
            duration=track.duration or 0,
            caption=_audio_caption(track),
            file_name=f"{track.title}{ext}",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def lyrics_handler(client: Client, message) -> None:
    text = message.text or ""
    parts = text.split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""
    artist: str | None = None
    title = query

    if query and " - " in query:
        artist, title = (x.strip() for x in query.split(" - ", 1))

    if not title:
        # Fall back to the currently playing track
        pm = getattr(client, "player_manager", None)
        try:
            if pm is not None:
                player = await pm.get_player(message.chat.id)
                cur = getattr(player, "current", None)
                if cur is not None:
                    artist, title = cur.artist, cur.title
        except Exception:
            logger.debug("could not read current track for lyrics", exc_info=True)

    if not title:
        await message.reply_text("❌ Usage: /lyrics <artist - title> (or run it while something is playing)")
        return

    await message.reply_text("⏳ Fetching lyrics...")
    try:
        lyrics = await fetch_lyrics(artist, title)
    except Exception:
        logger.exception("lyrics fetch failed")
        await message.reply_text("❌ Couldn't fetch lyrics. Try again later.")
        return

    if not lyrics:
        await message.reply_text("🎼 No lyrics found for that track.")
        return

    clean = lyrics.strip()
    if len(clean) > 3500:
        clean = clean[:3500] + "\n…"
    header = f"🎼 **{artist or title} — {title}**"
    await message.reply_text(f"{header}\n\n{clean}")


def _lyrics_key(artist: str | None, title: str) -> tuple[str, str]:
    return ((artist or "").strip().lower(), title.strip().lower())


async def fetch_lyrics(artist: str | None, title: str) -> str | None:
    """Fetch lyrics for `artist`/`title` from the lyrics API (cached)."""
    key = _lyrics_key(artist, title)
    now = time.monotonic()
    hit = _lyrics_cache.get(key)
    if hit is not None and now - hit[0] < LYRICS_CACHE_TTL:
        return hit[1]

    lyrics: str | None = None
    if artist:
        url = f"{LYRICS_API_BASE}/{quote(artist)}/{quote(title)}"
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    lyrics = data.get("lyrics")
        except Exception:
            logger.debug("lyrics API request failed for %s", key, exc_info=True)

    _lyrics_cache[key] = (now, lyrics)
    return lyrics


async def download_handler(client: Client, message) -> None:
    chat_id = message.chat.id
    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text("❌ Usage: /download <url_or_query>")
        return

    input_source = parts[1].strip()
    rl = getattr(client, "rate_limiter", None)
    if rl is not None and message.from_user is not None:
        try:
            user_key = f"ratelimit:download:user:{message.from_user.id}"
            chat_key = f"ratelimit:download:chat:{chat_id}"
            if not await rl.allow(user_key, limit=3, period=60):
                await message.reply_text("⏱️ You're making download requests too quickly. Try again later.")
                return
            if not await rl.allow(chat_key, limit=10, period=60):
                await message.reply_text("⏱️ This chat is rate-limited for downloads. Try again later.")
                return
        except Exception:
            pass

    await message.reply_text("⏳ Resolving track...")
    try:
        chosen_track, _playable_url, preferred_provider, search_results = await _resolve_track(
            client, message, input_source
        )
        if await _offer_picker(
            client, message, chat_id, preferred_provider, search_results, action="download"
        ):
            return
        track = search_results[0] if search_results else chosen_track
        if message.from_user is not None:
            track.requested_by = message.from_user.id

        await message.reply_text("⏳ Downloading audio...")
        await deliver_audio(client, chat_id, track)
        await message.reply_text("⬇️ Download sent.")
    except ValueError as e:
        await message.reply_text(f"❌ {e}")
    except Exception:
        logger.exception("download failed for chat=%s source=%s", chat_id, input_source)
        await message.reply_text("❌ Download failed. Try again later.")


def register(app: Client) -> None:
    app.add_handler(MessageHandler(lyrics_handler, filters=filters.command(["lyrics", "ly"])))
    app.add_handler(MessageHandler(download_handler, filters=filters.command(["download", "dl"])))
