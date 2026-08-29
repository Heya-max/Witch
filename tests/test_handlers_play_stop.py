import os
import sys

import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class FakeMessage:
    def __init__(self, chat_id, user_id=None, text=None):
        self.chat = type("C", (), {"id": chat_id})
        self.from_user = type("U", (), {"id": user_id}) if user_id else None
        self.text = text
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)
        return text


class FakePlayer:
    def __init__(self):
        self.enqueued = []
        self.stopped = False

    async def enqueue(self, track):
        self.enqueued.append(track)
        return len(self.enqueued)

    async def stop(self):
        self.stopped = True


class FakePlayerManager:
    def __init__(self, player):
        self._player = player

    async def get_player(self, chat_id):
        return self._player


@pytest.mark.asyncio
async def test_play_handler_enqueues_with_player_manager():
    import app.bot.handlers.playback as playback_mod
    from app.bot.handlers.playback import play_handler

    # ensure providers list is empty to use fallback path and avoid external calls
    playback_mod.get_default_providers = lambda: []
    from app.player.models import Track

    fake_player = FakePlayer()
    pm = FakePlayerManager(fake_player)
    client = type("C", (), {"player_manager": pm, "voice": object()})

    msg = FakeMessage(chat_id=1, user_id=10, text="/play https://example.com/song.mp3")

    await play_handler(client, msg)

    assert msg.replies
    assert len(fake_player.enqueued) == 1
    assert isinstance(fake_player.enqueued[0], Track)


@pytest.mark.asyncio
async def test_stop_handler_calls_player_stop():
    from app.bot.handlers.playback import stop_handler

    fake_player = FakePlayer()
    pm = FakePlayerManager(fake_player)
    client = type("C", (), {"player_manager": pm, "settings": type("S", (), {"BOT_OWNER_ID": 99})})
    msg = FakeMessage(chat_id=2, user_id=99)

    await stop_handler(client, msg)
    assert msg.replies
    assert any("Stopped" in r for r in msg.replies)
    assert fake_player.stopped is True
