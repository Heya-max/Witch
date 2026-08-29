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
    def __init__(self):
        self.skipped = False
        self.cleared = False

    async def skip(self):
        self.skipped = True

    async def clear(self):
        self.cleared = True


class FakePlayerManager:
    def __init__(self, player):
        self._player = player

    async def get_player(self, chat_id):
        return self._player


@pytest.mark.asyncio
async def test_skip_and_clear_as_owner():
    from app.bot.handlers.playback import clear_handler, skip_handler

    fake_player = FakePlayer()
    pm = FakePlayerManager(fake_player)
    client = type("C", (), {"player_manager": pm, "settings": type("S", (), {"BOT_OWNER_ID": 99})})

    # owner user
    msg = FakeMessage(chat_id=1, user_id=99)
    await skip_handler(client, msg)
    assert fake_player.skipped is True
    assert any("Skipped" in r or "skipped" in r.lower() or "⏭️" in r for r in msg.replies)

    msg2 = FakeMessage(chat_id=1, user_id=99)
    await clear_handler(client, msg2)
    assert fake_player.cleared is True
    assert any("Cleared" in r or "cleared" in r.lower() or "🧹" in r for r in msg2.replies)


@pytest.mark.asyncio
async def test_skip_denied_for_non_admin():
    from app.bot.handlers.playback import skip_handler

    fake_player = FakePlayer()
    pm = FakePlayerManager(fake_player)
    client = type("C", (), {"player_manager": pm})

    # non-admin user, get_chat_member will raise -> denied
    msg = FakeMessage(chat_id=2, user_id=123)
    await skip_handler(client, msg)
    assert fake_player.skipped is False
    assert any("don't have permission" in r.lower() for r in msg.replies)
