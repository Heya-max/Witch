"""Coverage for the remaining untested AudioEngine paths (subprocess lifecycle)."""

import asyncio
import signal

import pytest
from app.player.audio_engine import AudioEngine, AudioEngineConfig


def _reader_with(lines: bytes):
    reader = asyncio.StreamReader()
    if lines:
        reader.feed_data(lines)
    reader.feed_eof()
    return reader


class GoodProc:
    def __init__(self):
        self.stderr = _reader_with(b"hello from ffmpeg\n")
        self.pid = 999
        self.returncode = None
        self.signals = []
        self._wait = asyncio.get_running_loop().create_future()

    async def wait(self):
        return await self._wait

    def terminate(self):
        self.returncode = 0
        if not self._wait.done():
            self._wait.set_result(0)

    def kill(self):
        self.returncode = -9
        if not self._wait.done():
            self._wait.set_result(-9)

    def send_signal(self, sig):
        self.signals.append(sig)


class HangingProc(GoodProc):
    def terminate(self):
        # process ignores SIGTERM and never exits on its own
        pass


class RaiseReader:
    async def readline(self):
        raise RuntimeError("stream broken")


def _new_engine(ffmpeg_path="ffmpeg", **kw):
    return AudioEngine(AudioEngineConfig(ffmpeg_path=ffmpeg_path, **kw))


def _patch_create(monkeypatch, proc):
    created = {}

    async def fake_create(*args, **kwargs):
        created["args"] = args
        created["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    return created


@pytest.mark.asyncio
async def test_play_rejects_when_already_running(monkeypatch):
    proc = GoodProc()
    _patch_create(monkeypatch, proc)
    engine = _new_engine()
    await engine.play("sample.mp3")
    assert engine._process is proc
    with pytest.raises(RuntimeError, match="already running"):
        await engine.play("sample.mp3")


@pytest.mark.asyncio
async def test_play_resurfaces_process_spawn_error(monkeypatch):
    async def failing_create(*args, **kwargs):
        raise OSError("ffmpeg binary missing")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failing_create)
    engine = _new_engine()
    with pytest.raises(OSError, match="ffmpeg binary missing"):
        await engine.play("sample.mp3")
    assert engine._process is None


@pytest.mark.asyncio
async def test_play_monitor_reports_exit_with_extra_args():
    # no monkeypatch: drive the monitor manually with a fake process
    engine = _new_engine()
    proc = GoodProc()
    engine._process = proc
    engine._exit_future = asyncio.get_running_loop().create_future()
    engine._monitor_task = asyncio.create_task(engine._monitor_process())
    await asyncio.sleep(0)
    proc.returncode = 0
    if not proc._wait.done():
        proc._wait.set_result(0)
    assert await engine.wait_finished() == 0
    assert engine._process is None


@pytest.mark.asyncio
async def test_play_extra_args_are_inserted_before_dash(monkeypatch):
    proc = GoodProc()
    created = _patch_create(monkeypatch, proc)
    engine = _new_engine()
    await engine.play("sample.mp3", extra_args=["-af", "volume=0.5"])
    args = created["args"]
    assert args[-3:] == ("-af", "volume=0.5", "-")


@pytest.mark.asyncio
async def test_wait_finished_without_process(monkeypatch):
    engine = _new_engine()
    assert await engine.wait_finished() is None


@pytest.mark.asyncio
async def test_monitor_noop_without_process():
    engine = _new_engine()
    await engine._monitor_process()


@pytest.mark.asyncio
async def test_monitor_reports_read_error():
    engine = _new_engine()
    proc = GoodProc()
    proc.stderr = RaiseReader()
    engine._process = proc
    engine._exit_future = asyncio.get_running_loop().create_future()
    engine._monitor_task = asyncio.create_task(engine._monitor_process())
    await asyncio.sleep(0)
    if not proc._wait.done():
        proc._wait.set_result(0)
    assert await engine.wait_finished() == 0


@pytest.mark.asyncio
async def test_pause_requires_process():
    engine = _new_engine()
    with pytest.raises(RuntimeError, match="No ffmpeg process to pause"):
        await engine.pause()


@pytest.mark.asyncio
async def test_pause_noop_when_already_paused():
    engine = _new_engine()
    engine._process = GoodProc()
    engine._paused = True
    await engine.pause()


@pytest.mark.asyncio
async def test_pause_requires_posix(monkeypatch):
    monkeypatch.delattr(signal, "SIGSTOP", raising=False)
    engine = _new_engine()
    engine._process = GoodProc()
    with pytest.raises(NotImplementedError, match="POSIX"):
        await engine.pause()


@pytest.mark.asyncio
async def test_pause_sends_sigstop(monkeypatch):
    monkeypatch.setattr(signal, "SIGSTOP", signal.SIGTERM, raising=False)
    engine = _new_engine()
    proc = GoodProc()
    engine._process = proc
    await engine.pause()
    assert proc.signals == [signal.SIGTERM]
    assert engine._paused is True


@pytest.mark.asyncio
async def test_pause_surfaces_signal_error(monkeypatch):
    monkeypatch.setattr(signal, "SIGSTOP", signal.SIGTERM, raising=False)

    class BadSignalProc(GoodProc):
        def send_signal(self, sig):
            raise OSError("signal failed")

    engine = _new_engine()
    engine._process = BadSignalProc()
    with pytest.raises(OSError, match="signal failed"):
        await engine.pause()


@pytest.mark.asyncio
async def test_resume_requires_process():
    engine = _new_engine()
    with pytest.raises(RuntimeError, match="No ffmpeg process to resume"):
        await engine.resume()


@pytest.mark.asyncio
async def test_resume_noop_when_not_paused():
    engine = _new_engine()
    engine._process = GoodProc()
    engine._paused = False
    await engine.resume()


@pytest.mark.asyncio
async def test_resume_requires_posix(monkeypatch):
    monkeypatch.delattr(signal, "SIGCONT", raising=False)
    engine = _new_engine()
    engine._process = GoodProc()
    engine._paused = True
    with pytest.raises(NotImplementedError, match="POSIX"):
        await engine.resume()


@pytest.mark.asyncio
async def test_resume_sends_sigcont(monkeypatch):
    monkeypatch.setattr(signal, "SIGCONT", signal.SIGTERM, raising=False)
    engine = _new_engine()
    proc = GoodProc()
    engine._process = proc
    engine._paused = True
    await engine.resume()
    assert proc.signals == [signal.SIGTERM]
    assert engine._paused is False


@pytest.mark.asyncio
async def test_resume_surfaces_signal_error(monkeypatch):
    monkeypatch.setattr(signal, "SIGCONT", signal.SIGTERM, raising=False)

    class BadSignalProc(GoodProc):
        def send_signal(self, sig):
            raise OSError("signal failed")

    engine = _new_engine()
    engine._process = BadSignalProc()
    engine._paused = True
    with pytest.raises(OSError, match="signal failed"):
        await engine.resume()


@pytest.mark.asyncio
async def test_stop_without_process_is_noop():
    engine = _new_engine()
    await engine.stop()
    await engine.close()


@pytest.mark.asyncio
async def test_stop_handles_already_termined_process():
    class LookupErrorProc(GoodProc):
        def terminate(self):
            raise ProcessLookupError("no such process")

    engine = _new_engine()
    engine._process = LookupErrorProc()
    engine._exit_future = asyncio.get_running_loop().create_future()
    await engine.stop()
    assert engine._process is None


@pytest.mark.asyncio
async def test_stop_kills_hung_process():
    engine = _new_engine(shutdown_timeout=0.05)
    proc = HangingProc()
    engine._process = proc
    engine._exit_future = asyncio.get_running_loop().create_future()
    await engine.stop()
    assert proc.returncode == -9
    assert engine._process is None


@pytest.mark.asyncio
async def test_stop_survives_kill_error():
    class KillErrorProc(HangingProc):
        def kill(self):
            raise OSError("kill failed")

    engine = _new_engine(shutdown_timeout=0.05)
    engine._process = KillErrorProc()
    engine._exit_future = asyncio.get_running_loop().create_future()
    await engine.stop()
