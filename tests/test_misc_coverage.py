"""Coverage for logging, bot client factory, handler registrations, help/start, and inline/health extras."""

import asyncio
import logging
import os

import app.bot.client as client_mod
import app.bot.handlers as handlers_mod
import app.bot.handlers.favorites as h_fav
import app.bot.handlers.health as h_health
import app.bot.handlers.help as h_help
import app.bot.handlers.inline as h_inline
import app.bot.handlers.media as h_media
import app.bot.handlers.playback as h_playback
import app.bot.handlers.start as h_start
import app.logging as logging_mod
from app.player.models import Track


def _run(coro):
    return asyncio.run(coro)


class FakeApp:
    def __init__(self):
        self.handlers = []

    def add_handler(self, handler):
        self.handlers.append(handler)


def test_configure_logging_restores_root(monkeypatch):
    root = logging.getLogger()
    before_handlers = list(root.handlers)
    before_level = root.level
    try:
        logging_mod.configure_logging("debug")
        assert root.level == logging.DEBUG
        assert len(root.handlers) == 1
        assert root.handlers[0].level == logging.NOTSET
    finally:
        root.handlers[:] = before_handlers
        root.setLevel(before_level)


def test_create_bot_client_requires_only_token(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(client_mod, "Client", FakeClient)
    minimal = type("S", (), {"BOT_TOKEN": "tok", "API_ID": None, "API_HASH": None})()
    client_mod.create_bot_client(minimal)
    assert calls[0][1]["bot_token"] == "tok"
    assert "api_id" not in calls[0][1]
    assert calls[0][1]["workdir"] == os.getcwd()

    full = type("S", (), {"BOT_TOKEN": "tok", "API_ID": 123, "API_HASH": "hash"})()
    client_mod.create_bot_client(full)
    assert calls[1][1]["api_id"] == 123
    assert calls[1][1]["api_hash"] == "hash"


def test_register_handlers(monkeypatch):
    called = []

    for mod in (h_start, h_help, h_health, h_playback, h_media, h_fav, h_inline):
        monkeypatch.setattr(mod, "register", lambda app: called.append(app))

    handlers_mod.register_handlers("APP")
    assert called == ["APP"] * 7


class _CapturingMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


def test_help_handler():
    text = h_help.help_text()
    assert "/play <query>" in text
    assert "/health" in text

    app = FakeApp()
    h_help.register(app)
    assert len(app.handlers) == 1

    message = _CapturingMessage()
    _run(h_help.help_handler("client", message))
    assert message.replies == [h_help.help_text()]


def test_start_handler():
    assert "👋" in h_start.start_text()

    app = FakeApp()
    h_start.register(app)
    assert len(app.handlers) == 1

    message = _CapturingMessage()
    _run(h_start.start_handler("client", message))
    assert message.replies == [h_start.start_text()]


def test_health_ok_and_error_paths():
    class _OkConn:
        async def execute(self, query):
            return None

        async def run_sync(self, fn):
            return fn(None)

    class _OkEngine:
        def connect(self):
            return self

        async def __aenter__(self):
            return _OkConn()

        async def __aexit__(self, *exc):
            return False

    class _BrokenEngine:
        def connect(self):
            raise RuntimeError("db down")

    class _RedisOk:
        async def ping(self):
            return True

    class _RedisDown:
        async def ping(self):
            raise RuntimeError("redis down")

    ok_app = type("A", (), {"db_engine": _OkEngine(), "redis": _RedisOk()})()
    assert _run(h_health._db_ok(ok_app)) is True
    assert _run(h_health._redis_ok(ok_app)) is True

    broken_app = type("A", (), {"db_engine": _BrokenEngine(), "redis": _RedisDown()})()
    assert _run(h_health._db_ok(broken_app)) is False
    assert _run(h_health._redis_ok(broken_app)) is False


def test_fmt_duration_bad_inputs():
    assert h_inline._fmt_duration(None) == ""
    assert h_inline._fmt_duration("not-a-number") == ""
    assert h_inline._fmt_duration(125) == "2:05"


class FakeInlineQuery:
    def __init__(self, text):
        self.query = text
        self.answers = []

    async def answer(self, results, **kwargs):
        self.answers.append((results, kwargs))


class _GoodProvider:
    def __init__(self, count, raise_on=False):
        self.count = count
        self.raise_on = raise_on
        self.searched = 0

    async def search(self, q):
        self.searched += 1
        if self.raise_on:
            raise RuntimeError("inline provider failed")
        return [Track(id=f"t{i}", title=f"Song {i}", artist="A", duration=i) for i in range(self.count)]


def test_inline_query_clamps_results(monkeypatch):
    provider = _GoodProvider(11)
    monkeypatch.setattr(h_inline, "get_default_providers", lambda: [provider])
    client = type("C", (), {})()
    query = FakeInlineQuery("some song")
    _run(h_inline.inline_query_handler(client, query))
    (results, kwargs) = query.answers[0]
    assert len(results) == h_inline.MAX_INLINE_RESULTS
    assert kwargs == {"cache_time": 10, "is_personal": True}
    assert client.pending_inline


def test_inline_query_skips_failing_provider(monkeypatch):
    bad = _GoodProvider(0, raise_on=True)
    good = _GoodProvider(1)
    monkeypatch.setattr(h_inline, "get_default_providers", lambda: [bad, good])
    client = type("C", (), {})()
    query = FakeInlineQuery("q")
    _run(h_inline.inline_query_handler(client, query))
    (results, kwargs) = query.answers[0]
    assert len(results) == 1
    assert results[0].title.startswith("Song")


def test_inline_query_outer_break_at_limit(monkeypatch):
    first = _GoodProvider(h_inline.MAX_INLINE_RESULTS)
    second = _GoodProvider(1)
    monkeypatch.setattr(h_inline, "get_default_providers", lambda: [first, second])
    client = type("C", (), {})()
    query = FakeInlineQuery("q")
    _run(h_inline.inline_query_handler(client, query))
    assert second.searched == 0
    assert len(query.answers[0][0]) == h_inline.MAX_INLINE_RESULTS


class FakeChosenResult:
    def __init__(self, result_id, user):
        self.result_id = result_id
        self.from_user = user

class FakeUser:
    id = 123


def test_chosen_inline_delivery_failure_logged(monkeypatch):
    calls = []

    async def failing_deliver(client, user_id, track):
        calls.append((user_id, track.id))
        raise RuntimeError("delivery failed")

    monkeypatch.setattr(h_inline, "deliver_audio", failing_deliver)
    picks = {"tok": (_GoodProvider(0), Track(id="x", title="X"))}
    client = type("C", (), {"pending_inline": picks})()
    result = FakeChosenResult("tok", FakeUser())
    _run(h_inline.chosen_inline_result_handler(client, result))
    assert calls == [(123, "x")]
    assert "tok" not in picks
