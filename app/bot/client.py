import os
from typing import Any

from pyrogram import Client

from ..config import Settings


def create_bot_client(settings: Settings) -> Client:
    """Create and return a Pyrogram Client for the bot.

    Uses `bot_token` mode. `api_id`/`api_hash` are optional for bot-only operation.

    `workdir` is pinned to the process working directory (which is writable in the
    container) because some Pyrogram distributions derive the default workdir from
    `sys.argv[0]`, e.g. ``/home/app/app``, which is not writable by the app user.
    """
    kwargs: dict[str, Any] = {"bot_token": settings.BOT_TOKEN}
    if settings.API_ID is not None:
        kwargs["api_id"] = settings.API_ID
    if settings.API_HASH is not None:
        kwargs["api_hash"] = settings.API_HASH

    return Client("bot", **kwargs, workdir=os.getcwd())  # type: ignore[arg-type]
