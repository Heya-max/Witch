import asyncio
import contextlib
import logging

from pyrogram import idle

from .bot.assistant import create_assistant_client
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
                # Bind on 0.0.0.0 so the endpoint is reachable inside Docker
                # (Prometheus-style scrapers); the compose file publishes it.
                start_http_server(settings.METRICS_PORT, addr="0.0.0.0")
                logger.info("prometheus metrics listening on :%s", settings.METRICS_PORT)
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

    assistant = create_assistant_client(settings)
    if assistant is not None:
        app.assistant = assistant


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
        from .player.manager import PlayerManager
        from .player.voice import VoiceManager
        from .sources import resolve_playable

        assistant = getattr(app, "assistant", None)
        voice = VoiceManager(app, assistant=assistant)
        app.voice = voice

        settings = getattr(app, "settings", None)
        max_queue_size = getattr(settings, "QUEUE_MAX_SIZE", 200)
        max_retries = getattr(settings, "PLAY_MAX_RETRIES", 2)
        app.player_manager = PlayerManager(
            voice,
            session_factory=session_factory,
            resolver=resolve_playable,
            max_queue_size=max_queue_size,
            max_retries=max_retries,
        )
    except Exception:
        logger.warning(
            "could not initialize VoiceManager; voice commands will be disabled",
            exc_info=True,
        )


async def _prune_stale_queues(app, settings) -> None:
    """Drop persisted queue rows older than the startup TTL (best-effort)."""
    session_factory = getattr(app, "db_session_factory", None)
    ttl = getattr(settings, "QUEUE_MAX_AGE_SECONDS", 86400)
    if session_factory is None or not ttl:
        return
    try:
        from .player.queue import purge_stale_persisted_entries

        removed = await purge_stale_persisted_entries(session_factory, ttl)
        if removed:
            logger.info("pruned %s stale queue entries older than %ss", removed, ttl)
    except Exception:
        logger.warning("queue pruning failed (continuing with best-effort cleanup)", exc_info=True)


async def _run_bot(app, settings) -> None:
    # Use the persisted queue only if the DB schema is present; otherwise fall
    # back to in-memory queues so the bot still works without migrations.
    session_factory = None
    if await _db_is_ready(app, settings):
        session_factory = getattr(app, "db_session_factory", None)
    else:
        logger.warning("database tables not found; run `alembic upgrade head`. Falling back to in-memory queues.")

    # Clear stale queue state from earlier runs before any player is created.
    await _prune_stale_queues(app, settings)

    await app.start()

    # Start the optional userbot assistant before the voice manager so its
    # PyTgCalls instance is bound to a running client. If it fails, fall back
    # to bot-account voice (manual VC start required).
    assistant = getattr(app, "assistant", None)
    if assistant is not None:
        try:
            await assistant.start()
            logger.info("userbot assistant started")
        except Exception:
            logger.warning("failed to start userbot assistant; voice will use the bot account", exc_info=True)
            app.assistant = None

    _init_voice(app, session_factory)

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

    # Block until interrupted, processing updates. Wrap teardown in
    # try/finally so the player's background tasks and voice stream are torn
    # down cleanly even if idle() raises.
    try:
        await idle()
    finally:
        # Cancel per-chat playback tasks and clear in-memory queues first so
        # nothing spins after the clients stop.
        player_manager = getattr(app, "player_manager", None)
        if player_manager is not None:
            try:
                await player_manager.shutdown()
            except Exception:
                logger.warning("error shutting down players", exc_info=True)

        try:
            voice = getattr(app, "voice", None)
            if voice is not None:
                await voice.stop()
        except Exception:
            logger.warning("error stopping voice manager", exc_info=True)

        if assistant is not None:
            try:
                await assistant.stop()
            except Exception:
                logger.warning("error stopping userbot assistant", exc_info=True)

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
    raise SystemExit(main())  # pragma: no cover
