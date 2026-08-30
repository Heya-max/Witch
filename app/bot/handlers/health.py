import logging

from pyrogram import Client, filters
from pyrogram.types import Message
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def _db_ok(app: Client) -> bool:
    engine = getattr(app, "db_engine", None)
    if engine is None:
        return True  # DB is optional; in-memory queues are a valid config
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _redis_ok(app: Client) -> bool:
    redis = getattr(app, "redis", None)
    if redis is None:
        return True  # Redis is optional; rate limiting/locking degrade gracefully
    try:
        return bool(await redis.ping())
    except Exception:
        return False


async def health_handler(client: Client, message: Message) -> None:
    """Report the liveness of the bot's subsystems (for debugging after a reboot)."""
    lines: list[str] = []
    lines.append("client" if client.is_connected else "client: DISCONNECTED")
    lines.append("db" if await _db_ok(client) else "db: ERROR")
    lines.append("redis" if await _redis_ok(client) else "redis: ERROR")
    voice = getattr(client, "voice", None)
    lines.append("voice" if voice is not None else "voice: DISABLED")

    chat_id = message.chat.id
    pm = getattr(client, "player_manager", None)
    if pm is not None:
        try:
            player = await pm.get_player(chat_id)
            player_state = player.state.value if player.state is not None else "?"
            lines.append(f"player state: {player_state}")
            current = player.current
            if current is not None and current.title:
                lines.append(f"now playing: {current.title}")
            lines.append(f"queued tracks: {len(await player.queue.list())}")
        except Exception:
            logger.exception("health check: player introspection failed")

    ok = all(not line.endswith(("ERROR", "DISCONNECTED", "DISABLED")) for line in lines)
    await message.reply_text("\n".join([("ALL OK" if ok else "STATUS UNHEALTHY"), *lines]))


def register(app: Client) -> None:
    app.add_handler(__import__("pyrogram").handlers.MessageHandler(health_handler, filters=filters.command("health")))
