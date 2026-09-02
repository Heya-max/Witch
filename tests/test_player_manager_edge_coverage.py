"""Edge-path coverage for PlayerManager/Player: volume failure, registration errors, stalls, shutdown."""

import asyncio

import pytest
from app.player.manager import PlaybackState, Player, PlayerManager
from app.player.models import Track


def _track(tid="t1", duration=None):
    return Track(id=tid, title=f"Track {tid}", duration=duration)


class FakeEngine:
    def __init__(self, fail_wait=None):
        self.fail_wait = fail_wait

    async def wait_finished(self):
        if self.fail_wait:
            raise RuntimeError("engine crashed")
        await asyncio.Event().wait()  # stays pending until cancelled


class FakeVoice:
    def __init__(self, *, play_mode="engine", register_ok=True, stop_ok=True, play_exc=None, set_volume_exc=None):
        self.play_mode = play_mode
        self.register_ok = register_ok
        self.stop_ok = stop_ok
        self.play_exc = play_exc
        self.set_volume_exc = set_volume_exc
        self.last_play = None

    async def play(self, chat_id, url, volume=1.0):
        if self.play_exc:
            raise self.play_exc
        engine = FakeEngine()
        self.last_play = (chat_id, url, volume)
        return {"mode": self.play_mode, "engine": engine}

    async def stop_playback(self, chat_id):
        if not self.stop_ok:
            raise RuntimeError("stop boom")
        return True

    async def set_volume(self, chat_id, source, volume):
        if self.set_volume_exc:
            raise self.set_volume_exc
        return {"mode": self.play_mode, "engine": FakeEngine()}

    def register_on_stream_end(self, chat_id, callback):
        if not self.register_ok:
            raise RuntimeError("callback registration failed")


async def _drain_tasks():
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_enqueue_next_spawns_when_idle():
    voice = FakeVoice()
    player = Player(7, voice)
    await player.enqueue_next(_track("first", duration=None))
    await asyncio.sleep(0.05)
    assert player.current is not None and player.current.id == "first"
    await _drain_tasks()


@pytest.mark.asyncio
async def test_set_volume_when_idle_returns_false():
    player = Player(7, FakeVoice())
    assert await player.set_volume(1.5) is False
    assert player.volume == 1.5
    assert await player.set_volume(9.0) is False  # clamped to 2.0
    assert player.volume == 2.0


@pytest.mark.asyncio
async def test_set_volume_failure_requeues_and_resets():
    voice = FakeVoice(set_volume_exc=RuntimeError("volume boom"))
    player = Player(7, voice)
    player.current = _track("cur", duration=None)
    player.state = PlaybackState.PLAYING

    async def fail_requeue(track):
        raise RuntimeError("requeue boom")

    player.queue.add_next = fail_requeue

    assert await player.set_volume(1.0) is False
    assert player.current is None
    assert player.state == PlaybackState.IDLE


@pytest.mark.asyncio
async def test_spawn_playback_guard_while_running():
    player = Player(7, FakeVoice())
    player._ensure_playback_running = True
    assert player._spawn_playback() is None
    player._ensure_playback_running = False


@pytest.mark.asyncio
async def test_ensure_playback_skips_when_connecting():
    player = Player(7, FakeVoice())
    player.state = PlaybackState.CONNECTING
    await player._ensure_playback()
    assert player.current is None


@pytest.mark.asyncio
async def test_ensure_playback_pytgcalls_registration_failure():
    voice = FakeVoice(play_mode="pytgcalls", register_ok=False)
    player = Player(7, voice)
    await player.queue.add(_track("vid", duration=None))
    await player._ensure_playback()
    assert player.current is not None
    assert player.state == PlaybackState.PLAYING


@pytest.mark.asyncio
async def test_ensure_playback_cancelled_error():
    voice = FakeVoice(play_exc=asyncio.CancelledError())
    player = Player(7, voice)
    await player.queue.add(_track("vid", duration=None))
    player.state = PlaybackState.IDLE
    with pytest.raises(asyncio.CancelledError):
        await player._ensure_playback()
    assert player.current is None
    assert player.state == PlaybackState.IDLE


@pytest.mark.asyncio
async def test_stall_watch_early_return_when_not_playing(monkeypatch):
    async def fake_sleep(secs):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    player = Player(7, FakeVoice())
    player.state = PlaybackState.PAUSED
    task = asyncio.create_task(player._watch_for_stall(10))
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_stall_advance_survives_stop_failure(monkeypatch):
    async def fake_sleep(secs):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    voice = FakeVoice(stop_ok=False)
    player = Player(7, voice)
    player.state = PlaybackState.PLAYING
    player.current = _track("stuck", duration=None)
    player._stall_task = object()
    player._ensure_playback_running = True  # keep the recovery spawn a no-op
    await player._watch_for_stall(10)
    assert player.current is None
    assert player.state == PlaybackState.IDLE


@pytest.mark.asyncio
async def test_handle_track_end_engine_error():
    player = Player(7, FakeVoice())
    player._ensure_playback_running = True
    engine = FakeEngine(fail_wait=True)
    await player._handle_track_end(engine)
    assert player.current is None
    assert player.state == PlaybackState.IDLE


@pytest.mark.asyncio
async def test_on_pytgcalls_end_ignores_other_chat():
    player = Player(7, FakeVoice())
    player.state = PlaybackState.PLAYING
    await player._on_pytgcalls_end(99)
    assert player.state == PlaybackState.PLAYING


@pytest.mark.asyncio
async def test_clear_queue_only():
    player = Player(7, FakeVoice())
    await player.queue.add(_track("a"))
    await player.queue.add(_track("b"))
    await player.clear()
    assert await player.queue.size() == 0


@pytest.mark.asyncio
async def test_shutdown_survives_stop_failure():
    player = Player(7, FakeVoice(stop_ok=False))
    await player.queue.add(_track("a"))
    await player.shutdown()
    assert player.state == PlaybackState.IDLE
    assert player.current is None


@pytest.mark.asyncio
async def test_manager_shutdown_survives_player_failure():
    pm = PlayerManager(FakeVoice())
    player = await pm.get_player(3)

    async def boom():
        raise RuntimeError("player shutdown boom")

    player.shutdown = boom
    await pm.shutdown()
    assert pm._players == {}
