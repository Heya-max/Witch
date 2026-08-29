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
        self.on_stream_end = None

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def join_group_call(self, chat_id, stream):
        self.joined.append(chat_id)
        self.streams[chat_id] = stream

    async def leave_group_call(self, chat_id):
        self.left.append(chat_id)
        self.streams.pop(chat_id, None)

    async def pause_stream(self, chat_id):
        self.paused.append(chat_id)

    async def resume_stream(self, chat_id):
        self.resumed.append(chat_id)


class MinimalPyTgCalls:
    """Like FakePyTgCalls but WITHOUT pause_stream/resume_stream."""

    def __init__(self, app):
        self.app = app
        self.on_stream_end = None
        self.joined = []

    async def join_group_call(self, chat_id, stream):
        self.joined.append(chat_id)

    async def leave_group_call(self, chat_id):
        pass


class FakeAudioPiped:
    def __init__(self, media_path, **kwargs):
        self.media_path = media_path
        self.kwargs = kwargs


@pytest.fixture
def voice_with_pytgcalls(monkeypatch):
    monkeypatch.setattr(voice_mod, "PyTgCalls", FakePyTgCalls)
    monkeypatch.setattr(voice_mod, "AudioPiped", FakeAudioPiped)
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
    monkeypatch.setattr(voice_mod, "AudioPiped", FakeAudioPiped)
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
    # pytgcalls present (with pause/resume methods) but AudioPiped fallback
    # triggers engine mode.
    monkeypatch.setattr(voice_mod, "PyTgCalls", FakePyTgCalls)
    monkeypatch.setattr(voice_mod, "AudioPiped", FakeAudioPiped)
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
