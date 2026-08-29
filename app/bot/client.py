from typing import Any

from pyrogram import Client

from ..config import Settings


def create_bot_client(settings: Settings) -> Client:
    """Create and return a Pyrogram Client for the bot.

    Uses `bot_token` mode. `api_id`/`api_hash` are optional for bot-only operation.
    """
    kwargs: dict[str, Any] = {"bot_token": settings.BOT_TOKEN}
    if settings.API_ID is not None:
        kwargs["api_id"] = settings.API_ID
    if settings.API_HASH is not None:
        kwargs["api_hash"] = settings.API_HASH

    return Client("bot", **kwargs)  # type: ignore[arg-type]
