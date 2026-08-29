import asyncio
import logging
from enum import Enum, auto

from .models import Track
from .queue import Queue

logger = logging.getLogger(__name__)


class PlaybackState(Enum):
    IDLE = auto()
    CONNECTING = auto()
    PLAYING = auto()
    PAUSED = auto()
    STOPPING = auto()
    ERROR = auto()


class Player:
    def __init__(self, chat_id: int, voice_manager, session_factory=None) -> None:
        self.chat_id = chat_id
        self.voice = voice_manager
        self.queue = Queue(session_factory=session_factory, chat_id=chat_id)
        self.current: Track | None = None
        self.state: PlaybackState = PlaybackState.IDLE
        self.volume: float = 1.0
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None

    async def enqueue(self, track: Track) -> int:
        pos = await self.queue.add(track)
        logger.info("track enqueued chat=%s track=%s pos=%s", self.chat_id, track.id, pos)
        # if idle, trigger playback
        if self.state in (PlaybackState.IDLE, PlaybackState.STOPPING):
            asyncio.create_task(self._ensure_playback())
        return pos

    async def enqueue_next(self, track: Track) -> int:
        pos = await self.queue.add_next(track)
        logger.info("track queued next chat=%s track=%s", self.chat_id, track.id)
        if self.state in (PlaybackState.IDLE, PlaybackState.STOPPING):
            asyncio.create_task(self._ensure_playback())
        return pos

    async def pause(self) -> None:
        async with self._lock:
            if self.state != PlaybackState.PLAYING:
                raise ValueError("No active playback to pause.")
            await self.voice.pause_playback(self.chat_id)
            self.state = PlaybackState.PAUSED

    async def resume(self) -> None:
        async with self._lock:
            if self.state != PlaybackState.PAUSED:
                raise ValueError("Playback is not paused.")
            await self.voice.resume_playback(self.chat_id)
            self.state = PlaybackState.PLAYING

    async def set_volume(self, volume: float) -> bool:
        """Set playback volume (0.0-2.0) and apply it to the current track.

        Returns True if the volume was applied to an active stream, False if
        nothing is currently playing (the value is stored for the next track).
        """
        volume = max(0.0, min(2.0, volume))
        self.volume = volume
        cur = self.current
        if cur is None or self.state != PlaybackState.PLAYING:
            return False
        self._cancel_watch()
        source = cur.source_url or cur.source_id or cur.id
        try:
            result = await self.voice.set_volume(self.chat_id, source, volume)
            mode = result.get("mode") if isinstance(result, dict) else None
            if mode == "engine":
                self._watch_task = asyncio.create_task(self._handle_track_end(result.get("engine")))
            return True
        except Exception:
            logger.exception("failed to apply volume change chat=%s", self.chat_id)
            self.state = PlaybackState.ERROR
            return False

    def _cancel_watch(self) -> None:
        """Cancel any in-flight track-end watcher."""
        if self._watch_task is not None and not self._watch_task.done():
            self._watch_task.cancel()
        self._watch_task = None

    async def _ensure_playback(self) -> None:
        async with self._lock:
            # Treat CONNECTING as in-progress so that concurrent enqueues
            # cannot start a second voice.play while one is already starting.
            if self.state in (PlaybackState.PLAYING, PlaybackState.CONNECTING):
                return
            # Get next track
            next_track = await self.queue.pop_next()
            if not next_track:
                self.state = PlaybackState.IDLE
                return
            self.current = next_track
            self.state = PlaybackState.CONNECTING

            try:
                result = await self.voice.play(
                    self.chat_id,
                    next_track.source_url or next_track.source_id or next_track.id,
                    volume=self.volume,
                )
                self.state = PlaybackState.PLAYING
                mode = result.get("mode") if isinstance(result, dict) else None

                # Engine-based playback: wait for it to finish and then
                # automatically advance to the next track.
                if mode == "engine":
                    self._cancel_watch()
                    self._watch_task = asyncio.create_task(self._handle_track_end(result.get("engine")))
                else:
                    # pytgcalls mode: advance via the stream-end event callback.
                    try:
                        self.voice.register_on_stream_end(self.chat_id, self._on_pytgcalls_end)
                    except Exception:
                        logger.debug("could not register pytgcalls end-event callback for chat %s", self.chat_id)
            except Exception as e:
                logger.exception("playback failed chat=%s track=%s %s", self.chat_id, next_track.id, e)
                self.state = PlaybackState.ERROR

    async def skip(self) -> None:
        async with self._lock:
            self._cancel_watch()
            await self.voice.stop_playback(self.chat_id)
            self.state = PlaybackState.IDLE
            self.current = None
            # start next
            asyncio.create_task(self._ensure_playback())

    async def _handle_track_end(self, engine) -> None:
        try:
            await engine.wait_finished()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("error while waiting for engine to finish")

        # cleanup and start next track
        async with self._lock:
            self.current = None
            self.state = PlaybackState.IDLE
            asyncio.create_task(self._ensure_playback())

    async def _on_pytgcalls_end(self, chat_id: int) -> None:
        # Called when pytgcalls reports the stream ended in this chat
        async with self._lock:
            if chat_id != self.chat_id:
                return
            self.current = None
            self.state = PlaybackState.IDLE
            asyncio.create_task(self._ensure_playback())

    async def clear(self) -> None:
        """Clear pending queued tracks without stopping the current playback."""
        async with self._lock:
            await self.queue.clear()

    async def stop(self) -> None:
        async with self._lock:
            self._cancel_watch()
            await self.voice.stop_playback(self.chat_id)
            self.state = PlaybackState.IDLE
            self.current = None
            await self.queue.clear()


class PlayerManager:
    def __init__(self, voice_manager, session_factory=None) -> None:
        self._players: dict[int, Player] = {}
        self._voice = voice_manager
        self._session_factory = session_factory
        self._lock = asyncio.Lock()

    async def get_player(self, chat_id: int) -> Player:
        async with self._lock:
            p = self._players.get(chat_id)
            if p is None:
                p = Player(chat_id, self._voice, session_factory=self._session_factory)
                self._players[chat_id] = p
            return p
