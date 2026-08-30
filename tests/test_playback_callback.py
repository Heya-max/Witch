import pytest
from app.bot.handlers.playback import _quiet_answer
from pyrogram.errors import QueryIdInvalid


class _StubAnswer:
    def __init__(self):
        self.calls = []

    async def answer(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class _StubExpiredAnswer(_StubAnswer):
    async def answer(self, *args, **kwargs):
        raise QueryIdInvalid("The query ID is invalid")


@pytest.mark.asyncio
async def test_quiet_answer_passes_through():
    q = _StubAnswer()
    await _quiet_answer(q, "hello", show_alert=True)
    assert q.calls == [(("hello",), {"show_alert": True})]


@pytest.mark.asyncio
async def test_quiet_answer_suppresses_expired_query():
    q = _StubExpiredAnswer()
    await _quiet_answer(q, "hello", show_alert=True)
    assert q.calls == []
