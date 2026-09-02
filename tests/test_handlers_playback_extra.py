"""Coverage for the untested playback.py paths: join/leave/vc handlers,
privilege checks, rate limits, playlist batch enqueue, direct-play fallback,
admin-command error branches, and picker edge cases."""

import pytest
from app.bot.handlers import playback as pb
from app.player.models import Track
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import QueryIdInvalid


class FakeMessage:
    def __init__(self, chat_id=1, user_id=None, text=""):
        self.chat = type("C", (), {"id": chat_id})
        self.from_user = type("U", (), {"id": user_id}) if user_id else None
        self.text = text
        self.replies = []
        self.reply_markup = None

    async def reply_text(self, text, reply_markup=None):
        self.replies.append(text)
        self.reply_markup = reply_markup
        return text

    async def reply_photo(self, photo):
        self.replies.append(f"photo:{photo}")


class FakeEditedMessage:
    def __init__(self, chat_id=9):
        self.chat = type("C", (), {"id": chat_id})
        self.edited = None
        self.markup = None

    async def edit_text(self, text, reply_markup=None):
        self.edited = text
        self.markup = reply_markup
        return text


class FakeQuery:
    def __init__(self, data, chat_id=9, message=None, user_id=7):
        self.data = data
        self.answered = None
        self.alerted = None
        self.called = False
        self.message = message or FakeEditedMessage(chat_id)
        self.from_user = type("U", (), {"id": user_id}) if user_id else None

    async def answer(self, text=None, show_alert=False):
        self.called = True
        self.answered = text
        self.alerted = show_alert


class FakeQueue:
    def __init__(self, items):
        self._items = items
        self.list_raise = None

    async def list(self):
        if self.list_raise:
            raise self.list_raise
        return list(self._items)

    async def remove(self, idx):
        return None


class FakePlayer:
    def __init__(self, tracks=None):
        self.queue = FakeQueue(tracks or [])
        self.current = None
        self.state = type("S", (), {"name": "IDLE", "value": "IDLE"})()
        self.pause_raise = None
        self.resume_raise = None
        self.volume_returns = None
        self.enqueued_next = []

    async def pause(self):
        if self.pause_raise:
            raise self.pause_raise
        return None

    async def resume(self):
        if self.resume_raise:
            raise self.resume_raise
        return None

    async def set_volume(self, volume):
        if self.volume_returns is False:
            return False
        return True

    async def skip(self):
        self.skipped = True

    async def stop(self):
        self.stopped = True

    async def clear(self):
        self.cleared = True

    async def enqueue(self, track):
        return len(self.queue._items) - 1

    async def enqueue_next(self, track):
        self.enqueued_next.append(track)
        self.queue._items.insert(0, track)
        return 0


class StopOnlyPlayer:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


class FakePlayerManager:
    def __init__(self, player):
        self._player = player

    async def get_player(self, chat_id):
        return self._player


class FakeVoice:
    def __init__(self):
        self.joined = None
        self.left = None
        self.stopped = None
        self.played = None
        self.participants = []
        self.join_raise = None

    async def join(self, chat_id):
        if self.join_raise:
            raise self.join_raise
        self.joined = chat_id

    async def leave(self, chat_id):
        self.left = chat_id

    async def stop_playback(self, chat_id):
        self.stopped = chat_id

    async def play(self, chat_id, input_source):
        self.played = (chat_id, input_source)

    async def get_participants(self, chat_id):
        return list(self.participants)


def _client(player=None, voice=None, **kw):
    attrs = {"player_manager": player, "voice": voice}
    attrs.update(kw)
    return type("C", (), attrs)


def owner_client(player=None, voice=None, owner_id=99, **kw):
    return _client(player, voice, settings=type("S", (), {"BOT_OWNER_ID": owner_id}), **kw)


class LocksOk:
    async def acquire(self, *a, **k):
        return "tok"

    async def release(self, *a, **k):
        return True


class LocksDenied:
    async def acquire(self, *a, **k):
        return None


class BrokenManager:
    async def get_player(self, chat_id):
        raise RuntimeError("boom")


class _Awaited:
    def __init__(self, value):
        self._value = value

    def __await__(self):
        yield
        return self._value


def _fake_playlist(*tracks):
    async def _inner(src):
        return list(tracks)

    return _inner


@pytest.mark.asyncio
async def test_is_privileged_paths(monkeypatch):
    assert await pb._is_privileged(_client(), FakeMessage(user_id=None)) is False

    # owner id from settings
    client = _client(settings=type("S", (), {"BOT_OWNER_ID": "42"}))
    assert await pb._is_privileged(client, FakeMessage(user_id=42)) is True

    # owner id from env var fallback
    monkeypatch.setenv("BOT_OWNER_ID", "99")
    env_client = _client(settings=None)
    assert await pb._is_privileged(env_client, FakeMessage(user_id=99)) is True

    # admin member
    class Member:
        status = ChatMemberStatus.ADMINISTRATOR

    admin_client = _client(settings=None)
    admin_client.get_chat_member = lambda chat_id, user_id: _Awaited(Member())
    assert await pb._is_privileged(admin_client, FakeMessage(user_id=5)) is True

    # plain member -> not privileged
    class Sender:
        status = ChatMemberStatus.MEMBER

    sender_client = _client(settings=None)
    sender_client.get_chat_member = lambda chat_id, user_id: _Awaited(Sender())
    assert await pb._is_privileged(sender_client, FakeMessage(user_id=5)) is False

    # member lookup error -> not privileged
    async def failing(chat_id, user_id):
        raise RuntimeError("telegram down")

    err_client = _client(settings=None)
    err_client.get_chat_member = failing
    assert await pb._is_privileged(err_client, FakeMessage(user_id=5)) is False


@pytest.mark.asyncio
async def test_join_handler_paths():
    msg = FakeMessage()
    await pb.join_handler(_client(voice=None), msg)
    assert any("Voice support not configured" in r for r in msg.replies)

    msg2 = FakeMessage()
    await pb.join_handler(_client(voice=FakeVoice(), locks=LocksDenied()), msg2)
    assert any("Another join/leave is in progress" in r for r in msg2.replies)

    voice = FakeVoice()
    msg3 = FakeMessage(chat_id=42)
    await pb.join_handler(_client(voice=voice, locks=LocksOk()), msg3)
    assert voice.joined == 42
    assert any("Joined the voice chat" in r for r in msg3.replies)

    voice2 = FakeVoice()
    voice2.join_raise = RuntimeError("bad join")
    msg4 = FakeMessage()
    await pb.join_handler(_client(voice=voice2, locks=LocksOk()), msg4)
    assert any("Failed to join the voice chat" in r for r in msg4.replies)


@pytest.mark.asyncio
async def test_leave_handler_paths():
    msg = FakeMessage()
    await pb.leave_handler(_client(voice=None), msg)
    assert any("Voice support not configured" in r for r in msg.replies)

    msg2 = FakeMessage()
    await pb.leave_handler(_client(voice=FakeVoice(), locks=LocksDenied()), msg2)
    assert any("Another join/leave is in progress" in r for r in msg2.replies)

    voice = FakeVoice()
    msg3 = FakeMessage(chat_id=7)
    await pb.leave_handler(_client(voice=voice, locks=LocksOk()), msg3)
    assert voice.left == 7
    assert any("Left the voice chat" in r for r in msg3.replies)


@pytest.mark.asyncio
async def test_vc_status_handler_paths():
    msg = FakeMessage()
    await pb.vc_status_handler(_client(voice=None), msg)
    assert any("Voice support not configured" in r for r in msg.replies)

    class Participant:
        user_id = 1
        muted = False
        source = "source/a"

    voice = FakeVoice()
    voice.participants = [Participant()]
    msg2 = FakeMessage()
    await pb.vc_status_handler(_client(voice=voice), msg2)
    assert any("id=1" in r for r in msg2.replies)

    voice.participants = []
    msg3 = FakeMessage()
    await pb.vc_status_handler(_client(voice=voice), msg3)
    assert any("participants (0)" in r for r in msg3.replies)

    class ErrVoice:
        async def get_participants(self, chat_id):
            raise RuntimeError("boom")

    msg4 = FakeMessage()
    await pb.vc_status_handler(_client(voice=ErrVoice()), msg4)
    assert any("Failed to read voice-chat participants" in r for r in msg4.replies)


def test_register_adds_all_handlers():
    added = []

    class FakeApp:
        def add_handler(self, handler):
            added.append(handler)

    pb.register(FakeApp())
    assert len(added) == 17
    assert all(getattr(h, "callback", None) or getattr(h, "handler", None) for h in added)


@pytest.mark.asyncio
async def test_play_handler_basic_guards():
    msg = FakeMessage(text="/play")
    await pb.play_handler(_client(voice=FakeVoice()), msg)
    assert any("Usage: /play" in r for r in msg.replies)

    msg2 = FakeMessage(text="/play query")
    await pb.play_handler(_client(voice=None), msg2)
    assert any("Voice support not configured" in r for r in msg2.replies)


@pytest.mark.asyncio
async def test_play_handler_rate_limited():
    class UserLimited:
        async def allow(self, key, limit, period):
            return "user:7" not in key  # deny the user key

    class ChatLimited:
        async def allow(self, key, limit, period):
            return "chat:1" not in key  # deny the chat key

    client = _client(voice=FakeVoice(), rate_limiter=UserLimited())
    msg = FakeMessage(chat_id=1, user_id=7, text="/play query")
    await pb.play_handler(client, msg)
    assert any("too quickly" in r.lower() for r in msg.replies)

    client2 = _client(voice=FakeVoice(), rate_limiter=ChatLimited())
    msg2 = FakeMessage(chat_id=1, user_id=7, text="/play query")
    await pb.play_handler(client2, msg2)
    assert any("rate-limited" in r.lower() for r in msg2.replies)


@pytest.mark.asyncio
async def test_play_handler_playlist_batch(monkeypatch):
    from app.player.queue import QueueFullError

    player = FakePlayer()
    client = _client(voice=FakeVoice(), player_manager=FakePlayerManager(player))

    monkeypatch.setattr(pb, "_maybe_playlist", _fake_playlist(Track(id="a", title="A"), Track(id="b", title="B")))
    msg = FakeMessage(chat_id=1, user_id=7, text="/play https://youtube.com/playlist?list=x")
    await pb.play_handler(client, msg)
    assert any("Enqueued 2 of 2" in r for r in msg.replies)

    monkeypatch.setattr(pb, "_maybe_playlist", _fake_playlist(Track(id="a", title="Solo")))
    msg2 = FakeMessage(chat_id=1, user_id=7, text="/play https://youtu.be/abc")
    await pb.play_handler(client, msg2)
    assert any("Enqueued: Solo" in r for r in msg2.replies)

    class FullEnqueue:
        async def enqueue(self, track):
            raise QueueFullError(1, 100)

    monkeypatch.setattr(pb, "_maybe_playlist", _fake_playlist(Track(id="a", title="A")))
    player.enqueue = FullEnqueue().enqueue
    msg3 = FakeMessage(chat_id=1, user_id=7, text="/play https://youtu.be/abc")
    await pb.play_handler(client, msg3)
    assert any("queue is full" in r for r in msg3.replies)


@pytest.mark.asyncio
async def test_play_handler_playlist_needs_pm(monkeypatch):
    monkeypatch.setattr(pb, "_maybe_playlist", _fake_playlist(Track(id="a", title="A")))
    msg = FakeMessage(text="/play https://youtu.be/abc")
    await pb.play_handler(_client(voice=FakeVoice(), player_manager=None), msg)
    assert any("Queue support is required" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_play_handler_value_error_refusal(monkeypatch):
    def refuse(client, message, input_source):
        raise ValueError("That doesn't look like an audio source")

    monkeypatch.setattr(pb, "_resolve_track", refuse)
    msg = FakeMessage(text="/play https://junk.example/page")
    await pb.play_handler(_client(voice=FakeVoice()), msg)
    assert any("audio source" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_play_handler_direct_voice_fallback(monkeypatch):
    monkeypatch.setattr(pb, "_maybe_playlist", _fake_playlist())
    monkeypatch.setattr(pb, "get_default_providers", lambda: [])
    voice = FakeVoice()
    client = _client(voice=voice, player_manager=None)
    msg = FakeMessage(chat_id=1, text="/play https://example.com/audio.mp3")
    await pb.play_handler(client, msg)
    assert any("Playing:" in r for r in msg.replies)
    assert voice.played is not None


@pytest.mark.asyncio
async def test_play_handler_enqueue_error_paths(monkeypatch):
    from app.player.queue import QueueFullError

    monkeypatch.setattr(pb, "_maybe_playlist", _fake_playlist())
    monkeypatch.setattr(pb, "get_default_providers", lambda: [])

    class FullPlayer(FakePlayer):
        async def enqueue(self, track):
            raise QueueFullError(1, 100)

    msg = FakeMessage(chat_id=1, text="/play some plain query")
    await pb.play_handler(_client(voice=FakeVoice(), player_manager=FakePlayerManager(FullPlayer())), msg)
    assert any("queue is full" in r for r in msg.replies)

    boom_player = FakePlayer()

    async def boom_enqueue(track):
        raise RuntimeError("boom")

    boom_player.enqueue = boom_enqueue
    msg2 = FakeMessage(chat_id=1, text="/play some plain query")
    await pb.play_handler(_client(voice=FakeVoice(), player_manager=FakePlayerManager(boom_player)), msg2)
    assert any("Failed to start playback" in r for r in msg2.replies)


@pytest.mark.asyncio
async def test_resolve_track_fallbacks_and_provider_skip(monkeypatch):
    class FailingProvider:
        async def search(self, query):
            raise RuntimeError("no")

    monkeypatch.setattr(pb, "get_default_providers", lambda: [FailingProvider()])
    client = _client()
    msg = FakeMessage(user_id=7, text="/play my song")
    track, url, prov, results = await pb._resolve_track(client, msg, "my song")
    assert prov is None
    assert track.source_url == "my song"

    # non-audio http input is refused
    with pytest.raises(ValueError):
        await pb._resolve_track(client, msg, "https://techaistudy.com/article")


@pytest.mark.asyncio
async def test_resolve_track_chains_broken_then_working(monkeypatch):
    class BadProvider:
        async def search(self, query):
            raise RuntimeError("search down")

    class GoodProvider:
        async def search(self, query):
            return [Track(id="ok", title="Good", source="yt-dlp", source_url="http://sc/x")]

        async def resolve_audio(self, ref):
            return "http://audio/m3u8"

    monkeypatch.setattr(pb, "get_default_providers", lambda: [BadProvider(), GoodProvider()])
    client = _client()
    msg = FakeMessage(user_id=7, text="/play x")
    track, url, prov, results = await pb._resolve_track(client, msg, "x")
    assert track.id == "ok"
    assert url == "http://audio/m3u8"
    assert track.resolve_key == "http://sc/x"


@pytest.mark.asyncio
async def test_playnext_usage_and_no_pm():
    msg = FakeMessage(text="/playnext")
    await pb.playnext_handler(_client(player_manager=FakePlayerManager(FakePlayer())), msg)
    assert any("Usage: /playnext" in r for r in msg.replies)

    msg2 = FakeMessage(text="/playnext query")
    await pb.playnext_handler(_client(), msg2)
    assert any("Queue support" in r for r in msg2.replies)


@pytest.mark.asyncio
async def test_playnext_playlist_and_picker_next(monkeypatch):
    player = FakePlayer()
    client = _client(player_manager=FakePlayerManager(player), voice=FakeVoice())

    monkeypatch.setattr(pb, "_maybe_playlist", _fake_playlist(Track(id="a", title="First")))
    msg = FakeMessage(chat_id=1, text="/playnext https://youtu.be/abc")
    await pb.playnext_handler(client, msg)
    assert player.enqueued_next
    assert any("play next" in r.lower() for r in msg.replies)

    provider = type("P", (), {})()

    async def good_resolve(ref):
        return "http://audio/m3u8"

    provider.tracks = [Track(id="1", title="One"), Track(id="2", title="Two")]
    provider.resolve_audio = good_resolve
    client2 = _client(player_manager=FakePlayerManager(FakePlayer()), voice=FakeVoice())
    client2.pending_picks = {}
    picker_msg = FakeMessage(chat_id=1, user_id=7)
    await pb._offer_picker(client2, picker_msg, 1, provider, provider.tracks, next_play=True)
    entry = list(client2.pending_picks.values())[0]
    assert entry["action"] == "enqueue_next"


@pytest.mark.asyncio
async def test_playnext_generic_exception(monkeypatch):
    monkeypatch.setattr(pb, "get_default_providers", lambda: [])
    client = _client(player_manager=BrokenManager(), voice=FakeVoice())
    msg = FakeMessage(text="/playnext something")
    await pb.playnext_handler(client, msg)
    assert any("Failed to queue track" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_cb_data_and_inline_callback_dispatch(monkeypatch):
    assert pb._cb_data(type("Q", (), {"data": b"pick:1:0"})()) == "pick:1:0"

    client = _client()
    q = FakeQuery("qpage:none")
    await pb.inline_callback(client, q)
    assert q.called is True

    # fav handled by the favorites module's own callback
    q2 = FakeQuery("fav:p:0")
    await pb.inline_callback(client, q2)
    assert q2.called is False

    q3 = FakeQuery("junk")
    await pb.inline_callback(client, q3)
    assert q3.called is True

    # an unexpected exception becomes a quiet "Something went wrong."
    quiet_calls = []
    original_quiet = pb._quiet_answer

    async def record_quiet(query, text, **kw):
        quiet_calls.append(text)

    monkeypatch.setattr(pb, "_quiet_answer", record_quiet)
    try:

        class BoomQuery:
            data = "boom"
            message = None
            from_user = None

            async def answer(self, *a, **k):
                raise QueryIdInvalid("expired")

        await pb.inline_callback(client, BoomQuery())
        assert quiet_calls == ["Something went wrong."]
    finally:
        monkeypatch.setattr(pb, "_quiet_answer", original_quiet)


@pytest.mark.asyncio
async def test_pick_callback_out_of_range_and_resolve_failure():
    provider = type("P", (), {})()
    provider.tracks = [Track(id="1", title="One"), Track(id="2", title="Two")]
    client = _client(player_manager=object(), voice=FakeVoice())
    client.pending_picks = {}
    msg = FakeMessage(chat_id=1, user_id=7)
    await pb._offer_picker(client, msg, 1, provider, provider.tracks)
    nonce = list(client.pending_picks)[0]

    q = FakeQuery(f"pick:{nonce}:9", user_id=7)
    await pb.pick_callback(client, q)
    assert "Invalid selection" in q.answered

    async def bad_resolve(ref):
        raise RuntimeError("nope")

    provider.resolve_audio = bad_resolve
    q2 = FakeQuery(f"pick:{nonce}:0", user_id=7)
    await pb.pick_callback(client, q2)
    assert "Could not resolve audio" in q2.answered


@pytest.mark.asyncio
async def test_pick_callback_no_pm_and_enqueue_next(monkeypatch):
    provider = type("P", (), {})()
    provider.tracks = [
        Track(id="1", title="One", source="yt-dlp", source_url="http://sc/x"),
        Track(id="2", title="Two", source="yt-dlp", source_url="http://sc/y"),
    ]

    async def good_resolve(ref):
        return "http://audio/m3u8"

    provider.resolve_audio = good_resolve
    player = FakePlayer()

    # create a pick through a client with a player manager, then run the
    # callback after the manager is removed (pick registry stays on client)
    holder = _client(player_manager=FakePlayerManager(player), voice=FakeVoice())
    holder.pending_picks = {}
    msg = FakeMessage(chat_id=1, user_id=7)
    await pb._offer_picker(holder, msg, 1, provider, provider.tracks)
    nonce = list(holder.pending_picks)[0]

    holder.player_manager = None
    q = FakeQuery(f"pick:{nonce}:0", user_id=7)
    await pb.pick_callback(holder, q)
    assert "not configured" in q.answered

    # enqueue_next action plays through
    client2 = _client(player_manager=FakePlayerManager(player), voice=FakeVoice())
    client2.pending_picks = {}
    msg2 = FakeMessage(chat_id=1, user_id=7)
    await pb._offer_picker(client2, msg2, 1, provider, provider.tracks, next_play=True)
    nonce2 = list(client2.pending_picks)[0]
    q2 = FakeQuery(f"pick:{nonce2}:0", user_id=7)
    await pb.pick_callback(client2, q2)
    assert player.enqueued_next
    assert "Queued to play next" in (q2.message.edited or "")


@pytest.mark.asyncio
async def test_pick_callback_queue_full_alert():
    from app.player.queue import QueueFullError

    provider = type("P", (), {})()
    provider.tracks = [
        Track(id="1", title="One", source="yt-dlp", source_url="http://sc/x"),
        Track(id="2", title="Two", source="yt-dlp", source_url="http://sc/y"),
    ]

    async def good_resolve(ref):
        return "http://audio/m3u8"

    provider.resolve_audio = good_resolve

    class FullPlayer(FakePlayer):
        async def enqueue(self, track):
            raise QueueFullError(1, 100)

    client = _client(player_manager=FakePlayerManager(FullPlayer()), voice=FakeVoice())
    client.pending_picks = {}
    msg = FakeMessage(chat_id=1, user_id=7)
    await pb._offer_picker(client, msg, 1, provider, provider.tracks)
    nonce = list(client.pending_picks)[0]
    q = FakeQuery(f"pick:{nonce}:0", user_id=7)
    await pb.pick_callback(client, q)
    assert "is full" in q.answered


@pytest.mark.asyncio
async def test_download_picked_handles_failure(monkeypatch):
    async def bad_deliver(client, chat_id, track):
        raise RuntimeError("dl failed")

    monkeypatch.setattr("app.bot.handlers.media.deliver_audio", bad_deliver)
    track = Track(id="1", title="One")
    msg = FakeEditedMessage(chat_id=1)
    q = FakeQuery("pick:x:0", message=msg)
    await pb._download_picked(_client(), q, 1, track)
    assert "Download failed" in msg.edited


@pytest.mark.asyncio
async def test_queue_page_callback_edge_cases():
    q = FakeQuery("qpage:none")
    await pb.queue_page_callback(_client(), q)
    assert q.called is True

    q2 = FakeQuery("qpage:junk")
    await pb.queue_page_callback(_client(), q2)
    assert "Invalid page" in q2.answered

    msg = FakeEditedMessage(chat_id=1)
    q3 = FakeQuery("qpage:1", message=msg)
    await pb.queue_page_callback(_client(player_manager=None), q3)
    assert "not configured" in q3.answered

    broken = FakePlayer()
    broken.queue.list_raise = RuntimeError("db down")
    q4 = FakeQuery("qpage:1")
    await pb.queue_page_callback(_client(player_manager=FakePlayerManager(broken)), q4)
    assert "Could not fetch queue" in q4.answered

    # single page (no markup) edit
    msg5 = FakeEditedMessage(chat_id=1)
    q5 = FakeQuery("qpage:0", message=msg5)
    await pb.queue_page_callback(_client(player_manager=FakePlayerManager(FakePlayer())), q5)
    assert msg5.edited is not None


@pytest.mark.asyncio
async def test_queue_handler_not_configured_and_error():
    msg = FakeMessage(text="/queue")
    await pb.queue_handler(_client(), msg)
    assert any("Queue support" in r for r in msg.replies)

    broken = FakePlayer()
    broken.queue.list_raise = RuntimeError("db down")
    msg2 = FakeMessage(text="/queue")
    await pb.queue_handler(_client(player_manager=FakePlayerManager(broken)), msg2)
    assert any("Could not fetch queue" in r for r in msg2.replies)


@pytest.mark.asyncio
async def test_now_playing_handler_paths():
    msg = FakeMessage(text="/nowplaying")
    await pb.now_playing_handler(_client(), msg)
    assert any("not available" in r for r in msg.replies)

    player = FakePlayer()
    msg2 = FakeMessage(text="/nowplaying")
    await pb.now_playing_handler(_client(player_manager=FakePlayerManager(player)), msg2)
    assert any("Nothing is playing" in r for r in msg2.replies)

    player.current = Track(
        id="1",
        title="Song",
        artist="Artist",
        album="Album",
        duration=125,
        thumbnail="http://example.com/thumb.jpg",
        requested_by=7,
    )
    msg3 = FakeMessage(text="/nowplaying")
    await pb.now_playing_handler(_client(player_manager=FakePlayerManager(player)), msg3)
    assert any("Now playing" in r for r in msg3.replies)

    class NoPhotoMessage(FakeMessage):
        async def reply_photo(self, photo):
            raise RuntimeError("bad photo url")

    msg4 = NoPhotoMessage(text="/nowplaying")
    await pb.now_playing_handler(_client(player_manager=FakePlayerManager(player)), msg4)
    assert any("Now playing" in r for r in msg4.replies)

    msg5 = FakeMessage(text="/nowplaying")
    await pb.now_playing_handler(_client(player_manager=BrokenManager()), msg5)
    assert any("Could not fetch now-playing" in r for r in msg5.replies)


@pytest.mark.asyncio
async def test_admin_guard_denied():
    msg = FakeMessage(user_id=5)
    assert await pb._admin_guard(owner_client(player_manager=object()), msg) is False
    assert any("don't have permission" in r.lower() for r in msg.replies)


@pytest.mark.asyncio
async def test_remove_move_shuffle_guards_and_errors():
    msg = FakeMessage(user_id=99, text="/rm")
    await pb.remove_handler(owner_client(), msg)
    assert any("Usage: /rm" in r for r in msg.replies)

    msg2 = FakeMessage(user_id=99, text="/rm 1")
    await pb.remove_handler(owner_client(player_manager=None), msg2)
    assert any("Queue support" in r for r in msg2.replies)

    msg3 = FakeMessage(user_id=99, text="/move 1")
    await pb.move_handler(owner_client(), msg3)
    assert any("Usage: /move" in r for r in msg3.replies)

    msg4 = FakeMessage(user_id=99, text="/move 1 2")
    await pb.move_handler(owner_client(player_manager=None), msg4)
    assert any("Queue support" in r for r in msg4.replies)

    msg5 = FakeMessage(user_id=99, text="/shuffle")
    await pb.shuffle_handler(owner_client(player_manager=None), msg5)
    assert any("Queue support" in r for r in msg5.replies)

    broken = owner_client(player_manager=BrokenManager())
    rm = FakeMessage(user_id=99, text="/rm 1")
    await pb.remove_handler(broken, rm)
    assert any("Failed to remove" in r for r in rm.replies)

    mv = FakeMessage(user_id=99, text="/move 1 2")
    await pb.move_handler(broken, mv)
    assert any("Failed to move" in r for r in mv.replies)

    sh = FakeMessage(user_id=99, text="/shuffle")
    await pb.shuffle_handler(broken, sh)
    assert any("Failed to shuffle" in r for r in sh.replies)


@pytest.mark.asyncio
async def test_pause_resume_volume_guards_and_errors():
    not_conf = owner_client(player_manager=None)
    m = FakeMessage(user_id=99, text="/pause")
    await pb.pause_handler(not_conf, m)
    assert any("not configured" in r for r in m.replies)

    m2 = FakeMessage(user_id=99, text="/resume")
    await pb.resume_handler(not_conf, m2)
    assert any("not configured" in r for r in m2.replies)

    m3 = FakeMessage(user_id=99, text="/volume")
    await pb.volume_handler(not_conf, m3)
    assert any("Usage: /volume" in r for r in m3.replies)

    m4 = FakeMessage(user_id=99, text="/volume x")
    await pb.volume_handler(not_conf, m4)
    assert any("Usage: /volume" in r for r in m4.replies)

    m5 = FakeMessage(user_id=99, text="/volume 80")
    await pb.volume_handler(not_conf, m5)
    assert any("not configured" in r for r in m5.replies)

    # POSIX-only pause
    player = FakePlayer()
    player.pause_raise = NotImplementedError("posix signals")
    m6 = FakeMessage(user_id=99, text="/pause")
    await pb.pause_handler(owner_client(player_manager=FakePlayerManager(player)), m6)
    assert any("POSIX" in r for r in m6.replies)

    # resume when not paused
    player2 = FakePlayer()
    player2.resume_raise = ValueError("not paused")
    m7 = FakeMessage(user_id=99, text="/resume")
    await pb.resume_handler(owner_client(player_manager=FakePlayerManager(player2)), m7)
    assert any("not paused" in r for r in m7.replies)

    # volume stored when nothing playing
    player3 = FakePlayer()
    player3.volume_returns = False
    m8 = FakeMessage(user_id=99, text="/volume 50")
    await pb.volume_handler(owner_client(player_manager=FakePlayerManager(player3)), m8)
    assert any("stored" in r for r in m8.replies)

    # underlying failure surfaces
    broken = owner_client(player_manager=BrokenManager())
    m9 = FakeMessage(user_id=99, text="/volume 80")
    await pb.volume_handler(broken, m9)
    assert any("Failed to set volume" in r for r in m9.replies)


@pytest.mark.asyncio
async def test_fmt_duration_edge_cases():
    assert pb._fmt_duration("abc") == "abcs"
    assert pb._fmt_duration(-30) == "0:00"
    assert pb._fmt_duration(61) == "1:01"


@pytest.mark.asyncio
async def test_stop_handler_paths():
    # denied for non-admin
    msg = FakeMessage(user_id=5)
    await pb.stop_handler(_client(player_manager=FakePlayerManager(FakePlayer())), msg)
    assert any("permission" in r.lower() for r in msg.replies)

    # no player manager, no voice
    msg2 = FakeMessage(user_id=99, text="/stop")
    await pb.stop_handler(owner_client(), msg2)
    assert any("Voice support not configured" in r for r in msg2.replies)

    # no player manager, voice direct stop
    voice = FakeVoice()
    msg3 = FakeMessage(user_id=99, text="/stop")
    await pb.stop_handler(owner_client(player_manager=None, voice=voice), msg3)
    assert voice.stopped == 1
    assert any("Stopped" in r for r in msg3.replies)

    # voice direct-stop failure
    class ErrVoice(FakeVoice):
        async def stop_playback(self, chat_id):
            raise RuntimeError("stop failed")

    msg4 = FakeMessage(user_id=99, text="/stop")
    await pb.stop_handler(owner_client(player_manager=None, voice=ErrVoice()), msg4)
    assert any("Failed to stop" in r for r in msg4.replies)


@pytest.mark.asyncio
async def test_skip_clear_extra_paths():
    # skip without player manager
    msg = FakeMessage(user_id=99, text="/skip")
    await pb.skip_handler(_client(), msg)
    assert any("Queue support" in r for r in msg.replies)

    # clear without player manager
    msg2 = FakeMessage(user_id=99, text="/clear")
    await pb.clear_handler(_client(), msg2)
    assert any("Queue support" in r for r in msg2.replies)

    # skip on a player with no skip attribute
    msg3 = FakeMessage(user_id=99, text="/skip")
    await pb.skip_handler(owner_client(player_manager=FakePlayerManager(object())), msg3)
    assert any("not available" in r for r in msg3.replies)

    # clear fallback to stop
    stop_only = StopOnlyPlayer()
    msg4 = FakeMessage(user_id=99, text="/clear")
    await pb.clear_handler(owner_client(player_manager=FakePlayerManager(stop_only)), msg4)
    assert any("Cleared the queue" in r for r in msg4.replies)
    assert stop_only.stopped is True

    # neither clear nor stop
    msg5 = FakeMessage(user_id=99, text="/clear")
    await pb.clear_handler(owner_client(player_manager=FakePlayerManager(object())), msg5)
    assert any("not available" in r for r in msg5.replies)


@pytest.mark.asyncio
async def test_control_handlers_broken_manager():
    client = owner_client(player_manager=BrokenManager())

    msg = FakeMessage(user_id=99, text="/clear")
    await pb.clear_handler(client, msg)
    assert any("Failed to clear" in r for r in msg.replies)

    msg2 = FakeMessage(user_id=99, text="/pause")
    await pb.pause_handler(client, msg2)
    assert any("Failed to pause" in r for r in msg2.replies)

    msg3 = FakeMessage(user_id=99, text="/resume")
    await pb.resume_handler(client, msg3)
    assert any("Failed to resume" in r for r in msg3.replies)

    msg4 = FakeMessage(user_id=99, text="/skip")
    await pb.skip_handler(client, msg4)
    assert any("Failed to skip" in r for r in msg4.replies)


class FakeMetrics:
    def __init__(self):
        self.calls = []

    def inc(self, name, amount=1):
        self.calls.append(name)


class LocksReleaseFail:
    async def acquire(self, *a, **k):
        return "tok"

    async def release(self, *a, **k):
        return False


class LocksReleaseRaise:
    async def acquire(self, *a, **k):
        return "tok"

    async def release(self, *a, **k):
        raise RuntimeError("release boom")


@pytest.mark.asyncio
async def test_join_leave_lock_metric_paths():
    # join: acquire-denied increments the acquire_failed counter
    denied = FakeMessage()
    met1 = FakeMetrics()
    await pb.join_handler(_client(voice=FakeVoice(), locks=LocksDenied(), metrics=met1), denied)
    assert any("Another join/leave" in r for r in denied.replies)
    assert "locks.acquire_failed.join" in met1.calls

    # join: successful release with metrics present increments the released counter
    ok = FakeMessage()
    met2 = FakeMetrics()
    await pb.join_handler(_client(voice=FakeVoice(), locks=LocksOk(), metrics=met2), ok)
    assert any("Joined" in r for r in ok.replies)
    assert "locks.released.join" in met2.calls

    # join: release failure counts as released-failed (ok=False)
    fail = FakeMessage()
    met3 = FakeMetrics()
    await pb.join_handler(_client(voice=FakeVoice(), locks=LocksReleaseFail(), metrics=met3), fail)
    assert "locks.release_failed.join" in met3.calls

    # join: release raising counts as a release exception
    raise_join = FakeMessage()
    met4 = FakeMetrics()
    await pb.join_handler(_client(voice=FakeVoice(), locks=LocksReleaseRaise(), metrics=met4), raise_join)
    assert "locks.release_exception.join" in met4.calls

    # leave: acquire-denied increments the leave acquire_failed counter
    leave_denied = FakeMessage()
    met5 = FakeMetrics()
    await pb.leave_handler(_client(voice=FakeVoice(), locks=LocksDenied(), metrics=met5), leave_denied)
    assert "locks.acquire_failed.leave" in met5.calls

    # leave: successful release with metrics increments released.leave
    leave_ok = FakeMessage()
    met6 = FakeMetrics()
    await pb.leave_handler(_client(voice=FakeVoice(), locks=LocksOk(), metrics=met6), leave_ok)
    assert any("Left" in r for r in leave_ok.replies)
    assert "locks.released.leave" in met6.calls

    # leave: release failure counts as released-failed
    leave_fail = FakeMessage()
    met7 = FakeMetrics()
    await pb.leave_handler(_client(voice=FakeVoice(), locks=LocksReleaseFail(), metrics=met7), leave_fail)
    assert "locks.release_failed.leave" in met7.calls

    # leave: release raising counts as a release exception
    leave_raise = FakeMessage()
    met8 = FakeMetrics()
    await pb.leave_handler(_client(voice=FakeVoice(), locks=LocksReleaseRaise(), metrics=met8), leave_raise)
    assert "locks.release_exception.leave" in met8.calls

    # leave: the voice client failing propagates to the outer error reply
    class ErrLeaveVoice(FakeVoice):
        async def leave(self, chat_id):
            raise RuntimeError("leave boom")

    leave_broken = FakeMessage()
    await pb.leave_handler(_client(voice=ErrLeaveVoice(), locks=LocksOk()), leave_broken)
    assert any("Failed to leave" in r for r in leave_broken.replies)


@pytest.mark.asyncio
async def test_play_handler_rate_limiter_exception(monkeypatch):
    class RaisingRL:
        async def allow(self, key, limit, period):
            raise RuntimeError("redis down")

    monkeypatch.setattr(pb, "get_default_providers", lambda: [])
    client = _client(voice=FakeVoice(), rate_limiter=RaisingRL(), player_manager=None)
    msg = FakeMessage(chat_id=1, user_id=7, text="/play my song")
    await pb.play_handler(client, msg)
    assert any("Playing" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_play_handler_no_pm_lock_metric_paths(monkeypatch):
    monkeypatch.setattr(pb, "get_default_providers", lambda: [])

    # direct-play lock acquisition denied
    denied = FakeMessage(chat_id=1, user_id=7, text="/play my song")
    met = FakeMetrics()
    await pb.play_handler(_client(voice=FakeVoice(), player_manager=None, locks=LocksDenied(), metrics=met), denied)
    assert any("Another playback is starting" in r for r in denied.replies)
    assert "locks.acquire_failed.play" in met.calls

    # direct-play lock released successfully
    ok = FakeMessage(chat_id=1, user_id=7, text="/play my song")
    met2 = FakeMetrics()
    await pb.play_handler(_client(voice=FakeVoice(), player_manager=None, locks=LocksOk(), metrics=met2), ok)
    assert any("Playing" in r for r in ok.replies)
    assert "locks.released.play" in met2.calls

    # direct-play lock release failed (ok=False)
    fail = FakeMessage(chat_id=1, user_id=7, text="/play my song")
    met3 = FakeMetrics()
    await pb.play_handler(_client(voice=FakeVoice(), player_manager=None, locks=LocksReleaseFail(), metrics=met3), fail)
    assert "locks.release_failed.play" in met3.calls

    # direct-play lock release raising -> counted as a release exception
    raise_play = FakeMessage(chat_id=1, user_id=7, text="/play my song")
    met4 = FakeMetrics()
    await pb.play_handler(
        _client(voice=FakeVoice(), player_manager=None, locks=LocksReleaseRaise(), metrics=met4), raise_play
    )
    assert any("Playing" in r for r in raise_play.replies)
    assert "locks.release_exception.play" in met4.calls


@pytest.mark.asyncio
async def test_play_handler_enqueue_value_error(monkeypatch):
    class ValueErrorPlayer(FakePlayer):
        async def enqueue(self, track):
            raise ValueError("That's not a playable audio source")

    monkeypatch.setattr(pb, "get_default_providers", lambda: [])
    msg = FakeMessage(chat_id=1, user_id=7, text="/play my song")
    await pb.play_handler(_client(voice=FakeVoice(), player_manager=FakePlayerManager(ValueErrorPlayer())), msg)
    assert any("not a playable" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_resolve_track_empty_and_broken_inner(monkeypatch):
    class EmptyProvider:
        async def search(self, query):
            return []

    monkeypatch.setattr(pb, "get_default_providers", lambda: [EmptyProvider()])
    client = _client()
    msg = FakeMessage(user_id=7, text="/play x")
    track, url, prov, results = await pb._resolve_track(client, msg, "some title")
    assert prov is None
    assert track.source == "url"
    assert url == "some title"

    class ResolveFail:
        async def search(self, query):
            return [Track(id="bad", title="Bad", source="yt-dlp", source_url="http://sc/bad")]

        async def resolve_audio(self, ref):
            raise RuntimeError("resolve down")

    class GoodProvider:
        async def search(self, query):
            return [Track(id="ok", title="Good", source="yt-dlp", source_url="http://sc/ok")]

        async def resolve_audio(self, ref):
            return "http://audio/m3u8"

    monkeypatch.setattr(pb, "get_default_providers", lambda: [ResolveFail(), GoodProvider()])
    track2, url2, _prov, _results = await pb._resolve_track(client, msg, "x")
    assert track2.id == "ok"
    assert url2 == "http://audio/m3u8"


@pytest.mark.asyncio
async def test_playnext_playlist_sets_requested_by(monkeypatch):
    player = FakePlayer()
    client = _client(player_manager=FakePlayerManager(player), voice=FakeVoice())
    first = Track(id="a", title="First")
    monkeypatch.setattr(pb, "_maybe_playlist", _fake_playlist(first))
    msg = FakeMessage(chat_id=1, user_id=7, text="/playnext https://youtu.be/abc")
    await pb.playnext_handler(client, msg)
    assert player.enqueued_next
    assert first.requested_by == 7


@pytest.mark.asyncio
async def test_playnext_picker_returns_and_value_error(monkeypatch):
    provider = type("P", (), {})()

    async def resolve(client, message, input_source):
        return (
            Track(id="1", title="One"),
            "http://audio/m3u8",
            provider,
            [Track(id="1", title="One"), Track(id="2", title="Two")],
        )

    monkeypatch.setattr(pb, "_resolve_track", resolve)
    player = FakePlayer()
    client = _client(player_manager=FakePlayerManager(player), voice=FakeVoice())
    client.pending_picks = {}
    msg = FakeMessage(chat_id=1, user_id=7, text="/playnext some query")
    await pb.playnext_handler(client, msg)
    assert any("Search results" in r for r in msg.replies)
    assert player.enqueued_next == []

    async def refuse(client, message, input_source):
        raise ValueError("bad input source")

    monkeypatch.setattr(pb, "_resolve_track", refuse)
    msg2 = FakeMessage(chat_id=1, user_id=7, text="/playnext some query")
    await pb.playnext_handler(client, msg2)
    assert any("bad input source" in r for r in msg2.replies)


@pytest.mark.asyncio
async def test_quiet_answer_without_alert():
    q = FakeQuery("pick:x:0")
    await pb._quiet_answer(q, "bye", show_alert=False)
    assert q.answered == "bye"
    assert q.alerted is False


@pytest.mark.asyncio
async def test_pick_callback_unexpected_enqueue_error(monkeypatch):
    provider = type("P", (), {})()
    provider.tracks = [
        Track(id="1", title="One", source="yt-dlp", source_url="http://sc/x"),
        Track(id="2", title="Two", source="yt-dlp", source_url="http://sc/y"),
    ]

    async def good_resolve(ref):
        return "http://audio/m3u8"

    provider.resolve_audio = good_resolve

    class BoomPlayer(FakePlayer):
        async def enqueue(self, track):
            raise RuntimeError("boom")

    client = _client(player_manager=FakePlayerManager(BoomPlayer()), voice=FakeVoice())
    client.pending_picks = {}
    msg = FakeMessage(chat_id=1, user_id=7)
    await pb._offer_picker(client, msg, 1, provider, provider.tracks)
    nonce = list(client.pending_picks)[0]
    q = FakeQuery(f"pick:{nonce}:0", user_id=7)
    await pb.pick_callback(client, q)
    assert "Failed to start playback" in q.answered


@pytest.mark.asyncio
async def test_admin_guard_permission_check_failure(monkeypatch):
    async def raise_privileged(client, message):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(pb, "_is_privileged", raise_privileged)
    msg = FakeMessage(user_id=99)
    assert await pb._admin_guard(owner_client(), msg) is False
    assert any("Permission check failed" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_admin_guarded_handlers_denied(monkeypatch):
    async def deny_guard(client, message):
        return False

    monkeypatch.setattr(pb, "_admin_guard", deny_guard)

    msg = FakeMessage(user_id=99, text="/rm 1")
    await pb.remove_handler(owner_client(), msg)
    assert msg.replies == []

    msg2 = FakeMessage(user_id=99, text="/move 1 2")
    await pb.move_handler(owner_client(), msg2)
    assert msg2.replies == []

    msg3 = FakeMessage(user_id=99, text="/shuffle")
    await pb.shuffle_handler(owner_client(), msg3)
    assert msg3.replies == []

    msg4 = FakeMessage(user_id=99, text="/pause")
    await pb.pause_handler(owner_client(), msg4)
    assert msg4.replies == []

    msg5 = FakeMessage(user_id=99, text="/resume")
    await pb.resume_handler(owner_client(), msg5)
    assert msg5.replies == []

    msg6 = FakeMessage(user_id=99, text="/volume 50")
    await pb.volume_handler(owner_client(), msg6)
    assert msg6.replies == []


@pytest.mark.asyncio
async def test_volume_handler_applied():
    msg = FakeMessage(user_id=99, text="/volume 50")
    await pb.volume_handler(owner_client(player_manager=FakePlayerManager(FakePlayer())), msg)
    assert any("Volume set to 50%" in r for r in msg.replies)


@pytest.mark.asyncio
async def test_stop_permission_failure_and_stop_error(monkeypatch):
    async def raise_privileged(client, message):
        raise RuntimeError("permission service down")

    monkeypatch.setattr(pb, "_is_privileged", raise_privileged)
    msg = FakeMessage(user_id=99, text="/stop")
    await pb.stop_handler(owner_client(player_manager=FakePlayerManager(FakePlayer())), msg)
    assert any("Permission check failed" in r for r in msg.replies)

    class StopFail(FakePlayer):
        async def stop(self):
            raise RuntimeError("player stopped")

    async def allow_privileged(client, message):
        return True

    monkeypatch.setattr(pb, "_is_privileged", allow_privileged)
    stop_msg = FakeMessage(user_id=99, text="/stop")
    await pb.stop_handler(owner_client(player_manager=FakePlayerManager(StopFail())), stop_msg)
    assert any("Failed to stop playback" in r for r in stop_msg.replies)


@pytest.mark.asyncio
async def test_skip_clear_permission_paths(monkeypatch):
    async def raise_privileged(client, message):
        raise RuntimeError("permission service down")

    monkeypatch.setattr(pb, "_is_privileged", raise_privileged)
    msg = FakeMessage(user_id=99, text="/skip")
    await pb.skip_handler(owner_client(player_manager=FakePlayerManager(FakePlayer())), msg)
    assert any("Permission check failed" in r for r in msg.replies)

    msg2 = FakeMessage(user_id=99, text="/clear")
    await pb.clear_handler(owner_client(player_manager=FakePlayerManager(FakePlayer())), msg2)
    assert any("Permission check failed" in r for r in msg2.replies)

    async def deny_privileged(client, message):
        return False

    monkeypatch.setattr(pb, "_is_privileged", deny_privileged)
    msg3 = FakeMessage(user_id=99, text="/skip")
    await pb.skip_handler(owner_client(player_manager=FakePlayerManager(FakePlayer())), msg3)
    assert any("don't have permission to skip" in r for r in msg3.replies)

    msg4 = FakeMessage(user_id=99, text="/clear")
    await pb.clear_handler(owner_client(player_manager=FakePlayerManager(FakePlayer())), msg4)
    assert any("don't have permission to clear" in r for r in msg4.replies)
