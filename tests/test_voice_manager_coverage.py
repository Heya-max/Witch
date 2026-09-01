"""Coverage for the remaining untested VoiceManager paths in app.player.voice."""

import asyncio
import importlib

import app.player.voice as voice_mod
import pytest
from app.player.voice import VoiceManager
from pyrogram.errors import BotMethodInvalid


class FakePyTgCalls:
    def __init__(self, app):
        self.app = app
        self.started = False
        self.stopped = False
        self.joined = []
        self.left = []
        self.paused = []
        self.resumed = []
        self.streams = {}
        self.participants = []
        self.play_raise = None
        self.leave_raise = None
        self.pause_raise = None
        self.resume_raise = None
        self.participants_raise = None

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def play(self, chat_id, stream=None):
        if self.play_raise is not None:
            raise self.play_raise
        self.joined.append(chat_id)
        self.streams[chat_id] = stream

    async def leave_call(self, chat_id):
        if self.leave_raise is not None:
            raise self.leave_raise
        self.left.append(chat_id)
        self.streams.pop(chat_id, None)

    async def pause(self, chat_id):
        if self.pause_raise is not None:
            raise self.pause_raise
        self.paused.append(chat_id)

    async def resume(self, chat_id):
        if self.resume_raise is not None:
            raise self.resume_raise
        self.resumed.append(chat_id)

    async def get_participants(self, chat_id):
        if self.participants_raise is not None:
            raise self.participants_raise
        return list(self.participants)

    def on_update(self, filters=None):
        return lambda func: func


class NoStopPyTgCalls:
    def __init__(self, app):
        self.app = app

    def on_update(self, filters=None):
        return lambda func: func


class FakeMediaStream:
    def __init__(self, media_path, **kwargs):
        self.media_path = media_path
        self.kwargs = kwargs


class FakeApp:
    def __init__(self, raise_on_send=False):
        self.sent = []
        self.raise_on_send = raise_on_send

    async def send_message(self, chat_id, text):
        if self.raise_on_send:
            raise RuntimeError("telegram down")
        self.sent.append((chat_id, text))


class FakeAudioEngine:
    def __init__(self):
        self.stopped = 0
        self.paused = 0
        self.resumed = 0
        self.played = []
        self.stop_raise = False

    async def stop(self):
        if self.stop_raise:
            raise RuntimeError("engine stop boom")
        self.stopped += 1

    async def pause(self):
        self.paused += 1

    async def resume(self):
        self.resumed += 1

    async def play(self, input_source, *, extra_args=None):
        self.played.append((input_source, extra_args))


@pytest.fixture
def vm(monkeypatch):
    monkeypatch.setattr(voice_mod, "PyTgCalls", FakePyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)
    return VoiceManager(None)


def test_groupcall_aliases_recreated_when_missing(monkeypatch):
    import pyrogram.errors as pe

    monkeypatch.delattr(pe, "GroupcallForbidden")
    monkeypatch.delattr(pe, "GroupcallInvalid")
    importlib.reload(voice_mod)
    assert hasattr(pe, "GroupcallForbidden")
    assert hasattr(pe, "GroupcallInvalid")


def test_stream_end_registration_failure_is_ignored(monkeypatch):
    monkeypatch.setattr(voice_mod, "PyTgCalls", FakePyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)

    def boom():
        raise RuntimeError("incompatible pytgcalls API")

    monkeypatch.setattr(voice_mod.fl, "stream_end", boom)
    manager = VoiceManager(None)
    assert manager._pytgcalls is not None


def test_pytgcalls_init_failure_disables_voice(monkeypatch):
    class BrokenPyTgCalls:
        def __init__(self, app):
            raise RuntimeError("bad client")

    monkeypatch.setattr(voice_mod, "PyTgCalls", BrokenPyTgCalls)
    manager = VoiceManager(None)
    assert manager._pytgcalls is None


@pytest.mark.asyncio
async def test_start_starts_pytgcalls(vm):
    await vm.start()
    assert vm._pytgcalls.started is True


@pytest.mark.asyncio
async def test_start_join_leave_participants_without_pytgcalls(monkeypatch):
    monkeypatch.setattr(voice_mod, "PyTgCalls", None)
    manager = VoiceManager(None)
    await manager.start()
    with pytest.raises(RuntimeError, match="pytgcalls missing"):
        await manager.join(1)
    with pytest.raises(RuntimeError, match="pytgcalls missing"):
        await manager.leave(2)
    assert await manager.get_participants(3) == []


@pytest.mark.asyncio
async def test_stop_survives_pytgcalls_stop_error(monkeypatch):
    class StopFailPyTgCalls(FakePyTgCalls):
        async def stop(self):
            raise RuntimeError("stop failed")

    monkeypatch.setattr(voice_mod, "PyTgCalls", StopFailPyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)
    manager = VoiceManager(None)
    await manager.stop()


@pytest.mark.asyncio
async def test_stop_skips_when_pytgcalls_has_no_stop(monkeypatch):
    monkeypatch.setattr(voice_mod, "PyTgCalls", NoStopPyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)
    manager = VoiceManager(None)
    await manager.stop()


@pytest.mark.asyncio
async def test_stop_cancels_callbacks_and_stops_engines(vm):
    pending = asyncio.ensure_future(asyncio.sleep(60))
    done = asyncio.create_task(asyncio.sleep(0.001))
    await done
    vm._callback_tasks.add(pending)
    vm._callback_tasks.add(done)

    engine = FakeAudioEngine()
    vm._audio[4] = engine

    await vm.stop()
    assert pending.cancelled()
    assert engine.stopped == 1
    assert vm._audio == {}
    assert vm._callback_tasks == set()


@pytest.mark.asyncio
async def test_stop_survives_engine_stop_error(vm):
    engine = FakeAudioEngine()
    engine.stop_raise = True
    vm._audio[4] = engine
    await vm.stop()


@pytest.mark.asyncio
async def test_notify_survives_send_error(monkeypatch):
    monkeypatch.setattr(voice_mod, "PyTgCalls", FakePyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)
    manager = VoiceManager(FakeApp(raise_on_send=True))
    await manager._notify(5, "hello")


@pytest.mark.asyncio
async def test_join_success_and_rejoin_noop(vm):
    await vm.join(123)
    assert 123 in vm._joined
    await vm.join(123)
    assert vm._pytgcalls.joined == [123]


@pytest.mark.asyncio
async def test_join_no_active_group_call(monkeypatch):
    class _NoCall(Exception):
        pass

    monkeypatch.setattr(voice_mod, "NoActiveGroupCall", _NoCall)
    monkeypatch.setattr(voice_mod, "PyTgCalls", FakePyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)
    app = FakeApp()
    manager = VoiceManager(app)
    manager._pytgcalls.play_raise = _NoCall()
    with pytest.raises(RuntimeError, match="no active voice chat"):
        await manager.join(123)
    assert app.sent and "No voice chat is active" in app.sent[0][1]
    assert 123 not in manager._joined


@pytest.mark.asyncio
async def test_join_propagates_unexpected_error(vm):
    vm._pytgcalls.play_raise = RuntimeError("unexpected")
    with pytest.raises(RuntimeError, match="unexpected"):
        await vm.join(123)


@pytest.mark.asyncio
async def test_leave_not_joined_is_noop(vm):
    await vm.leave(7)
    assert vm._pytgcalls.left == []


@pytest.mark.asyncio
async def test_leave_propagates_error_but_cleans_up(vm):
    vm._joined.add(5)
    vm._pytgcalls.leave_raise = RuntimeError("leave boom")
    with pytest.raises(RuntimeError, match="leave boom"):
        await vm.leave(5)
    assert 5 not in vm._joined


@pytest.mark.asyncio
async def test_get_participants_returns_list(vm):
    vm._pytgcalls.participants = ["member-a"]
    assert await vm.get_participants(9) == ["member-a"]


@pytest.mark.asyncio
async def test_get_participants_error_returns_empty(vm):
    vm._pytgcalls.participants_raise = RuntimeError("boom")
    assert await vm.get_participants(9) == []


@pytest.mark.asyncio
async def test_stop_engine_survives_engine_error(vm):
    engine = FakeAudioEngine()
    engine.stop_raise = True
    vm._audio[6] = engine
    await vm._stop_engine(6)
    assert 6 not in vm._audio


@pytest.mark.asyncio
async def test_play_no_active_group_call(monkeypatch):
    class _NoCall(Exception):
        pass

    monkeypatch.setattr(voice_mod, "NoActiveGroupCall", _NoCall)
    monkeypatch.setattr(voice_mod, "PyTgCalls", FakePyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)
    app = FakeApp()
    manager = VoiceManager(app)
    manager._pytgcalls.play_raise = _NoCall()
    with pytest.raises(RuntimeError, match="no active voice chat to stream into"):
        await manager.play(123, "http://x/audio")
    assert app.sent and "No voice chat is active" in app.sent[0][1]


@pytest.mark.asyncio
async def test_play_falls_back_to_engine(monkeypatch):
    monkeypatch.setattr(voice_mod, "PyTgCalls", FakePyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)
    monkeypatch.setattr(voice_mod, "AudioEngine", FakeAudioEngine)
    manager = VoiceManager(None)
    manager._pytgcalls.play_raise = RuntimeError("stream failed")
    result = await manager.play(7, "http://x/1", volume=0.5)
    assert result["mode"] == "engine"
    assert 7 in manager._audio
    assert manager._audio[7].played == [("http://x/1", ["-af", "volume=0.5"])]


@pytest.mark.asyncio
async def test_set_volume_no_active_group_call(monkeypatch):
    class _NoCall(Exception):
        pass

    monkeypatch.setattr(voice_mod, "NoActiveGroupCall", _NoCall)
    monkeypatch.setattr(voice_mod, "PyTgCalls", FakePyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)
    app = FakeApp()
    manager = VoiceManager(app)
    manager._pytgcalls.play_raise = _NoCall()
    with pytest.raises(RuntimeError, match="no active voice chat"):
        await manager.set_volume(3, "http://x", 0.5)
    assert app.sent and "No voice chat is active" in app.sent[0][1]


@pytest.mark.asyncio
async def test_set_volume_bot_method_invalid(monkeypatch):
    monkeypatch.setattr(voice_mod, "PyTgCalls", FakePyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)
    app = FakeApp()
    manager = VoiceManager(app)
    manager._pytgcalls.play_raise = BotMethodInvalid()
    with pytest.raises(RuntimeError, match="no active voice chat"):
        await manager.set_volume(3, "http://x", 0.5)
    assert app.sent and "No voice chat is active" in app.sent[0][1]


@pytest.mark.asyncio
async def test_set_volume_falls_back_to_engine(monkeypatch):
    monkeypatch.setattr(voice_mod, "PyTgCalls", FakePyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)
    monkeypatch.setattr(voice_mod, "AudioEngine", FakeAudioEngine)
    manager = VoiceManager(None)
    manager._pytgcalls.play_raise = RuntimeError("boom")
    result = await manager.set_volume(5, "http://y", 0.25)
    assert result["mode"] == "engine"
    assert manager._audio[5].played == [("http://y", ["-af", "volume=0.25"])]


@pytest.mark.asyncio
async def test_pause_surfaces_pytgcalls_failure(vm):
    vm._pytgcalls.pause_raise = RuntimeError("pause boom")
    with pytest.raises(RuntimeError, match="no active playback to pause"):
        await vm.pause_playback(1)
    assert vm._pytgcalls.paused == []


@pytest.mark.asyncio
async def test_resume_surfaces_pytgcalls_failure(vm):
    vm._pytgcalls.resume_raise = RuntimeError("resume boom")
    with pytest.raises(RuntimeError, match="no active playback to resume"):
        await vm.resume_playback(1)
    assert vm._pytgcalls.resumed == []


@pytest.mark.asyncio
async def test_stream_end_without_chat_id_is_ignored(vm):
    await vm._internal_stream_end_handler(None, type("U", (), {})())


@pytest.mark.asyncio
async def test_register_on_stream_end_dedupes(vm):
    class Cb:
        async def __call__(self, chat_id):
            pass

    cb = Cb()
    vm.register_on_stream_end(3, cb)
    vm.register_on_stream_end(3, cb)
    assert len(vm._on_stream_end_callbacks[3]) == 1


@pytest.mark.asyncio
async def test_stream_end_non_awaitable_callback_ignored(vm):
    def not_a_coroutine(chat_id):
        return "not awaitable"

    vm.register_on_stream_end(5, not_a_coroutine)
    await vm._internal_stream_end_handler(None, type("U", (), {"chat_id": 5})())


@pytest.mark.asyncio
async def test_stop_playback_survives_leave_error(vm):
    vm._pytgcalls.leave_raise = RuntimeError("leave boom")
    await vm.stop_playback(4)
    assert 4 not in vm._audio


@pytest.mark.asyncio
async def test_stop_playback_survives_engine_error(vm):
    engine = FakeAudioEngine()
    engine.stop_raise = True
    vm._audio[8] = engine
    await vm.stop_playback(8)
    assert 8 not in vm._audio
