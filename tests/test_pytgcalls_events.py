import asyncio

import pytest
from app.player.voice import VoiceManager


@pytest.mark.asyncio
async def test_voice_manager_stream_end_triggers_callback():
    # Create VoiceManager with a dummy app (None is acceptable for tests because
    # pytgcalls is optional and will not be initialized)
    vm = VoiceManager(app=None)  # type: ignore[arg-type]

    called = asyncio.Event()

    async def on_end(chat_id: int):
        # mark that callback was called
        called.set()

    vm.register_on_stream_end(12345, on_end)

    class Event:
        def __init__(self, chat_id):
            self.chat_id = chat_id

    # call internal handler directly
    await vm._internal_stream_end_handler(None, Event(12345))
    await asyncio.sleep(0)

    assert called.is_set()
