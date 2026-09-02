import asyncio

import pytest
from app.player.manager import PlaybackState, Player, PlayerManager
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
        self.mode = "engine"

    async def join(self, chat_id):
        return

    async def play(self, chat_id: int, input_source: str, volume: float = 1.0):
        self.play_calls.append((chat_id, input_source, volume))
        if self.mode == "engine":
            engine = FakeEngine()
            self.last_engine = engine
            return {"mode": "engine", "engine": engine}
        return {"mode": "pytgcalls"}

    async def stop_playback(self, chat_id):
        return

    def register_on_stream_end(self, chat_id, callback):
        self._callbacks.setdefault(chat_id, []).append(callback)

    async def trigger_end(self, chat_id):
        for cb in list(self._callbacks.get(chat_id, [])):
            await cb(chat_id)


@pytest.mark.asyncio
async def test_auto_advance_on_engine_finish_plays_all_tracks():
    voice = FakeVoice()
    mgr = PlayerManager(voice)
    player = await mgr.get_player(1)

    for i in range(3):
        await player.enqueue(Track(id=f"t{i}", title=f"Track {i}"))
    await asyncio.sleep(0.05)

    assert player.current is not None
    assert player.current.id == "t0"

    for expected in ("t1", "t2"):
        voice.last_engine.finish()
        await asyncio.sleep(0.05)
        assert player.current is not None
        assert player.current.id == expected

    # queue drained after the last track ends
    voice.last_engine.finish()
    await asyncio.sleep(0.05)
    assert player.current is None
    assert player.state is PlaybackState.IDLE
    assert await player.queue.size() == 0

    await player.shutdown()


@pytest.mark.asyncio
async def test_skip_cancels_watchdog_so_next_track_is_not_eaten():
    voice = FakeVoice()
    mgr = PlayerManager(voice)
    player = await mgr.get_player(1)
    player.stall_margin = 0.05

    t1 = Track(id="t1", title="Track 1", duration=1)
    t2 = Track(id="t2", title="Track 2")  # no duration -> no stall watchdog
    await player.enqueue(t1)
    await player.enqueue(t2)
    await asyncio.sleep(0.05)

    assert player.current is not None and player.current.id == "t1"

    # skip before t1's stall watchdog fires
    await player.skip()
    await asyncio.sleep(0.05)
    assert player.current is not None and player.current.id == "t2"

    # wait past the point where t1's watchdog WOULD have fired: t2 must still
    # be playing (a leaked watchdog would have force-advanced and dropped it)
    await asyncio.sleep(1.2)
    assert player.current is not None
    assert player.current.id == "t2"

    await player.shutdown()


@pytest.mark.asyncio
async def test_pytgcalls_stream_end_advances_queue():
    voice = FakeVoice()
    voice.mode = "pytgcalls"
    mgr = PlayerManager(voice)
    player = await mgr.get_player(2)

    await player.enqueue(Track(id="p1", title="P1"))
    await player.enqueue(Track(id="p2", title="P2"))
    await asyncio.sleep(0.05)

    assert player.current is not None and player.current.id == "p1"

    await voice.trigger_end(2)
    await asyncio.sleep(0.05)
    assert player.current is not None and player.current.id == "p2"

    await player.shutdown()


@pytest.mark.asyncio
async def test_retry_cap_drops_wedge_track():
    voice = FakeVoice()

    class AlwaysFailingVoice(FakeVoice):
        async def play(self, chat_id, input_source, volume=1.0):
            raise RuntimeError("stream is dead")

    voice = AlwaysFailingVoice()
    mgr = PlayerManager(voice, max_retries=1)
    player = await mgr.get_player(3)

    await player.enqueue(Track(id="t1", title="Track 1"))
    await player.enqueue(Track(id="t2", title="Track 2"))
    await asyncio.sleep(0.1)

    # first failure requeues t1 (retries=1 <= max=1): not wedged
    assert [t.id for t in await player.queue.list()] == ["t1", "t2"]
    assert player.current is None
    assert player.state is PlaybackState.IDLE

    # a new trigger pops t1 again, fails (retries=2 > max=1) and drops it
    await player.enqueue(Track(id="t3", title="Track 3"))
    await asyncio.sleep(0.1)
    ids = [t.id for t in await player.queue.list()]
    assert "t1" not in ids
    assert ids == ["t2", "t3"]

    await player.shutdown()


@pytest.mark.asyncio
async def test_zero_retries_never_drops():
    class AlwaysFailingVoice(FakeVoice):
        async def play(self, chat_id, input_source, volume=1.0):
            raise RuntimeError("stream is dead")

    mgr = PlayerManager(AlwaysFailingVoice(), max_retries=0)
    player = await mgr.get_player(4)

    await player.enqueue(Track(id="t1", title="Track 1"))
    await player.enqueue(Track(id="t2", title="Track 2"))
    await asyncio.sleep(0.1)
    await player.enqueue(Track(id="t3", title="Track 3"))
    await asyncio.sleep(0.1)

    assert [t.id for t in await player.queue.list()] == ["t1", "t2", "t3"]

    await player.shutdown()


@pytest.mark.asyncio
async def test_no_active_voice_chat_holds_track_at_front_without_retries():
    # "no active voice chat" is a deliberate user-facing guard, NOT a stream
    # fault: the track must stay at the front (never dropped or retried away)
    # so the user can start a voice chat and the next /play picks it up.
    class NoVoiceYet(FakeVoice):
        async def play(self, chat_id, input_source, volume=1.0):
            raise RuntimeError("no active voice chat to stream into")

    mgr = PlayerManager(NoVoiceYet(), max_retries=1)
    player = await mgr.get_player(5)

    await player.enqueue(Track(id="t1", title="Track 1"))
    await asyncio.sleep(0.1)
    assert [t.id for t in await player.queue.list()] == ["t1"]
    assert player.current is None
    assert player.state is PlaybackState.IDLE
    assert player._retries == 0

    # repeated triggers keep the same track at the front - still around but at
    # the head, and it never gets dropped even past max_retries
    for i in range(3):
        await player.enqueue(Track(id=f"t{i + 2}", title=f"Track {i + 2}"))
        await asyncio.sleep(0.1)

    ids = [t.id for t in await player.queue.list()]
    assert ids[0] == "t1"
    assert player._retries == 0

    await player.shutdown()


@pytest.mark.asyncio
async def test_volume_restart_reattaches_watcher():
    voice = FakeVoice()

    class VolVoice(FakeVoice):
        async def set_volume(self, chat_id, source, volume):
            engine = FakeEngine()
            self.last_engine = engine
            return {"mode": "engine", "engine": engine}

    voice = VolVoice()
    mgr = PlayerManager(voice)
    player = await mgr.get_player(5)

    await player.enqueue(Track(id="v1", title="V1", duration=30))
    await player.enqueue(Track(id="v2", title="V2"))
    await asyncio.sleep(0.05)

    assert player.current is not None and player.current.id == "v1"
    first_engine = voice.last_engine

    assert await player.set_volume(1.5) is True
    second_engine = voice.last_engine
    assert second_engine is not first_engine

    # finishing the *new* engine must advance (the watcher was re-attached);
    # finishing the *old* one must not trigger an extra advance
    first_engine.finish()
    await asyncio.sleep(0.05)
    assert player.current is not None and player.current.id == "v1"

    second_engine.finish()
    await asyncio.sleep(0.05)
    assert player.current is not None and player.current.id == "v2"

    await player.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_tasks_and_clears_queue():
    voice = FakeVoice()
    mgr = PlayerManager(voice)
    player = await mgr.get_player(6)

    await player.enqueue(Track(id="s1", title="S1", duration=60))
    await player.enqueue(Track(id="s2", title="S2"))
    await asyncio.sleep(0.05)

    assert player.state is PlaybackState.PLAYING
    assert player.current is not None

    await player.shutdown()

    assert player.state is PlaybackState.IDLE
    assert player.current is None
    assert await player.queue.size() == 0
    assert player._play_task is None or player._play_task.done()

    # a second shutdown must be a no-op (idempotent / no crash)
    await player.shutdown()


@pytest.mark.asyncio
async def test_manager_shutdown_cleans_every_chat():
    voice = FakeVoice()
    mgr = PlayerManager(voice)
    p1 = await mgr.get_player(10)
    p2 = await mgr.get_player(20)

    await p1.enqueue(Track(id="a1", title="A1"))
    await p2.enqueue(Track(id="b1", title="B1"))
    await asyncio.sleep(0.05)

    assert p1.current is not None and p2.current is not None

    await mgr.shutdown()

    assert p1.current is None and p1.state is PlaybackState.IDLE
    assert p2.current is None and p2.state is PlaybackState.IDLE
    assert await p1.queue.size() == 0
    assert await p2.queue.size() == 0


def test_player_exposes_track_validation_for_source_urls():
    player = Player(1, FakeVoice())
    # direct source URLs that don't look like audio are rejected at the door
    assert (
        player._validate_track(Track(id="ok", title="ok", source="url", source_url="https://cdn.example/song.mp3"))
        is None
    )
    with pytest.raises(ValueError):
        player._validate_track(Track(id="bad", title="bad", source="url", source_url="https://techaistudy.com/"))
    # search tracks (source != "url") are not web-gated
    player._validate_track(Track(id="y", title="y", source="yt-dlp", source_url="http://example/1"))
