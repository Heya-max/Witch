import asyncio

import pytest
from app.player.manager import PlaybackState, PlayerManager
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
        self.last_engine = None
        self.paused = []
        self.resumed = []
        self.volume_calls = []

    async def play(self, chat_id, input_source, volume=1.0):
        engine = FakeEngine()
        self.last_engine = engine
        return {"mode": "engine", "engine": engine}

    async def set_volume(self, chat_id, input_source, volume):
        engine = FakeEngine()
        self.last_engine = engine
        self.volume_calls.append((chat_id, volume))
        return {"mode": "engine", "engine": engine}

    async def pause_playback(self, chat_id):
        self.paused.append(chat_id)

    async def resume_playback(self, chat_id):
        self.resumed.append(chat_id)

    async def stop_playback(self, chat_id):
        pass

    def register_on_stream_end(self, chat_id, callback):
        pass


@pytest.mark.asyncio
async def test_player_pause_and_resume():
    voice = FakeVoice()
    player = await PlayerManager(voice).get_player(1)
    await player.enqueue(Track(id="t1", title="One", source_url="http://x/1"))
    await asyncio.sleep(0.05)

    assert player.state == PlaybackState.PLAYING
    await player.pause()
    assert player.state == PlaybackState.PAUSED
    assert voice.paused == [1]

    await player.resume()
    assert player.state == PlaybackState.PLAYING
    assert voice.resumed == [1]
    await player.stop()


@pytest.mark.asyncio
async def test_player_pause_when_idle_raises():
    voice = FakeVoice()
    player = await PlayerManager(voice).get_player(1)
    with pytest.raises(ValueError):
        await player.pause()
    with pytest.raises(ValueError):
        await player.resume()


@pytest.mark.asyncio
async def test_player_set_volume_applies_to_current_track():
    voice = FakeVoice()
    player = await PlayerManager(voice).get_player(1)
    await player.enqueue(Track(id="t1", title="One", source_url="http://x/1"))
    await asyncio.sleep(0.05)
    assert player.state == PlaybackState.PLAYING

    await player.set_volume(0.5)
    assert player.volume == 0.5
    assert voice.volume_calls == [(1, 0.5)]
    assert player.current is not None

    await player.set_volume(2.5)  # clamp
    assert player.volume == 2.0
    await player.stop()


@pytest.mark.asyncio
async def test_player_enqueue_next_jumps_queue():
    voice = FakeVoice()
    player = await PlayerManager(voice).get_player(1)

    await player.enqueue(Track(id="t1", title="One", source_url="http://x/1"))
    # let t1 reach PLAYING before adding more tracks to avoid startup races
    await asyncio.sleep(0.05)
    assert player.state == PlaybackState.PLAYING

    await player.enqueue(Track(id="t3", title="Three", source_url="http://x/3"))
    pos = await player.enqueue_next(Track(id="t2", title="Two", source_url="http://x/2"))
    assert pos == 0

    # after t1 finishes, t2 must play next, then t3
    assert player.current.id == "t1"
    voice.last_engine.finish()
    await asyncio.sleep(0.05)
    assert player.current.id == "t2"
    voice.last_engine.finish()
    await asyncio.sleep(0.05)
    assert player.current.id == "t3"
    await player.stop()
