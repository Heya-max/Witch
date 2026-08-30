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
async def test_playback_failure_requeues_track_for_retry():
    class FailingVoice(FakeVoice):
        def __init__(self):
            super().__init__()
            self.fail = True

        async def play(self, chat_id: int, input_source: str, volume: float = 1.0):
            if self.fail:
                raise RuntimeError("no active voice chat to stream into")
            return await super().play(chat_id, input_source, volume)

    voice = FailingVoice()
    mgr = PlayerManager(voice)
    player = await mgr.get_player(1)

    t1 = Track(id="t1", title="Track 1", source_url="http://example/1")
    t2 = Track(id="t2", title="Track 2", source_url="http://example/2")

    await player.enqueue(t1)
    await player.enqueue(t2)
    await asyncio.sleep(0.05)

    # first playback attempt failed -> t1 is requeued at the front, not wedged
    ids = [t.id for t in await player.queue.list()]
    assert ids == ["t1", "t2"]
    assert player.current is None
    assert player.state.name == "IDLE"

    # once the voice chat exists, a new enqueue retries the failed track
    voice.fail = False
    await player.enqueue(Track(id="t3", title="Track 3", source_url="http://example/3"))
    await asyncio.sleep(0.05)
    assert player.current is not None
    assert player.current.id == "t1"

    await player.stop()


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


@pytest.mark.asyncio
async def test_play_resolves_fresh_url_at_play_time():
    voice = FakeVoice()
    resolved = []

    async def resolver(track):
        resolved.append(track.id)
        return "http://fresh.example/stream.m3u8"

    mgr = PlayerManager(voice, resolver=resolver)
    player = await mgr.get_player(1)

    # source_url holds a stale signed URL; resolve_key holds the page URL that
    # can still be re-resolved into a fresh stream.
    t1 = Track(
        id="sc1",
        title="Track 1",
        source_url="http://expired.example/signed.m3u8",
        resolve_key="https://soundcloud.com/someone/track-1",
        duration=30,
    )
    await player.enqueue(t1)
    await asyncio.sleep(0.05)

    assert resolved == ["sc1"]
    assert voice.play_calls and voice.play_calls[-1][1] == "http://fresh.example/stream.m3u8"

    await player.stop()


@pytest.mark.asyncio
async def test_play_falls_back_to_stored_url_when_resolver_fails():
    voice = FakeVoice()

    async def failing_resolver(track):
        raise RuntimeError("boom")

    mgr = PlayerManager(voice, resolver=failing_resolver)
    player = await mgr.get_player(1)

    t1 = Track(id="t1", title="Track 1", source_url="http://example/1", duration=30)
    await player.enqueue(t1)
    await asyncio.sleep(0.05)

    assert voice.play_calls and voice.play_calls[-1][1] == "http://example/1"

    await player.stop()


@pytest.mark.asyncio
async def test_stall_watchdog_advances_queue():
    voice = FakeVoice()
    mgr = PlayerManager(voice)
    player = await mgr.get_player(1)
    player.stall_margin = 0.05

    t1 = Track(id="t1", title="Track 1", source_url="http://example/1", duration=1)
    t2 = Track(id="t2", title="Track 2", source_url="http://example/2")
    await player.enqueue(t1)
    await player.enqueue(t2)
    await asyncio.sleep(0.05)

    assert player.current is not None
    assert player.current.id == "t1"

    # The engine never finishes and no stream-end fires; the watchdog should
    # have forced advancement once duration + margin elapsed.
    await asyncio.sleep(t1.duration + player.stall_margin + 0.2)
    await asyncio.sleep(0.05)

    assert player.current is not None
    assert player.current.id == "t2"

    await player.stop()


@pytest.mark.asyncio
async def test_resolver_ignored_when_absent():
    voice = FakeVoice()
    mgr = PlayerManager(voice)
    player = await mgr.get_player(1)

    t1 = Track(
        id="sc1",
        title="Track 1",
        source_url="http://signed.example/stream",
        resolve_key="https://soundcloud.com/x/y",
    )
    await player.enqueue(t1)
    await asyncio.sleep(0.05)

    # no resolver configured -> stored source_url used as-is
    assert voice.play_calls and voice.play_calls[-1][1] == "http://signed.example/stream"

    await player.stop()
