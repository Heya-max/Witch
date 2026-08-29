import asyncio
import contextlib
import logging

from pyrogram import idle

from .bot.client import create_bot_client
from .bot.handlers import register_handlers
from .config import get_settings
from .logging import configure_logging

logger = logging.getLogger(__name__)


def _init_services(app, settings) -> None:
    """Attach Redis-backed services, metrics, and the DB session factory."""
    try:
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(settings.REDIS_URL)
        app.redis = redis_client

        from .services.locks import RedisLock
        from .services.rate_limiter import RateLimiter

        app.rate_limiter = RateLimiter(redis_client)
        app.locks = RedisLock(redis_client)

        from .services.metrics import Metrics

        app.metrics = Metrics()

        if settings.METRICS_PORT:
            try:
                from prometheus_client import Counter, start_http_server

                from .services.metrics import set_prometheus_counter

                prom_counter = Counter("app_events_total", "Application event counters", ["name"])
                set_prometheus_counter(prom_counter)
                start_http_server(settings.METRICS_PORT, addr="127.0.0.1")
            except Exception:
                logger.warning("prometheus metrics server failed to start", exc_info=True)
    except Exception:
        logger.warning("Redis unavailable; rate limiting and locking disabled", exc_info=True)

    try:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        engine = create_async_engine(settings.DATABASE_URL)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        app.db_engine = engine
        app.db_session_factory = session_factory
    except Exception:
        logger.warning("could not initialise database; queue will be in-memory", exc_info=True)


async def _db_is_ready(app, settings) -> bool:
    """Check that migrations have been applied (queue_entries table exists)."""
    session_factory = getattr(app, "db_session_factory", None)
    if session_factory is None:
        return False
    try:
        from sqlalchemy import inspect

        engine = getattr(app, "db_engine", None)
        if engine is None:
            return False
        async with engine.connect() as conn:

            def has_table(sync_conn) -> bool:
                return inspect(sync_conn).has_table("queue_entries")

            return bool(await conn.run_sync(has_table))
    except Exception:
        logger.debug("database readiness check failed", exc_info=True)
        return False


def _init_voice(app, session_factory=None) -> None:
    """Attach VoiceManager/PlayerManager to the client (best-effort)."""
    try:
        from .player.voice import VoiceManager

        voice = VoiceManager(app)
        app.voice = voice

        from .player.manager import PlayerManager

        app.player_manager = PlayerManager(voice, session_factory=session_factory)
    except Exception:
        logger.warning(
            "could not initialize VoiceManager; voice commands will be disabled",
            exc_info=True,
        )


async def _run_bot(app, settings) -> None:
    # Use the persisted queue only if the DB schema is present; otherwise fall
    # back to in-memory queues so the bot still works without migrations.
    session_factory = None
    if await _db_is_ready(app, settings):
        session_factory = getattr(app, "db_session_factory", None)
    else:
        logger.warning("database tables not found; run `alembic upgrade head`. Falling back to in-memory queues.")
    _init_voice(app, session_factory)

    await app.start()

    # Start the voice manager (PyTgCalls) only after the client is up.
    try:
        voice = getattr(app, "voice", None)
        if voice is not None:
            await voice.start()
    except Exception:
        logger.warning(
            "could not start voice manager; voice commands will be disabled",
            exc_info=True,
        )

    # Block until interrupted, processing updates.
    await idle()

    try:
        voice = getattr(app, "voice", None)
        if voice is not None:
            await voice.stop()
    except Exception:
        logger.warning("error stopping voice manager", exc_info=True)

    await app.stop()


def main() -> int:
    try:
        settings = get_settings()
    except SystemExit as e:
        print(e)
        return 1

    configure_logging(settings.LOG_LEVEL)

    # Create and run Pyrogram bot client
    app = create_bot_client(settings)
    app.settings = settings  # type: ignore[attr-defined]
    register_handlers(app)

    _init_services(app, settings)

    print("Starting bot (press Ctrl+C to stop)...")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run_bot(app, settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
