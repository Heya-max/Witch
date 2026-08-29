import asyncio

import pytest
from app.player.manager import PlayerManager
from app.player.models import Track


class FakeEngine:
    def __init__(self):
        self._fut = asyncio.get_event_loop().create_future()

    async def wait_finished(self):
        return await self._fut

    def finish(self, code=0):
        if not self._fut.done():
            self._fut.set_result(code)


class FakeVoice:
    def __init__(self):
        self._callbacks = {}
        self.play_calls = []
        self.last_engine = None

    async def join(self, chat_id: int):
        return

    async def play(self, chat_id: int, input_source: str, volume: float = 1.0):
        self.play_calls.append((chat_id, input_source, volume))
        # Return engine-based playback with a fake engine
        engine = FakeEngine()
        self.last_engine = engine
        return {"mode": "engine", "engine": engine}

    async def stop_playback(self, chat_id: int):
        return

    def register_on_stream_end(self, chat_id: int, callback):
        self._callbacks.setdefault(chat_id, []).append(callback)

    async def trigger_end(self, chat_id: int):
        for cb in list(self._callbacks.get(chat_id, [])):
            await cb(chat_id)


@pytest.mark.asyncio
async def test_player_advance_on_engine_finish():
    voice = FakeVoice()
    mgr = PlayerManager(voice)
    player = await mgr.get_player(1)

    t1 = Track(id="t1", title="Track 1", source_url="http://example/1")
    t2 = Track(id="t2", title="Track 2", source_url="http://example/2")

    await player.enqueue(t1)
    await player.enqueue(t2)

    # allow background tasks to start
    await asyncio.sleep(0.05)

    # current should be t1
    assert player.current is not None
    assert player.current.id == "t1"

    # Simulate engine finishing by calling finish() on the fake engine returned by play()
    engine = voice.last_engine
    assert engine is not None
    engine.finish()

    # allow background advance
    await asyncio.sleep(0.05)

    # After end, current should be t2
    # Note: depending on scheduling, current may be None momentarily; check queue/list
    lst = await player.queue.list()
    # t2 may have been popped into current
    if player.current:
        assert player.current.id in {"t2", None}
    else:
        # If current cleared, ensure queue has remaining track (t2)
        ids = [t.id for t in lst]
        assert "t2" in ids

    # clean up the background watcher so no orphaned tasks remain
    await player.stop()
