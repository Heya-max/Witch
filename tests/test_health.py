import os
import sys

import app.bot.handlers.health as health_mod
import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class FakeMessage:
    def __init__(self, chat_id):
        self.chat = type("C", (), {"id": chat_id})
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)
        return text


class FakePlayer:
    def __init__(self):
        self.state = type("S", (), {"value": 2})()  # Playing
        self.current = type("C", (), {"title": "Now Playing"})()
        self.queue = type("Q", (), {"list": _async_list})()


class FakePlayerManager:
    async def get_player(self, chat_id):
        return FakePlayer()


async def _async_list():
    return []


class FakeClient:
    def __init__(self, *, connected=True, voice=True, player_manager=True, db=None, redis=None):
        self.is_connected = connected
        self.voice = object() if voice else None
        self.player_manager = FakePlayerManager() if player_manager else None
        self.db_engine = db
        self.redis = redis


@pytest.mark.asyncio
async def test_health_handler_reports_all_ok():
    from app.bot.handlers.health import health_handler

    client = FakeClient()
    msg = FakeMessage(chat_id=1)
    await health_handler(client, msg)

    assert msg.replies
    assert "ALL OK" in msg.replies[0]
    assert "client" in msg.replies[0]
    assert "db" in msg.replies[0]
    assert "redis" in msg.replies[0]
    assert "now playing: Now Playing" in msg.replies[0]


@pytest.mark.asyncio
async def test_health_handler_reports_unhealthy_when_disconnected():
    from app.bot.handlers.health import health_handler

    client = FakeClient(connected=False)
    msg = FakeMessage(chat_id=1)
    await health_handler(client, msg)

    assert msg.replies
    assert "STATUS UNHEALTHY" in msg.replies[0]
    assert "client: DISCONNECTED" in msg.replies[0]


@pytest.mark.asyncio
async def test_health_handler_tolerates_optional_services():
    from app.bot.handlers.health import health_handler

    # DB and Redis both optional: their absence is healthy by design
    client = FakeClient(db=None, redis=None)
    msg = FakeMessage(chat_id=1)
    await health_handler(client, msg)

    assert msg.replies
    assert "ALL OK" in msg.replies[0]
    assert "db" in msg.replies[0]
    assert "redis" in msg.replies[0]


@pytest.mark.asyncio
async def test_health_handler_reports_voice_disabled():
    from app.bot.handlers.health import health_handler

    client = FakeClient(voice=False)
    msg = FakeMessage(chat_id=1)
    await health_handler(client, msg)

    assert "voice: DISABLED" in msg.replies[0]
    assert "STATUS UNHEALTHY" in msg.replies[0]


def test_health_register_adds_handler():
    class FakeClient:
        def __init__(self):
            self._added = None

        def add_handler(self, handler, **kwargs):
            self._added = handler

    client = FakeClient()
    health_mod.register(client)
    assert client._added is not None
