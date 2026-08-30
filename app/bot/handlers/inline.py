import base64
import logging
import os

from pyrogram import Client
from pyrogram.handlers import ChosenInlineResultHandler, InlineQueryHandler
from pyrogram.types import (
    ChosenInlineResult,
    InlineQuery,
    InlineQueryResult,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from ...sources import get_default_providers
from .media import deliver_audio

logger = logging.getLogger(__name__)

MAX_INLINE_RESULTS = 10


def _fmt_duration(seconds) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ""
    m, s = divmod(max(0, seconds), 60)
    return f"{m}:{s:02d}"


def _description(track) -> str:
    parts = []
    if track.artist:
        parts.append(track.artist)
    if track.duration:
        parts.append(_fmt_duration(track.duration))
    return " • ".join(parts)


async def inline_query_handler(client: Client, query: InlineQuery) -> None:
    q = (query.query or "").strip()
    if not q:
        await query.answer([], cache_time=60)
        return

    picks = getattr(client, "pending_inline", None)
    if picks is None:
        picks = {}
        setattr(client, "pending_inline", picks)  # noqa: B010 - mypy blocks plain attribute assignment

    results: list[InlineQueryResult] = []
    for provider in get_default_providers():
        try:
            found = await provider.search(q)
        except Exception:
            logger.debug("inline search failed for %s", type(provider).__name__, exc_info=True)
            continue
        for track in found:
            if len(results) >= MAX_INLINE_RESULTS:
                break
            token = base64.urlsafe_b64encode(os.urandom(6)).decode().rstrip("=")
            picks[token] = (provider, track)
            results.append(
                InlineQueryResultArticle(
                    id=token,
                    title=track.title[:64],
                    description=_description(track)[:64] or "",
                    thumb_url=track.thumbnail,
                    input_message_content=InputTextMessageContent(
                        message_text=f"🎧 {track.title}"
                        + (f" — {track.artist}" if track.artist else "")
                        + (f" [{_fmt_duration(track.duration)}]" if track.duration else "")
                    ),
                )
            )
        if len(results) >= MAX_INLINE_RESULTS:
            break

    await query.answer(results, cache_time=10, is_personal=True)


async def chosen_inline_result_handler(client: Client, result: ChosenInlineResult) -> None:
    """Deliver the picked track as an audio message to the sender's private chat."""
    picks = getattr(client, "pending_inline", None) or {}
    token = result.result_id
    entry = picks.pop(token, None)
    if entry is None or result.from_user is None:
        return
    _provider, track = entry
    try:
        await deliver_audio(client, result.from_user.id, track)
    except Exception:
        logger.debug("could not deliver inline audio to user %s", result.from_user.id, exc_info=True)


def register(app: Client) -> None:
    app.add_handler(InlineQueryHandler(inline_query_handler))
    app.add_handler(ChosenInlineResultHandler(chosen_inline_result_handler))
