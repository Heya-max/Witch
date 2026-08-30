import os
import sys

import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class FakeProvider:
    def __init__(self, tracks):
        self.tracks = tracks

    async def search(self, query):
        return list(self.tracks)


class FakeInlineQuery:
    def __init__(self, query_text="hello"):
        self.query = query_text
        self.answered = None

    async def answer(self, results, cache_time=0, is_personal=True):
        self.answered = (results, cache_time, is_personal)


class FakeUser:
    def __init__(self, id):
        self.id = id


class FakeChosenInlineResult:
    def __init__(self, result_id, user_id):
        self.result_id = result_id
        self.from_user = FakeUser(user_id)


class FakeClient:
    def __init__(self):
        self.pending_inline = {}


def _make_track():
    from app.player.models import Track

    return Track(
        id="vid123",
        title="Alpha Song",
        artist="The Artist",
        duration=125,
        thumbnail="http://example.com/thumb.jpg",
    )


@pytest.mark.asyncio
async def test_inline_query_returns_articles(monkeypatch):
    import app.bot.handlers.inline as inline_mod

    track = _make_track()
    provider = FakeProvider([track])
    monkeypatch.setattr(inline_mod, "get_default_providers", lambda: [provider])

    client = FakeClient()
    query = FakeInlineQuery("alpha")
    await inline_mod.inline_query_handler(client, query)

    results, cache_time, is_personal = query.answered
    assert results
    assert len(results) == 1
    article = results[0]
    assert article.title == "Alpha Song"
    assert article.description == "The Artist • 2:05"
    assert article.thumb_url == "http://example.com/thumb.jpg"
    assert article.id in client.pending_inline
    assert cache_time is not None


@pytest.mark.asyncio
async def test_inline_empty_query_answers_empty(monkeypatch):
    import app.bot.handlers.inline as inline_mod

    client = FakeClient()
    query = FakeInlineQuery("   ")
    await inline_mod.inline_query_handler(client, query)
    results, _, _ = query.answered
    assert results == []


@pytest.mark.asyncio
async def test_inline_creates_pending_store_when_missing(monkeypatch):
    import app.bot.handlers.inline as inline_mod

    track = _make_track()
    monkeypatch.setattr(inline_mod, "get_default_providers", lambda: [FakeProvider([track])])

    client = FakeClient()
    del client.pending_inline  # simulate client without the attribute
    query = FakeInlineQuery("alpha")
    await inline_mod.inline_query_handler(client, query)
    assert client.pending_inline


@pytest.mark.asyncio
async def test_chosen_inline_result_delivers_to_user(monkeypatch):
    import app.bot.handlers.inline as inline_mod

    track = _make_track()
    delivered = []

    async def fake_deliver(client, chat_id, t):
        delivered.append((chat_id, t))

    monkeypatch.setattr(inline_mod, "deliver_audio", fake_deliver)

    client = FakeClient()
    client.pending_inline["tok"] = (FakeProvider([track]), track)
    result = FakeChosenInlineResult("tok", user_id=4242)
    await inline_mod.chosen_inline_result_handler(client, result)

    assert delivered == [(4242, track)]
    assert "tok" not in client.pending_inline


@pytest.mark.asyncio
async def test_chosen_inline_result_unknown_token_noop(monkeypatch):
    import app.bot.handlers.inline as inline_mod

    delivered = []

    async def fake_deliver(client, chat_id, t):
        delivered.append(1)

    monkeypatch.setattr(inline_mod, "deliver_audio", fake_deliver)

    client = FakeClient()
    result = FakeChosenInlineResult("nope", user_id=1)
    await inline_mod.chosen_inline_result_handler(client, result)
    assert not delivered


def test_inline_register_adds_handlers():
    import app.bot.handlers.inline as inline_mod

    added = []

    class FakeApp:
        def add_handler(self, handler):
            added.append(handler)

    inline_mod.register(FakeApp())
    assert len(added) == 2
    assert all(callable(h.callback) for h in added)
