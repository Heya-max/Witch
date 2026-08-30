import app.player.voice as voice_mod
import pytest
from app.player.voice import VoiceManager


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
        self.stream_end_handler = None

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def play(self, chat_id, stream=None):
        self.joined.append(chat_id)
        self.streams[chat_id] = stream

    async def leave_call(self, chat_id):
        self.left.append(chat_id)
        self.streams.pop(chat_id, None)

    async def pause(self, chat_id):
        self.paused.append(chat_id)

    async def resume(self, chat_id):
        self.resumed.append(chat_id)

    def on_update(self, filters=None):
        def decorator(func):
            self.stream_end_handler = func
            return func

        return decorator


class MinimalPyTgCalls:
    """Like FakePyTgCalls but WITHOUT pause/resume."""

    def __init__(self, app):
        self.app = app
        self.stream_end_handler = None
        self.joined = []

    async def play(self, chat_id, stream=None):
        self.joined.append(chat_id)

    async def leave_call(self, chat_id):
        pass

    def on_update(self, filters=None):
        def decorator(func):
            self.stream_end_handler = func
            return func

        return decorator


class FakeMediaStream:
    def __init__(self, media_path, **kwargs):
        self.media_path = media_path
        self.kwargs = kwargs


@pytest.fixture
def voice_with_pytgcalls(monkeypatch):
    monkeypatch.setattr(voice_mod, "PyTgCalls", FakePyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)
    vm = VoiceManager(None)
    assert vm._pytgcalls is not None
    return vm


@pytest.mark.asyncio
async def test_play_default_volume_no_ffmpeg_args(voice_with_pytgcalls):
    await voice_with_pytgcalls.play(123, "http://x/1")
    stream = voice_with_pytgcalls._pytgcalls.streams[123]
    assert "ffmpeg_parameters" not in stream.kwargs


@pytest.mark.asyncio
async def test_play_volume_uses_ffmpeg_parameters(voice_with_pytgcalls):
    await voice_with_pytgcalls.play(123, "http://x/1", volume=0.5)
    stream = voice_with_pytgcalls._pytgcalls.streams[123]
    assert stream.kwargs.get("ffmpeg_parameters") == "-af volume=0.5"


@pytest.mark.asyncio
async def test_set_volume_restarts_stream(voice_with_pytgcalls):
    await voice_with_pytgcalls.play(123, "http://x/1")
    result = await voice_with_pytgcalls.set_volume(123, "http://x/1", 0.5)
    assert result == {"mode": "pytgcalls"}
    assert 123 in voice_with_pytgcalls._pytgcalls.left
    stream = voice_with_pytgcalls._pytgcalls.streams[123]
    assert stream.kwargs.get("ffmpeg_parameters") == "-af volume=0.5"


@pytest.mark.asyncio
async def test_pause_and_resume_pytgcalls(voice_with_pytgcalls):
    await voice_with_pytgcalls.pause_playback(9)
    assert voice_with_pytgcalls._pytgcalls.paused == [9]
    await voice_with_pytgcalls.resume_playback(9)
    assert voice_with_pytgcalls._pytgcalls.resumed == [9]


@pytest.mark.asyncio
async def test_pause_requires_active_playback(monkeypatch):
    monkeypatch.setattr(voice_mod, "PyTgCalls", MinimalPyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)
    vm = VoiceManager(None)
    with pytest.raises(RuntimeError):
        await vm.pause_playback(5)


class FakeAudioEngine:
    def __init__(self):
        self.stopped = 0
        self.paused = 0
        self.resumed = 0

    async def stop(self):
        self.stopped += 1

    async def pause(self):
        self.paused += 1

    async def resume(self):
        self.resumed += 1

    async def play(self, input_source, *, extra_args=None):
        return None


def _engine_vm(monkeypatch):
    # pytgcalls present (with pause/resume methods) but MediaStream fallback
    # triggers engine mode.
    monkeypatch.setattr(voice_mod, "PyTgCalls", FakePyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)
    from app.player import voice as vm_mod

    monkeypatch.setattr(vm_mod, "AudioEngine", FakeAudioEngine)
    return VoiceManager(None)


@pytest.mark.asyncio
async def test_play_stops_preexisting_engine(monkeypatch):
    vm = _engine_vm(monkeypatch)
    # simulate a prior engine-mode playback for this chat
    old = FakeAudioEngine()
    vm._audio[7] = old

    result = await vm.play(7, "http://x/1")
    # previous engine must be stopped to avoid an FFmpeg leak
    assert old.stopped == 1
    assert result["mode"] == "pytgcalls"
    assert 7 not in vm._audio


@pytest.mark.asyncio
async def test_pause_resume_rout_to_engine_not_pytgcalls(monkeypatch):
    vm = _engine_vm(monkeypatch)
    engine = FakeAudioEngine()
    vm._audio[7] = engine

    await vm.pause_playback(7)
    await vm.resume_playback(7)
    assert engine.paused == 1
    assert engine.resumed == 1
    # pytgcalls methods must NOT have been called
    assert vm._pytgcalls.paused == []
    assert vm._pytgcalls.resumed == []


@pytest.mark.asyncio
async def test_leave_stops_engine(monkeypatch):
    vm = _engine_vm(monkeypatch)
    engine = FakeAudioEngine()
    vm._audio[3] = engine
    vm._joined.add(3)

    await vm.leave(3)
    assert engine.stopped == 1
    assert 3 not in vm._audio
    assert 3 not in vm._joined


class FakeApp:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


class NoVCDeniedPyTgCalls(FakePyTgCalls):
    """PyTgCalls that refuses to play because the bot can't create a VC."""

    async def play(self, chat_id, stream=None):
        from pyrogram.errors import BotMethodInvalid

        raise BotMethodInvalid()


def _no_vc_vm(monkeypatch):
    # pytgcalls available, but the bot is denied creating a group call, which
    # is exactly what py-tgcalls attempts when no voice chat is active.
    monkeypatch.setattr(voice_mod, "PyTgCalls", NoVCDeniedPyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)
    return VoiceManager(FakeApp())


@pytest.mark.asyncio
async def test_play_without_active_voice_chat_raises(monkeypatch):
    vm = _no_vc_vm(monkeypatch)
    with pytest.raises(RuntimeError, match="no active voice chat"):
        await vm.play(123, "http://x/1")
    # no silent engine fallback that plays to nowhere
    assert vm._audio == {}
    # the chat was told what to do
    assert vm._app.sent and "No voice chat is active" in vm._app.sent[0][1]


@pytest.mark.asyncio
async def test_join_without_active_voice_chat_raises(monkeypatch):
    vm = _no_vc_vm(monkeypatch)
    with pytest.raises(RuntimeError, match="no active voice chat"):
        await vm.join(123)
    assert 123 not in vm._joined


def test_voice_manager_drives_assistant_client(monkeypatch):
    # When a userbot assistant is configured, PyTgCalls must be bound to that
    # account (users can create group calls; bots cannot).
    monkeypatch.setattr(voice_mod, "PyTgCalls", FakePyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)

    class FakeUserClient:
        pass

    assistant = FakeUserClient()
    vm = VoiceManager(None, assistant=assistant)
    assert vm._voice_app is assistant
    assert vm._pytgcalls is not None
    assert vm._pytgcalls.app is assistant


def test_voice_manager_uses_bot_client_without_assistant(monkeypatch):
    monkeypatch.setattr(voice_mod, "PyTgCalls", FakePyTgCalls)
    monkeypatch.setattr(voice_mod, "MediaStream", FakeMediaStream)
    vm = VoiceManager(None)
    assert vm._voice_app is None
    assert vm._pytgcalls.app is None
