import asyncio
import logging
from typing import Any

import pyrogram.errors as _pyro_errors
from pyrogram import Client

try:
    from pytgcalls import PyTgCalls
    from pytgcalls import filters as fl
    from pytgcalls.types import MediaStream, StreamEnded
except Exception:  # pragma: no cover - optional dependency
    PyTgCalls = None
    MediaStream = None
    StreamEnded = None

try:
    from pytgcalls.exceptions import NoActiveGroupCall
except Exception:  # pragma: no cover - optional dependency
    NoActiveGroupCall = ()

from pyrogram.errors import BotMethodInvalid

from .audio_engine import AudioEngine

logger = logging.getLogger(__name__)

# A bot account cannot create group calls (phone.createGroupCall is forbidden),
# so playback/join depends on a user having an active voice chat in the group.
NO_ACTIVE_VOICE_CHAT_REASON = "no active voice chat to stream into"
NO_ACTIVE_VOICE_CHAT_MSG = "❌ No voice chat is active in this chat. Start the group's voice chat, then try again."

# py-tgcalls imports `GroupcallForbidden`/`GroupcallInvalid` from pyrogram.errors,
# but modern Pyrogram releases no longer define them. Add safe aliases so PyTgCalls
# can be initialized (these are only ever referenced in `except` clauses). This is
# defensive: the alias only applies when the symbol is genuinely missing.
for _missing in ("GroupcallForbidden", "GroupcallInvalid"):
    if not hasattr(_pyro_errors, _missing):
        _Base = getattr(_pyro_errors, "RPCError", Exception)
        setattr(_pyro_errors, _missing, type(_missing, (_Base,), {}))


class VoiceManager:
    """Manage voice chat connections and playback using pytgcalls + AudioEngine.

    Behavior:
    - If `pytgcalls` and `MediaStream` are available, prefer using `MediaStream`
      so pytgcalls handles FFmpeg piping internally.
    - Otherwise, use a per-chat `AudioEngine` to spawn FFmpeg locally.
    """

    def __init__(self, app: Client, assistant: Client | None = None) -> None:
        self._app = app
        # Playback rides on the assistant (user account, can create group
        # calls) when one is configured; otherwise the bot account is used and
        # a user must start the voice chat first.
        self._voice_app = assistant if assistant is not None else app
        self._pytgcalls = None
        if PyTgCalls is not None:
            try:
                self._pytgcalls = PyTgCalls(self._voice_app)
            except Exception:
                # An invalid/None MTProto client may fail to initialize; keep
                # voice disabled in that case (e.g. some test setups).
                logger.warning("failed to initialize PyTgCalls; voice disabled")
                self._pytgcalls = None
        self._joined: set[int] = set()
        # per-chat FFmpeg engine so different chats don't share one process
        self._audio: dict[int, AudioEngine] = {}
        # map chat_id -> list of async callbacks to call when stream ends
        self._on_stream_end_callbacks: dict[int, list] = {}
        self._callback_tasks: set[asyncio.Task] = set()

        # Hook into pytgcalls stream-end event if available
        if self._pytgcalls is not None and StreamEnded is not None:
            try:
                self._pytgcalls.on_update(fl.stream_end())(self._internal_stream_end_handler)
            except Exception:
                # some pytgcalls versions may have a different API; ignore
                logger.debug("pytgcalls stream-end registration failed")

    async def start(self) -> None:
        if self._pytgcalls is None:
            logger.warning("pytgcalls is not available; voice disabled")
            return
        await self._pytgcalls.start()

    async def stop(self) -> None:
        """Shut down pytgcalls, pending callbacks, and all per-chat engines."""
        if self._pytgcalls is not None:
            method = getattr(self._pytgcalls, "stop", None)
            if callable(method):
                try:
                    await method()
                except Exception:
                    logger.exception("failed to stop pytgcalls")
            else:
                logger.debug("pytgcalls has no stop(); skipped")

        for task in list(self._callback_tasks):
            if not task.done():
                task.cancel()
        if self._callback_tasks:
            await asyncio.gather(*self._callback_tasks, return_exceptions=True)
        self._callback_tasks.clear()

        for chat_id, engine in list(self._audio.items()):
            try:
                await engine.stop()
            except Exception:
                logger.exception("failed to stop audio engine for chat %s", chat_id)
        self._audio.clear()

    async def _notify(self, chat_id: int, text: str) -> None:
        """Best-effort message into a chat (used to surface playback errors)."""
        try:
            app = getattr(self, "_app", None)
            if app is not None and hasattr(app, "send_message"):
                await app.send_message(chat_id, text)
        except Exception:
            logger.debug("failed to notify chat %s", chat_id, exc_info=True)

    async def join(self, chat_id: int) -> None:
        if self._pytgcalls is None:
            raise RuntimeError("Voice support not installed (pytgcalls missing)")

        if chat_id in self._joined:
            logger.debug("already joined %s", chat_id)
            return

        try:
            # Join without an active stream; stream will be started via `play`
            await self._pytgcalls.play(chat_id)
            self._joined.add(chat_id)
            logger.info("joined voice chat %s", chat_id)
        except NoActiveGroupCall:
            await self._notify(chat_id, NO_ACTIVE_VOICE_CHAT_MSG)
            raise RuntimeError("no active voice chat to join") from None
        except BotMethodInvalid:
            # auto_start tried to create the call, but bots may not create
            # group calls; require a user already in the voice chat.
            await self._notify(chat_id, NO_ACTIVE_VOICE_CHAT_MSG)
            raise RuntimeError("no active voice chat to join") from None
        except Exception as e:
            logger.exception("failed to join voice chat %s: %s", chat_id, e)
            raise

    async def leave(self, chat_id: int) -> None:
        if self._pytgcalls is None:
            raise RuntimeError("Voice support not installed (pytgcalls missing)")

        if chat_id not in self._joined:
            logger.debug("not joined %s", chat_id)
            return

        try:
            await self._pytgcalls.leave_call(chat_id)
        except Exception as e:
            logger.exception("failed to leave voice chat %s: %s", chat_id, e)
            raise
        finally:
            # Leaving the chat should also free any engine-mode FFmpeg process
            # so audio does not keep playing after the bot leaves.
            await self._stop_engine(chat_id)
            self._joined.discard(chat_id)
            logger.info("left voice chat %s", chat_id)

    async def get_participants(self, chat_id: int) -> list:
        """Return the current voice-chat participants (best-effort)."""
        if self._pytgcalls is None:
            return []
        try:
            return await self._pytgcalls.get_participants(chat_id)
        except Exception:
            logger.debug("get_participants failed for chat %s", chat_id, exc_info=True)
            return []

    async def _stop_engine(self, chat_id: int) -> None:
        """Stop and drop the per-chat audio engine (if any), freeing its process."""
        engine = self._audio.pop(chat_id, None)
        if engine is not None:
            try:
                await engine.stop()
            except Exception:
                logger.exception("failed to stop audio engine for chat %s", chat_id)

    async def play(self, chat_id: int, input_source: str, volume: float = 1.0) -> dict:
        """Join the chat's voice call (if needed) and start playback.

        If `MediaStream` is available, pytgcalls manages FFmpeg internally and
        the call is joined in the same operation. Otherwise a per-chat
        `AudioEngine` (plain FFmpeg) is started locally. A `volume` other than
        `1.0` is applied via an FFmpeg volume filter.
        """
        # Stop any existing engine for this chat first so switching from
        # engine-mode to pytgcalls-mode (or restarting) never leaks an FFmpeg
        # process.
        await self._stop_engine(chat_id)

        if self._pytgcalls and MediaStream is not None:
            try:
                ffp = f"-af volume={volume:g}" if volume != 1.0 else ""
                stream = MediaStream(input_source, ffmpeg_parameters=ffp) if ffp else MediaStream(input_source)
                await self._pytgcalls.play(chat_id, stream)
                self._joined.add(chat_id)
                logger.info("playing %s in chat %s via MediaStream", input_source, chat_id)
                return {"mode": "pytgcalls"}
            except NoActiveGroupCall:
                await self._notify(chat_id, NO_ACTIVE_VOICE_CHAT_MSG)
                raise RuntimeError(NO_ACTIVE_VOICE_CHAT_REASON) from None
            except BotMethodInvalid:
                await self._notify(chat_id, NO_ACTIVE_VOICE_CHAT_MSG)
                raise RuntimeError(NO_ACTIVE_VOICE_CHAT_REASON) from None
            except Exception:
                logger.exception("MediaStream playback failed; falling back to AudioEngine")

        # Fallback: start per-chat AudioEngine (does not yet pipe into pytgcalls)
        engine = AudioEngine()
        self._audio[chat_id] = engine
        extra = ["-af", f"volume={volume:g}"] if volume != 1.0 else None
        await engine.play(input_source, extra_args=extra)
        logger.info("started ffmpeg for %s (engine-only)", input_source)
        return {"mode": "engine", "engine": engine}

    async def set_volume(self, chat_id: int, input_source: str, volume: float) -> dict:
        """Restart the current playback for `chat_id` at a new volume.

        Returns the same shape as `play` so the caller can re-attach a
        track-end watcher in engine mode.
        """
        await self.stop_playback(chat_id)

        if self._pytgcalls and MediaStream is not None:
            try:
                ffp = f"-af volume={volume:g}" if volume != 1.0 else ""
                stream = MediaStream(input_source, ffmpeg_parameters=ffp) if ffp else MediaStream(input_source)
                await self._pytgcalls.play(chat_id, stream)
                self._joined.add(chat_id)
                logger.info("volume set for %s in chat %s via MediaStream", volume, chat_id)
                return {"mode": "pytgcalls"}
            except NoActiveGroupCall:
                await self._notify(chat_id, NO_ACTIVE_VOICE_CHAT_MSG)
                raise RuntimeError(NO_ACTIVE_VOICE_CHAT_REASON) from None
            except BotMethodInvalid:
                await self._notify(chat_id, NO_ACTIVE_VOICE_CHAT_MSG)
                raise RuntimeError(NO_ACTIVE_VOICE_CHAT_REASON) from None
            except Exception:
                logger.exception("MediaStream volume change failed; falling back to AudioEngine")

        engine = AudioEngine()
        self._audio[chat_id] = engine
        extra = ["-af", f"volume={volume:g}"] if volume != 1.0 else None
        await engine.play(input_source, extra_args=extra)
        return {"mode": "engine", "engine": engine}

    async def pause_playback(self, chat_id: int) -> None:
        """Pause the active stream in a chat (engine, else pytgcalls)."""
        # If this chat is engine-backed (pytgcalls unavailable or MediaStream
        # fell back), pause the engine directly so the action actually takes
        # effect instead of being a no-op against pytgcalls.
        if chat_id in self._audio:
            await self._audio[chat_id].pause()
            return
        if self._pytgcalls is not None:
            method: Any = getattr(self._pytgcalls, "pause", None)
            if callable(method):
                try:
                    await method(chat_id)
                    return
                except Exception:
                    logger.debug("pytgcalls pause failed for %s", chat_id)
        raise RuntimeError("no active playback to pause")

    async def resume_playback(self, chat_id: int) -> None:
        """Resume the active stream in a chat (engine, else pytgcalls)."""
        if chat_id in self._audio:
            await self._audio[chat_id].resume()
            return
        if self._pytgcalls is not None:
            method: Any = getattr(self._pytgcalls, "resume", None)
            if callable(method):
                try:
                    await method(chat_id)
                    return
                except Exception:
                    logger.debug("pytgcalls resume failed for %s", chat_id)
        raise RuntimeError("no active playback to resume")

    def register_on_stream_end(self, chat_id: int, callback) -> None:
        """Register a coroutine callback(chat_id) to be called when stream ends (deduped)."""
        lst = self._on_stream_end_callbacks.setdefault(chat_id, [])

        def _same(a, b) -> bool:
            # bound methods compare by identity by default; compare target+func
            return getattr(a, "__self__", None) is getattr(b, "__self__", None) and getattr(
                a, "__func__", None
            ) is getattr(b, "__func__", None)

        if not any(_same(cb, callback) for cb in lst):
            lst.append(callback)

    async def _internal_stream_end_handler(self, client, update: StreamEnded) -> None:
        """Internal handler called by pytgcalls when a stream ends.

        `client` is the PyTgCalls instance (unused) and `update` is a
        `StreamEnded` event carrying the chat id. We notify registered
        per-chat callbacks.
        """
        chat_id = getattr(update, "chat_id", None)
        if chat_id is None:
            return

        callbacks = list(self._on_stream_end_callbacks.get(chat_id, []))
        for cb in callbacks:
            try:
                # schedule callback asynchronously and track it for cleanup
                task = asyncio.create_task(cb(chat_id))
                self._callback_tasks.add(task)
                task.add_done_callback(self._callback_tasks.discard)
            except Exception:
                logger.exception("stream-end callback failed for chat %s", chat_id)

    async def stop_playback(self, chat_id: int) -> None:
        """Stop playback for a chat: stop pytgcalls stream and this chat's audio engine."""
        if self._pytgcalls is not None:
            try:
                await self._pytgcalls.leave_call(chat_id)
            except Exception:
                logger.exception("failed to leave group call for %s", chat_id)

        # stop and drop this chat's engine, freeing its ffmpeg process
        engine = self._audio.pop(chat_id, None)
        if engine is not None:
            try:
                await engine.stop()
            except Exception:
                logger.exception("failed to stop audio engine for chat %s", chat_id)
