import os
import sys

import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class FakeMessage:
    def __init__(self, chat_id, user_id=None):
        self.chat = type("C", (), {"id": chat_id})
        self.from_user = type("U", (), {"id": user_id}) if user_id else None
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)
        return text


class FakePlayer:
    def __init__(self, items=None, current=None):
        self.queue = type("Q", (), {})()
        self.queue._items = items or []

        async def list_fn():
            return list(self.queue._items)

        self.queue.list = list_fn
        self.current = current


class FakePlayerManager:
    def __init__(self, player):
        self._player = player

    async def get_player(self, chat_id):
        return self._player


@pytest.mark.asyncio
async def test_queue_handler_empty():
    from app.bot.handlers.playback import queue_handler

    fake_player = FakePlayer(items=[])
    pm = FakePlayerManager(fake_player)

    client = type("C", (), {"player_manager": pm})
    msg = FakeMessage(chat_id=111)

    await queue_handler(client, msg)
    assert msg.replies
    assert "empty" in msg.replies[0].lower()


@pytest.mark.asyncio
async def test_queue_handler_with_items():
    from app.bot.handlers.playback import queue_handler
    from app.player.models import Track

    t1 = Track(id="a", title="Song A")
    t2 = Track(id="b", title="Song B")
    fake_player = FakePlayer(items=[t1, t2])
    pm = FakePlayerManager(fake_player)

    client = type("C", (), {"player_manager": pm})
    msg = FakeMessage(chat_id=222)

    await queue_handler(client, msg)
    assert msg.replies
    assert "Song A" in msg.replies[0]


@pytest.mark.asyncio
async def test_now_playing_handler_none():
    from app.bot.handlers.playback import now_playing_handler

    fake_player = FakePlayer(items=[], current=None)
    pm = FakePlayerManager(fake_player)

    client = type("C", (), {"player_manager": pm})
    msg = FakeMessage(chat_id=333)

    await now_playing_handler(client, msg)
    assert msg.replies
    assert "nothing" in msg.replies[0].lower()


@pytest.mark.asyncio
async def test_now_playing_handler_with_current():
    from app.bot.handlers.playback import now_playing_handler
    from app.player.models import Track

    t = Track(id="x", title="Current Song", duration=200, requested_by=42)
    fake_player = FakePlayer(items=[t], current=t)
    pm = FakePlayerManager(fake_player)

    client = type("C", (), {"player_manager": pm})
    msg = FakeMessage(chat_id=444)

    await now_playing_handler(client, msg)
    assert msg.replies
    assert "Current Song" in msg.replies[0]
