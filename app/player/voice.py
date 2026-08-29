import asyncio
import logging
from typing import Any

from pyrogram import Client

try:
    from pytgcalls import PyTgCalls
    from pytgcalls.types.input_stream import AudioPiped
except Exception:  # pragma: no cover - optional dependency
    PyTgCalls = None
    AudioPiped = None

from .audio_engine import AudioEngine

logger = logging.getLogger(__name__)


class VoiceManager:
    """Manage voice chat connections and playback using pytgcalls + AudioEngine.

    Behavior:
    - If `pytgcalls` and `AudioPiped` are available, prefer using `AudioPiped`
      so pytgcalls handles FFmpeg piping internally.
    - Otherwise, use a per-chat `AudioEngine` to spawn FFmpeg locally.
    """

    def __init__(self, app: Client) -> None:
        self._app = app
        self._pytgcalls = PyTgCalls(app) if PyTgCalls is not None else None
        self._joined: set[int] = set()
        # per-chat FFmpeg engine so different chats don't share one process
        self._audio: dict[int, AudioEngine] = {}
        # map chat_id -> list of async callbacks to call when stream ends
        self._on_stream_end_callbacks: dict[int, list] = {}
        self._callback_tasks: set[asyncio.Task] = set()

        # Try to hook into pytgcalls stream-end event if available
        if self._pytgcalls is not None:
            handler = getattr(self._pytgcalls, "on_stream_end", None)
            if callable(handler):
                try:
                    # register our internal handler
                    handler(self._internal_stream_end_handler)
                except Exception:
                    # some pytgcalls versions may have different API; ignore
                    logger.debug("pytgcalls.on_stream_end registration failed")

    async def start(self) -> None:
        if self._pytgcalls is None:
            logger.warning("pytgcalls is not available; voice disabled")
            return
        await self._pytgcalls.start()

    async def stop(self) -> None:
        """Shut down pytgcalls, pending callbacks, and all per-chat engines."""
        if self._pytgcalls is not None:
            try:
                await self._pytgcalls.stop()
            except Exception:
                logger.exception("failed to stop pytgcalls")

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

    async def join(self, chat_id: int) -> None:
        if self._pytgcalls is None:
            raise RuntimeError("Voice support not installed (pytgcalls missing)")

        if chat_id in self._joined:
            logger.debug("already joined %s", chat_id)
            return

        try:
            # Join without an active stream; stream will be started via `play`
            await self._pytgcalls.join_group_call(chat_id, None)  # type: ignore[arg-type]
            self._joined.add(chat_id)
            logger.info("joined voice chat %s", chat_id)
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
            await self._pytgcalls.leave_group_call(chat_id)
        except Exception as e:
            logger.exception("failed to leave voice chat %s: %s", chat_id, e)
            raise
        finally:
            # Leaving the chat should also free any engine-mode FFmpeg process
            # so audio does not keep playing after the bot leaves.
            await self._stop_engine(chat_id)
            self._joined.discard(chat_id)
            logger.info("left voice chat %s", chat_id)

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

        If `AudioPiped` is available, pytgcalls manages FFmpeg internally and
        the call is joined in the same operation. Otherwise a per-chat
        `AudioEngine` (plain FFmpeg) is started locally. A `volume` other than
        `1.0` is applied via an FFmpeg volume filter.
        """
        # Stop any existing engine for this chat first so switching from
        # engine-mode to pytgcalls-mode (or restarting) never leaks an FFmpeg
        # process.
        await self._stop_engine(chat_id)

        if self._pytgcalls and AudioPiped is not None:
            try:
                # AudioPiped will run ffmpeg internally; pass the source directly
                ffp = f"-af volume={volume:g}" if volume != 1.0 else ""
                stream = AudioPiped(input_source, ffmpeg_parameters=ffp) if ffp else AudioPiped(input_source)
                await self._pytgcalls.join_group_call(chat_id, stream)
                self._joined.add(chat_id)
                logger.info("playing %s in chat %s via AudioPiped", input_source, chat_id)
                return {"mode": "pytgcalls"}
            except Exception:
                logger.exception("AudioPiped playback failed; falling back to AudioEngine")

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

        if self._pytgcalls and AudioPiped is not None:
            try:
                ffp = f"-af volume={volume:g}" if volume != 1.0 else ""
                stream = AudioPiped(input_source, ffmpeg_parameters=ffp) if ffp else AudioPiped(input_source)
                await self._pytgcalls.join_group_call(chat_id, stream)
                self._joined.add(chat_id)
                logger.info("volume set for %s in chat %s via AudioPiped", volume, chat_id)
                return {"mode": "pytgcalls"}
            except Exception:
                logger.exception("AudioPiped volume change failed; falling back to AudioEngine")

        engine = AudioEngine()
        self._audio[chat_id] = engine
        extra = ["-af", f"volume={volume:g}"] if volume != 1.0 else None
        await engine.play(input_source, extra_args=extra)
        return {"mode": "engine", "engine": engine}

    async def pause_playback(self, chat_id: int) -> None:
        """Pause the active stream in a chat (engine, else pytgcalls)."""
        # If this chat is engine-backed (pytgcalls unavailable or AudioPiped
        # fell back), pause the engine directly so the action actually takes
        # effect instead of being a no-op against pytgcalls.
        if chat_id in self._audio:
            await self._audio[chat_id].pause()
            return
        if self._pytgcalls is not None:
            method: Any = getattr(self._pytgcalls, "pause_stream", None)
            if callable(method):
                try:
                    await method(chat_id)
                    return
                except Exception:
                    logger.debug("pytgcalls pause_stream failed for %s", chat_id)
        raise RuntimeError("no active playback to pause")

    async def resume_playback(self, chat_id: int) -> None:
        """Resume the active stream in a chat (engine, else pytgcalls)."""
        if chat_id in self._audio:
            await self._audio[chat_id].resume()
            return
        if self._pytgcalls is not None:
            method: Any = getattr(self._pytgcalls, "resume_stream", None)
            if callable(method):
                try:
                    await method(chat_id)
                    return
                except Exception:
                    logger.debug("pytgcalls resume_stream failed for %s", chat_id)
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

    async def _internal_stream_end_handler(self, event) -> None:
        """Internal handler called by pytgcalls when a stream ends.

        This is best-effort: different pytgcalls versions supply different event
        shapes. We attempt to extract a chat id and notify registered callbacks.
        """
        # Try common event attributes to find chat_id
        chat_id = None
        try:
            if hasattr(event, "chat_id"):
                chat_id = event.chat_id
            elif hasattr(event, "group_call") and hasattr(event.group_call, "chat_id"):
                chat_id = event.group_call.chat_id
            elif hasattr(event, "call") and hasattr(event.call, "chat_id"):
                chat_id = event.call.chat_id
        except Exception:
            logger.debug("could not extract chat_id from stream-end event")

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
                await self._pytgcalls.leave_group_call(chat_id)
            except Exception:
                logger.exception("failed to leave group call for %s", chat_id)

        # stop and drop this chat's engine, freeing its ffmpeg process
        engine = self._audio.pop(chat_id, None)
        if engine is not None:
            try:
                await engine.stop()
            except Exception:
                logger.exception("failed to stop audio engine for chat %s", chat_id)
