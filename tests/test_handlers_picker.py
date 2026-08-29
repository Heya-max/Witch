import os
import sys

import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class FakeMessage:
    def __init__(self, chat_id, user_id=None, text="/play test"):
        self.chat = type("C", (), {"id": chat_id})
        self.from_user = type("U", (), {"id": user_id}) if user_id else None
        self.text = text
        self.replies = []
        self.reply_markup = None

    async def reply_text(self, text, reply_markup=None):
        self.replies.append(text)
        self.reply_markup = reply_markup
        return text


class FakeProvider:
    def __init__(self, tracks):
        self.tracks = tracks

    async def search(self, query):
        return list(self.tracks)

    async def resolve_audio(self, ref):
        return f"http://audio/{ref}"


class FakePlayer:
    def __init__(self):
        self.enqueued = []

    def queue_like(self, items):
        obj = type("Q", (), {})()
        obj._items = items or []

        async def list_fn():
            return list(obj._items)

        obj.list = list_fn
        self.queue = obj
        return obj

    async def get_queue(self):
        return self.queue

    async def enqueue(self, track):
        self.enqueued.append(track)
        return len(self.enqueued)


class FakePlayerManager:
    def __init__(self, player):
        self._player = player

    async def get_player(self, chat_id):
        return self._player


class FakeQuery:
    def __init__(self, data, chat_id=9, message=None, user_id=None):
        self.data = data
        self.answered = None
        self.alerted = None
        self.message = message or FakeEditedMessage(chat_id)
        self.from_user = type("U", (), {"id": user_id}) if user_id else None

    async def answer(self, text=None, show_alert=False):
        self.answered = text
        self.alerted = show_alert


class FakeEditedMessage:
    def __init__(self, chat_id=9):
        self.chat = type("C", (), {"id": chat_id})
        self.edited = None
        self.markup = None

    async def edit_text(self, text, reply_markup=None):
        self.edited = text
        self.markup = reply_markup
        return text


def _provider_and_results():
    from app.player.models import Track

    tracks = [
        Track(id="1", title="One"),
        Track(id="2", title="Two"),
        Track(id="3", title="Three"),
    ]
    return FakeProvider(tracks), tracks


@pytest.mark.asyncio
async def test_play_handler_shows_picker_on_multiple_results():
    from app.bot.handlers.playback import play_handler

    provider, tracks = _provider_and_results()
    player = FakePlayer()
    pm = FakePlayerManager(player)

    client = type("C", (), {"player_manager": pm, "voice": object(), "pending_picks": {}})
    client.get_default_providers = lambda: [provider]

    import app.bot.handlers.playback as mod

    original = None
    if hasattr(mod, "get_default_providers"):
        original = mod.get_default_providers
    mod.get_default_providers = lambda: [provider]
    try:
        msg = FakeMessage(chat_id=1, user_id=7, text="/play query")
        await play_handler(client, msg)
        assert any("Search results" in r for r in msg.replies)
        assert msg.reply_markup is not None
        assert len(msg.reply_markup.inline_keyboard) == 3
        assert not player.enqueued
    finally:
        if original is not None:
            mod.get_default_providers = original


@pytest.mark.asyncio
async def test_play_handler_auto_enqueues_single_result():
    from app.bot.handlers.playback import play_handler
    from app.player.models import Track

    player = FakePlayer()
    pm = FakePlayerManager(player)
    provider = FakeProvider([Track(id="1", title="Solo")])

    client = type("C", (), {"player_manager": pm, "voice": object()})
    import app.bot.handlers.playback as mod

    original = mod.get_default_providers
    mod.get_default_providers = lambda: [provider]
    try:
        msg = FakeMessage(chat_id=1, user_id=7, text="/play query")
        await play_handler(client, msg)
        assert player.enqueued
        assert player.enqueued[0].title == "Solo"
    finally:
        mod.get_default_providers = original


@pytest.mark.asyncio
async def test_pick_callback_enqueues_selected():
    from app.bot.handlers.playback import inline_callback, play_handler

    provider, tracks = _provider_and_results()
    player = FakePlayer()
    pm = FakePlayerManager(player)

    client = type("C", (), {"player_manager": pm, "voice": object(), "pending_picks": {}})
    import app.bot.handlers.playback as mod

    original = mod.get_default_providers
    mod.get_default_providers = lambda: [provider]
    try:
        msg = FakeMessage(chat_id=1, user_id=7, text="/play query")
        await play_handler(client, msg)
        nonce = list(client.pending_picks)[0]
        message = FakeEditedMessage(chat_id=1)
        query = FakeQuery(f"pick:{nonce}:2", chat_id=1, message=message, user_id=7)
        await inline_callback(client, query)
        assert player.enqueued
        assert player.enqueued[0].title == "Three"
        assert message.edited and "Three" in message.edited
        assert client.pending_picks.get(nonce) is None
    finally:
        mod.get_default_providers = original


@pytest.mark.asyncio
async def test_pick_callback_denies_wrong_user():
    from app.bot.handlers.playback import inline_callback, play_handler

    provider, tracks = _provider_and_results()
    player = FakePlayer()
    pm = FakePlayerManager(player)

    client = type("C", (), {"player_manager": pm, "voice": object(), "pending_picks": {}})
    import app.bot.handlers.playback as mod

    original = mod.get_default_providers
    mod.get_default_providers = lambda: [provider]
    try:
        msg = FakeMessage(chat_id=1, user_id=7, text="/play query")
        await play_handler(client, msg)
        nonce = list(client.pending_picks)[0]
        query = FakeQuery(f"pick:{nonce}:2", user_id=999)  # different user
        await inline_callback(client, query)
        assert not player.enqueued
        assert "wasn't meant for you" in query.answered
        # pending pick is preserved for the real requester
        assert nonce in client.pending_picks
    finally:
        mod.get_default_providers = original


@pytest.mark.asyncio
async def test_pick_callback_expired_nonce():
    from app.bot.handlers.playback import inline_callback

    client = type("C", (), {"player_manager": object(), "pending_picks": {}})
    query = FakeQuery("pick:deadbeef:0")
    await inline_callback(client, query)
    assert query.answered
    assert query.answered == "This selection has expired. Run /play again."

    client2 = type("C", (), {})
    query2 = FakeQuery("pick:xx:zz")
    await inline_callback(client2, query2)
    assert query2.answered == "Invalid selection."


@pytest.mark.asyncio
async def test_queue_handler_page_buttons_for_long_queue():
    from app.bot.handlers.playback import queue_handler
    from app.player.models import Track

    tracks = [Track(id=str(i), title=f"Track {i}") for i in range(1, 26)]
    player = FakePlayer()
    player.queue_like(tracks)
    pm = FakePlayerManager(player)
    client = type("C", (), {"player_manager": pm})
    msg = FakeMessage(chat_id=2, text="/queue")

    await queue_handler(client, msg)
    assert msg.replies
    assert "Track 1" in msg.replies[0]
    assert "Track 10" in msg.replies[0]
    assert "Track 11" not in msg.replies[0]
    assert msg.reply_markup is not None
    row = msg.reply_markup.inline_keyboard[0]
    assert any(b.callback_data == "qpage:1" for b in row)


@pytest.mark.asyncio
async def test_queue_page_callback_second_page():
    from app.bot.handlers.playback import queue_page_callback
    from app.player.models import Track

    tracks = [Track(id=str(i), title=f"Track {i}") for i in range(1, 26)]
    player = FakePlayer()
    player.queue_like(tracks)
    pm = FakePlayerManager(player)
    client = type("C", (), {"player_manager": pm})
    message = FakeEditedMessage(chat_id=2)

    query = FakeQuery("qpage:1", chat_id=2, message=message)
    await queue_page_callback(client, query)
    assert message.edited
    assert "Track 11" in message.edited
    assert "Track 20" in message.edited
    assert message.markup is not None
