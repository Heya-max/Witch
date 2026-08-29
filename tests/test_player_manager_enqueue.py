import asyncio
import os
import sys

import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class FakeEngine:
    def __init__(self):
        self._finished = asyncio.Event()

    async def wait_finished(self):
        await self._finished.wait()

    def finish(self):
        self._finished.set()


class FakeVoice:
    def __init__(self):
        self.joined = set()
        self.last_play = None

    async def join(self, chat_id):
        self.joined.add(chat_id)

    async def play(self, chat_id, url, volume: float = 1.0):
        # return engine-backed playback
        engine = FakeEngine()
        self.last_play = (chat_id, url, engine, volume)
        return {"mode": "engine", "engine": engine}

    async def stop_playback(self, chat_id):
        # simulate stop
        return True

    def register_on_stream_end(self, chat_id, callback):
        # not used in this fake
        pass


@pytest.mark.asyncio
async def test_enqueue_and_auto_advance():
    from app.player.manager import PlayerManager
    from app.player.models import Track

    fake_voice = FakeVoice()
    pm = PlayerManager(fake_voice)

    player = await pm.get_player(12345)

    t1 = Track(id="t1", title="One")
    t2 = Track(id="t2", title="Two")

    pos1 = await player.enqueue(t1)
    assert pos1 == 0

    pos2 = await player.enqueue(t2)
    assert pos2 == 1

    # wait briefly for playback to start
    await asyncio.sleep(0.2)

    # player should be playing t1
    assert player.current is not None
    assert player.current.id == "t1"

    # get the engine and finish it to simulate end of track
    engine = fake_voice.last_play[2]
    engine.finish()

    # wait for auto-advance
    await asyncio.sleep(0.2)

    # now current should be t2
    assert player.current is not None
    assert player.current.id == "t2"

    # cleanup background tasks to avoid warnings
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
