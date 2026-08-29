import asyncio

import pytest
from app.player.audio_engine import AudioEngine, AudioEngineConfig


class DummyProcess:
    def __init__(self):
        self.stderr = asyncio.StreamReader()
        self._wait_future = asyncio.get_event_loop().create_future()
        self.pid = 12345

    async def wait(self):
        return await self._wait_future

    def terminate(self):
        # simulate termination by completing the wait future
        if not self._wait_future.done():
            self._wait_future.set_result(0)

    def kill(self):
        if not self._wait_future.done():
            self._wait_future.set_result(-9)

    def send_signal(self, sig):
        # ignore for dummy
        pass


@pytest.mark.asyncio
async def test_play_stop(monkeypatch):
    created = {}

    async def fake_create(*args, **kwargs):
        # return a dummy process
        proc = DummyProcess()
        created["proc"] = proc
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    engine = AudioEngine(AudioEngineConfig(ffmpeg_path="ffmpeg"))
    await engine.play("sample.mp3")
    assert created.get("proc") is not None

    await engine.stop()
