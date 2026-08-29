import os
import sys

import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class FakeMessage:
    def __init__(self, chat_id, user_id=None, text=""):
        self.chat = type("C", (), {"id": chat_id})
        self.from_user = type("U", (), {"id": user_id}) if user_id else None
        self.text = text
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)
        return text


class FakeQueue:
    def __init__(self, items):
        self._items = items
        self.shuffled = False

    async def remove(self, idx):
        if idx < 0 or idx >= len(self._items):
            return None
        return self._items.pop(idx)

    async def move(self, old, new):
        n = len(self._items)
        if not (0 <= old < n and 0 <= new < n):
            return False
        self._items.insert(new, self._items.pop(old))
        return True

    async def shuffle(self):
        self.shuffled = True

    async def add_next(self, track):
        self._items.insert(0, track)
        return 0


class FakePlayer:
    def __init__(self, items):
        self.queue = FakeQueue(items)
        self.paused = False
        self.resumed = False
        self.volume = 1.0
        self.enqueued_next = []

    async def pause(self):
        self.paused = True

    async def resume(self):
        self.resumed = True

    async def set_volume(self, volume):
        self.volume = volume

    async def enqueue_next(self, track):
        self.enqueued_next.append(track)
        return await self.queue.add_next(track)


class FakePlayerManager:
    def __init__(self, player):
        self._player = player

    async def get_player(self, chat_id):
        return self._player


def owner_client(pm):
    return type("C", (), {"player_manager": pm, "settings": type("S", (), {"BOT_OWNER_ID": 99})})


@pytest.mark.asyncio
async def test_rm_removes_track():
    from app.bot.handlers.playback import remove_handler
    from app.player.models import Track

    tracks = [Track(id="a", title="Alpha"), Track(id="b", title="Beta")]
    pg = FakePlayer(tracks)
    pm = FakePlayerManager(pg)
    client = owner_client(pm)

    m = FakeMessage(chat_id=1, user_id=99, text="/rm 1")
    await remove_handler(client, m)
    assert [t.id for t in pg.queue._items] == ["b"]
    assert any("Alpha" in r for r in m.replies)


@pytest.mark.asyncio
async def test_rm_invalid_position():
    from app.bot.handlers.playback import remove_handler
    from app.player.models import Track

    pg = FakePlayer([Track(id="a", title="Alpha")])
    pm = FakePlayerManager(pg)
    client = owner_client(pm)

    m = FakeMessage(chat_id=1, user_id=99, text="/rm 9")
    await remove_handler(client, m)
    assert any("No track" in r for r in m.replies)


@pytest.mark.asyncio
async def test_move_track():
    from app.bot.handlers.playback import move_handler
    from app.player.models import Track

    tracks = [Track(id="a", title="Alpha"), Track(id="b", title="Beta")]
    pg = FakePlayer(tracks)
    pm = FakePlayerManager(pg)
    client = owner_client(pm)

    m = FakeMessage(chat_id=1, user_id=99, text="/move 1 2")
    await move_handler(client, m)
    assert [t.id for t in pg.queue._items] == ["b", "a"]
    assert any("Moved" in r for r in m.replies)


@pytest.mark.asyncio
async def test_move_invalid():
    from app.bot.handlers.playback import move_handler
    from app.player.models import Track

    pg = FakePlayer([Track(id="a", title="Alpha")])
    pm = FakePlayerManager(pg)
    client = owner_client(pm)

    m = FakeMessage(chat_id=1, user_id=99, text="/move 1 5")
    await move_handler(client, m)
    assert any("Invalid" in r for r in m.replies)


@pytest.mark.asyncio
async def test_shuffle():
    from app.bot.handlers.playback import shuffle_handler
    from app.player.models import Track

    pg = FakePlayer([Track(id="a", title="Alpha"), Track(id="b", title="Beta")])
    pm = FakePlayerManager(pg)
    client = owner_client(pm)

    m = FakeMessage(chat_id=1, user_id=99, text="/shuffle")
    await shuffle_handler(client, m)
    assert pg.queue.shuffled
    assert any("shuffled" in r.lower() for r in m.replies)


@pytest.mark.asyncio
async def test_pause_and_resume():
    from app.bot.handlers.playback import pause_handler, resume_handler
    from app.player.models import Track

    pg = FakePlayer([Track(id="a", title="Alpha")])
    pm = FakePlayerManager(pg)
    client = owner_client(pm)

    m = FakeMessage(chat_id=1, user_id=99, text="/pause")
    await pause_handler(client, m)
    assert pg.paused
    assert any("Paused" in r for r in m.replies)

    m2 = FakeMessage(chat_id=1, user_id=99, text="/resume")
    await resume_handler(client, m2)
    assert pg.resumed
    assert any("Resumed" in r for r in m2.replies)


@pytest.mark.asyncio
async def test_pause_when_not_playing():
    from app.bot.handlers.playback import pause_handler
    from app.player.models import Track

    class NoOpPlayer(FakePlayer):
        async def pause(self):
            raise ValueError("No active playback to pause.")

    pg = NoOpPlayer([Track(id="a", title="Alpha")])
    pm = FakePlayerManager(pg)
    client = owner_client(pm)

    m = FakeMessage(chat_id=1, user_id=99, text="/pause")
    await pause_handler(client, m)
    assert any("Nothing is playing" in r for r in m.replies)


@pytest.mark.asyncio
async def test_volume_set_and_range():
    from app.bot.handlers.playback import volume_handler
    from app.player.models import Track

    pg = FakePlayer([Track(id="a", title="Alpha")])
    pm = FakePlayerManager(pg)
    client = owner_client(pm)

    m = FakeMessage(chat_id=1, user_id=99, text="/volume 80")
    await volume_handler(client, m)
    assert pg.volume == 0.8
    assert any("80" in r for r in m.replies)

    m2 = FakeMessage(chat_id=1, user_id=99, text="/volume 250")
    await volume_handler(client, m2)
    assert pg.volume == 0.8
    assert any("between 0 and 200" in r for r in m2.replies)


@pytest.mark.asyncio
async def test_playnext_queues_single_result():
    import app.bot.handlers.playback as mod
    from app.bot.handlers.playback import playnext_handler
    from app.player.models import Track

    pg = FakePlayer([])
    pm = FakePlayerManager(pg)
    client = owner_client(pm)

    provider = type("P", (), {})()
    provider.tracks = [Track(id="1", title="Solo")]
    original = mod.get_default_providers

    async def search(_query):
        return list(provider.tracks)

    async def resolve_audio(ref):
        return f"http://audio/{ref}"

    provider.search = search
    provider.resolve_audio = resolve_audio
    mod.get_default_providers = lambda: [provider]
    try:
        m = FakeMessage(chat_id=1, user_id=99, text="/playnext query")
        await playnext_handler(client, m)
        assert pg.enqueued_next
        assert pg.enqueued_next[0].title == "Solo"
        assert pg.queue._items[0].title == "Solo"
        assert any("play next" in r.lower() for r in m.replies)
    finally:
        mod.get_default_providers = original
