"""Coverage for app/main.py wiring: services, DB readiness, voice init, bot run."""

import asyncio

import app.main as main_mod
import app.services.favorites  # noqa: F401 - keep redis import side effects stable
import app.services.locks as locks_mod
import app.services.metrics as metrics_mod
import app.services.rate_limiter as rate_limiter_mod
import prometheus_client
import pytest
import redis.asyncio as redis_async
import sqlalchemy
import sqlalchemy.ext.asyncio as sqla_ext


class Settings:
    def __init__(self, **kw):
        self.REDIS_URL = kw.get("REDIS_URL", "redis://localhost:6379")
        self.METRICS_PORT = kw.get("METRICS_PORT", 0)
        self.DATABASE_URL = kw.get("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
        self.LOG_LEVEL = "INFO"
        self.QUEUE_MAX_SIZE = kw.get("QUEUE_MAX_SIZE", 200)
        self.PLAY_MAX_RETRIES = kw.get("PLAY_MAX_RETRIES", 2)
        self.QUEUE_MAX_AGE_SECONDS = kw.get("QUEUE_MAX_AGE_SECONDS", 86400)


def _app(**attrs):
    app = type("App", (), {})()
    for k, v in attrs.items():
        setattr(app, k, v)
    return app


class FakeRedis:
    def __init__(self, url):
        self.url = url


class FakeRateLimiter:
    def __init__(self, client):
        self.client = client


class FakeRedisLock:
    def __init__(self, client):
        self.client = client


class FakeMetrics:
    def __init__(self):
        self.init = True


class FakeAssistant:
    def __init__(self, start_ok=True, stop_ok=True):
        self.start_ok = start_ok
        self.stop_ok = stop_ok
        self.started = False
        self.stopped = False

    async def start(self):
        if not self.start_ok:
            raise RuntimeError("assistant start boom")
        self.started = True

    async def stop(self):
        if not self.stop_ok:
            raise RuntimeError("assistant stop boom")
        self.stopped = True


def test_init_services_happy_path(monkeypatch):
    from_url_calls = []
    start_http_calls = []
    counter_seen = []

    def fake_from_url(url):
        from_url_calls.append(url)
        return FakeRedis(url)

    def fake_start_http_server(port, addr):
        start_http_calls.append((port, addr))

    def fake_set_counter(counter):
        counter_seen.append(counter)

    class FakeEngine:
        pass

    monkeypatch.setattr(redis_async, "from_url", fake_from_url)
    monkeypatch.setattr(rate_limiter_mod, "RateLimiter", FakeRateLimiter)
    monkeypatch.setattr(locks_mod, "RedisLock", FakeRedisLock)
    monkeypatch.setattr(metrics_mod, "Metrics", FakeMetrics)
    monkeypatch.setattr(metrics_mod, "set_prometheus_counter", fake_set_counter)
    monkeypatch.setattr(prometheus_client, "start_http_server", fake_start_http_server)
    monkeypatch.setattr(prometheus_client, "Counter", lambda name, document, labels: object())
    monkeypatch.setattr(sqla_ext, "create_async_engine", lambda url: FakeEngine())
    monkeypatch.setattr(main_mod, "create_assistant_client", lambda settings: FakeAssistant())

    app = _app()
    main_mod._init_services(app, Settings(METRICS_PORT=8000, DATABASE_URL="postgresql+asyncpg://x"))
    assert from_url_calls == ["redis://localhost:6379"]
    assert isinstance(app.rate_limiter, FakeRateLimiter)
    assert isinstance(app.locks, FakeRedisLock)
    assert isinstance(app.metrics, FakeMetrics)
    assert app.metrics.init is True
    assert start_http_calls == [(8000, "0.0.0.0")]
    assert counter_seen
    assert hasattr(app, "db_engine")
    assert hasattr(app, "db_session_factory")
    assert isinstance(app.assistant, FakeAssistant)


def test_init_services_redis_metrics_and_db_failures(monkeypatch):
    def failing_from_url(url):
        raise RuntimeError("redis down")

    monkeypatch.setattr(redis_async, "from_url", failing_from_url)
    monkeypatch.setattr(main_mod, "create_assistant_client", lambda settings: None)

    app = _app()
    main_mod._init_services(app, Settings(METRICS_PORT=8000))
    assert not hasattr(app, "redis")
    assert not hasattr(app, "rate_limiter")
    # DB init happens independently of Redis.
    assert hasattr(app, "db_session_factory")

    # Metrics server failing to start should not take the app down with it.
    def boom_http_server(port, addr):
        raise OSError("port in use")

    def good_from_url(url):
        return FakeRedis(url)

    monkeypatch.setattr(redis_async, "from_url", good_from_url)
    monkeypatch.setattr(prometheus_client, "start_http_server", boom_http_server)
    monkeypatch.setattr(rate_limiter_mod, "RateLimiter", FakeRateLimiter)
    monkeypatch.setattr(locks_mod, "RedisLock", FakeRedisLock)
    monkeypatch.setattr(metrics_mod, "Metrics", FakeMetrics)

    app2 = _app()
    main_mod._init_services(app2, Settings(METRICS_PORT=8000))
    assert isinstance(app2.rate_limiter, FakeRateLimiter)

    # Database engine failure degrades gracefully.
    def failing_engine(url):
        raise RuntimeError("sql down")

    monkeypatch.setattr(sqla_ext, "create_async_engine", failing_engine)
    app3 = _app()
    main_mod._init_services(app3, Settings())
    assert isinstance(app3.rate_limiter, FakeRateLimiter)
    assert not hasattr(app3, "db_engine")


@pytest.mark.asyncio
async def test_db_is_ready_paths(monkeypatch):
    assert await main_mod._db_is_ready(_app(), Settings()) is False
    assert await main_mod._db_is_ready(_app(db_session_factory=object()), Settings()) is False

    class FakeConn:
        async def run_sync(self, fn):
            return fn(None)

    class FakeEngine:
        def connect(self):
            return self._ctx()

        class _ctx:
            async def __aenter__(self):
                return FakeConn()

            async def __aexit__(self, *exc):
                return False

    class FakeInspector:
        def has_table(self, name):
            return name == "queue_entries"

    monkeypatch.setattr(sqla_ext, "create_async_engine", lambda url: FakeEngine())
    monkeypatch.setattr(sqlalchemy, "inspect", lambda conn: FakeInspector())
    ready_app = _app(db_session_factory=object(), db_engine=FakeEngine())
    assert await main_mod._db_is_ready(ready_app, Settings()) is True

    class BrokenEngine:
        def connect(self):
            raise RuntimeError("db gone")

    broken_app = _app(db_session_factory=object(), db_engine=BrokenEngine())
    assert await main_mod._db_is_ready(broken_app, Settings()) is False


def test_init_voice_success(monkeypatch):
    import app.player.manager as manager_mod
    import app.player.voice as voice_mod

    class FakeVoiceManager:
        def __init__(self, app, assistant=None):
            self.app = app
            self.assistant = assistant

    class FakePlayerManager:
        def __init__(self, voice, session_factory=None, resolver=None, max_queue_size=200, max_retries=2):
            self.voice = voice
            self.session_factory = session_factory
            self.max_queue_size = max_queue_size
            self.max_retries = max_retries

    monkeypatch.setattr(voice_mod, "VoiceManager", FakeVoiceManager)
    monkeypatch.setattr(manager_mod, "PlayerManager", FakePlayerManager)

    app = _app(assistant=FakeAssistant(), settings=Settings(QUEUE_MAX_SIZE=50, PLAY_MAX_RETRIES=5))
    session_factory = object()
    main_mod._init_voice(app, session_factory)
    assert isinstance(app.voice, FakeVoiceManager)
    assert app.player_manager.max_queue_size == 50
    assert app.player_manager.max_retries == 5
    assert app.player_manager.session_factory is session_factory


def test_init_voice_failure_disables_voice(monkeypatch):
    import app.player.voice as voice_mod

    class BoomVoiceManager:
        def __init__(self, app, assistant=None):
            raise RuntimeError("pytgcalls init failed")

    monkeypatch.setattr(voice_mod, "VoiceManager", BoomVoiceManager)
    app = _app(settings=Settings())
    main_mod._init_voice(app, None)
    assert not hasattr(app, "voice")
    assert not hasattr(app, "player_manager")


@pytest.mark.asyncio
async def test_prune_stale_queues_paths(monkeypatch):
    from app.player import queue as queue_mod

    await main_mod._prune_stale_queues(_app(), Settings())
    await main_mod._prune_stale_queues(_app(db_session_factory=object()), Settings(QUEUE_MAX_AGE_SECONDS=0))

    pruned = []

    async def fake_purge(session_factory, ttl):
        pruned.append(ttl)
        return 3

    monkeypatch.setattr(queue_mod, "purge_stale_persisted_entries", fake_purge)
    await main_mod._prune_stale_queues(_app(db_session_factory=object()), Settings(QUEUE_MAX_AGE_SECONDS=120))
    assert pruned == [120]

    async def failing_purge(session_factory, ttl):
        raise RuntimeError("db busy")

    monkeypatch.setattr(queue_mod, "purge_stale_persisted_entries", failing_purge)
    await main_mod._prune_stale_queues(_app(db_session_factory=object()), Settings())


class FakePlayerManager:
    def __init__(self, shutdown_ok=True):
        self.shutdown_ok = shutdown_ok
        self.shutdown_called = False

    async def shutdown(self):
        if not self.shutdown_ok:
            raise RuntimeError("shutdown boom")
        self.shutdown_called = True


class FakeVoiceManager:
    def __init__(self, start_ok=True, stop_ok=True):
        self.start_ok = start_ok
        self.stop_ok = stop_ok
        self.started = False
        self.stopped = False

    async def start(self):
        if not self.start_ok:
            raise RuntimeError("voice start boom")
        self.started = True

    async def stop(self):
        if not self.stop_ok:
            raise RuntimeError("voice stop boom")
        self.stopped = True


class FakeBotApp:
    def __init__(self, assistant=FakeAssistant(), voice=None, player_manager=None, db_session_factory=None):
        self.assistant = assistant
        self.voice = voice
        self.player_manager = player_manager
        self.db_session_factory = db_session_factory
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


def _run_bot_env(monkeypatch, db_ready=True):
    idle_calls = []

    async def fake_idle():
        idle_calls.append(1)

    init_voice = []

    def fake_init_voice(app, session_factory):
        init_voice.append(session_factory)

    async def fake_ready(app, settings):
        return db_ready

    async def fake_prune(app, settings):
        return None

    monkeypatch.setattr(main_mod, "_db_is_ready", fake_ready)
    monkeypatch.setattr(main_mod, "_prune_stale_queues", fake_prune)
    monkeypatch.setattr(main_mod, "_init_voice", fake_init_voice)
    monkeypatch.setattr(main_mod, "idle", fake_idle)
    return idle_calls, init_voice


def test_run_bot_happy_path(monkeypatch):
    idle_calls, init_voice = _run_bot_env(monkeypatch)
    assistant = FakeAssistant()
    voice = FakeVoiceManager()
    player_manager = FakePlayerManager()
    session_factory = object()
    app = FakeBotApp(
        assistant=assistant,
        voice=voice,
        player_manager=player_manager,
        db_session_factory=session_factory,
    )
    asyncio.run(main_mod._run_bot(app, Settings()))
    assert app.started is True
    assert assistant.started is True
    assert init_voice == [session_factory]
    assert voice.started is True
    assert idle_calls == [1]
    assert player_manager.shutdown_called is True
    assert voice.stopped is True
    assert assistant.stopped is True
    assert app.stopped is True

def test_run_bot_db_not_ready(monkeypatch):
    idle_calls, init_voice = _run_bot_env(monkeypatch, db_ready=False)
    app = FakeBotApp(voice=FakeVoiceManager())
    asyncio.run(main_mod._run_bot(app, Settings()))
    assert init_voice == [None]
    assert app.started is True


def test_run_bot_assistant_start_fails(monkeypatch):
    _run_bot_env(monkeypatch)
    app = FakeBotApp(assistant=FakeAssistant(start_ok=False), voice=FakeVoiceManager())
    asyncio.run(main_mod._run_bot(app, Settings()))
    assert app.assistant is None


def test_run_bot_voice_start_fails(monkeypatch):
    _run_bot_env(monkeypatch)
    app = FakeBotApp(voice=FakeVoiceManager(start_ok=False))
    asyncio.run(main_mod._run_bot(app, Settings()))
    assert app.started is True


def test_run_bot_teardown_failures(monkeypatch):
    _run_bot_env(monkeypatch)
    app = FakeBotApp(
        assistant=FakeAssistant(stop_ok=False),
        voice=FakeVoiceManager(stop_ok=False),
        player_manager=FakePlayerManager(shutdown_ok=False),
    )
    asyncio.run(main_mod._run_bot(app, Settings()))
    assert app.stopped is True


def test_main_entrypoint(monkeypatch):
    logged = []
    booted = []
    services_seen = []
    runs = []

    monkeypatch.setattr(main_mod, "get_settings", lambda: Settings())
    monkeypatch.setattr(main_mod, "configure_logging", lambda level: logged.append(level))
    monkeypatch.setattr(main_mod, "create_bot_client", lambda settings: FakeBotApp())
    monkeypatch.setattr(main_mod, "register_handlers", lambda app: booted.append(app))
    monkeypatch.setattr(main_mod, "_init_services", lambda app, settings: services_seen.append(app))
    monkeypatch.setattr(main_mod, "create_assistant_client", lambda settings: None)

    async def fake_run_bot(app, settings):
        runs.append(app)

    monkeypatch.setattr(main_mod, "_run_bot", fake_run_bot)

    assert main_mod.main() == 0
    assert logged == ["INFO"]
    assert booted
    assert services_seen
    assert runs


def test_main_entrypoint_config_error(monkeypatch):
    def bad_settings():
        raise SystemExit("bad config")

    monkeypatch.setattr(main_mod, "get_settings", bad_settings)
    assert main_mod.main() == 1
