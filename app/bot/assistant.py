import logging
import os
from typing import Any

from pyrogram import Client

from ..config import Settings

logger = logging.getLogger(__name__)


def create_assistant_client(settings: Settings) -> Client | None:
    """Create the optional userbot client that drives voice playback.

    Bots cannot create Telegram group calls, so when a userbot session is
    configured the assistant's PyTgCalls is used to join/start voice chats and
    stream audio (auto-start then works for the user account).

    Returns None when the feature is not configured or its credentials are
    incomplete.
    """
    if not (settings.USERBOT_SESSION or settings.USERBOT_SESSION_STRING):
        return None

    api_id = settings.USERBOT_API_ID or settings.API_ID
    api_hash = settings.USERBOT_API_HASH or settings.API_HASH
    if not (api_id and api_hash):
        logger.warning(
            "USERBOT_SESSION is set but API_ID/API_HASH are missing; "
            "voice will use the bot account (manual VC start required)"
        )
        return None

    kwargs: dict[str, Any] = {"api_id": api_id, "api_hash": api_hash}
    if settings.USERBOT_SESSION_STRING:
        name = "userbot"
        kwargs["session_string"] = settings.USERBOT_SESSION_STRING
    else:
        name = str(settings.USERBOT_SESSION)

    return Client(name, **kwargs, workdir=os.getcwd())  # type: ignore[arg-type]
