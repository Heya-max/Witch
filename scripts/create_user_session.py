"""Interactive userbot assistant session creator.

Creates a ``*.session`` file (or prints an in-memory session string) for the
optional userbot that drives voice playback. Bots cannot create Telegram group
calls, so this user account is what lets the bot open and stream into voice
chats automatically.

Usage (run in your own terminal; it will ask for phone + code + 2FA password):

    python scripts/create_user_session.py            # writes user.session
    python scripts/create_user_session.py --string   # prints a session string

Credentials come from USERBOT_API_ID/USERBOT_API_HASH, falling back to
API_ID/API_HASH in your .env.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyrogram import Client  # noqa: E402


def _credentials():
    from app.config import get_settings

    settings = get_settings()
    api_id = settings.USERBOT_API_ID or settings.API_ID
    api_hash = settings.USERBOT_API_HASH or settings.API_HASH
    if not api_id or not api_hash:
        raise SystemExit("Set USERBOT_API_ID/USERBOT_API_HASH (or API_ID/API_HASH) first.")
    return api_id, api_hash


async def _run(name: str, export_string: bool) -> int:
    api_id, api_hash = _credentials()
    print(f"Logging in as a Telegram user to create session '{name}'...")
    print("Enter your phone number and the login code when prompted.")
    client = Client(name, api_id=api_id, api_hash=api_hash, workdir=os.getcwd())
    await client.start()
    try:
        if export_string:
            token = await client.export_session_string()
            print("\n--- session string (set USERBOT_SESSION_STRING to this) ---")
            print(token)
            print("----------------------------------------------------------------")
        else:
            print(f"\nSession saved to {name}.session")
            print(f"Set USERBOT_SESSION={name} in your .env to enable the assistant.")
    finally:
        await client.stop()
    return 0


def main() -> int:
    export_string = "--string" in sys.argv
    name = "user"
    for arg in sys.argv[1:]:
        if not arg.startswith("--"):
            name = arg
    return asyncio.run(_run(name, export_string))


if __name__ == "__main__":
    raise SystemExit(main())
