import asyncio

import app.player.voice as voice_mod
import pytest
from app.player.manager import PlayerManager
from app.player.models import Track
from app.player.voice import VoiceManager


class FakeAudioPiped:
    def __init__(self, source):
        self.source = source


class FakePyTgCalls:
    def __init__(self, app):
        self.app = app
        self.joins = []
        self.stream_end_handler = None
        self.start_called = False
        self.stop_called = False

    async def start(self):
        self.start_called = True

    async def stop(self):
        self.stop_called = True

    async def join_group_call(self, chat_id, media):
        self.joins.append((chat_id, media))
        return self

    async def play(self):
        return

    async def leave_group_call(self, chat_id):
        return

    def on_stream_end(self, handler):
        self.stream_end_handler = handler


class StreamEndEvent:
    def __init__(self, chat_id):
        self.chat_id = chat_id


@pytest.mark.asyncio
async def test_pytgcalls_playback_advances_on_stream_end(monkeypatch):
    monkeypatch.setattr(voice_mod, "PyTgCalls", FakePyTgCalls)
    monkeypatch.setattr(voice_mod, "AudioPiped", FakeAudioPiped)

    vm = VoiceManager(app=object())
    assert vm._pytgcalls is not None
    await vm.start()

    pm = PlayerManager(vm)
    player = await pm.get_player(12345)

    await player.enqueue(Track(id="t1", title="One", source_url="http://a/1"))
    await player.enqueue(Track(id="t2", title="Two", source_url="http://a/2"))
    await asyncio.sleep(0.05)

    assert player.current is not None
    assert player.current.id == "t1"

    # A single join+play via AudioPiped, and the stream-end handler is wired
    pytgcalls = vm._pytgcalls
    assert len(pytgcalls.joins) == 1
    assert isinstance(pytgcalls.joins[0][1], FakeAudioPiped)
    assert pytgcalls.stream_end_handler is not None

    # Simulate the group call stream ending -> player auto-advances
    await pytgcalls.stream_end_handler(StreamEndEvent(12345))
    await asyncio.sleep(0.1)

    assert player.current is not None
    assert player.current.id == "t2"

    # register must not duplicate the callback across plays
    callbacks = vm._on_stream_end_callbacks[12345]
    assert len(callbacks) == 1

    await player.stop()
    await vm.stop()
