import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from enum import Enum, auto

from ..sources.validation import is_http_url, looks_like_audio
from .models import Track
from .queue import Queue
from .voice import NO_ACTIVE_VOICE_CHAT_REASON

logger = logging.getLogger(__name__)

DEFAULT_MAX_QUEUE_SIZE = 200
DEFAULT_MAX_PLAY_RETRIES = 2


class PlaybackState(Enum):
    IDLE = auto()
    CONNECTING = auto()
    PLAYING = auto()
    PAUSED = auto()
    STOPPING = auto()
    ERROR = auto()


class Player:
    # Extra time (seconds) after a track's nominal duration before the stall
    # watchdog force-advances the queue.
    STALL_MARGIN = 60

    def __init__(
        self,
        chat_id: int,
        voice_manager,
        session_factory=None,
        resolver: "Callable[[Track], Awaitable[str]] | None" = None,
        stall_margin: int = STALL_MARGIN,
        max_queue_size: int | None = DEFAULT_MAX_QUEUE_SIZE,
        max_retries: int = DEFAULT_MAX_PLAY_RETRIES,
    ) -> None:
        self.chat_id = chat_id
        self.voice = voice_manager
        self.queue = Queue(session_factory=session_factory, chat_id=chat_id, max_size=max_queue_size)
        self.current: Track | None = None
        self.state: PlaybackState = PlaybackState.IDLE
        self.volume: float = 1.0
        # Freshly resolve a stable track into a playable URL at play time.
        self.resolver = resolver
        self.stall_margin = stall_margin
        self.max_retries = max_retries
        self._retries = 0
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None
        self._stall_task: asyncio.Task | None = None
        self._play_task: asyncio.Task | None = None
        self._ensure_playback_running = False

    # ------------------------------------------------------------------ #
    # input validation
    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate_track(track: Track) -> None:
        """Reject tracks whose only source is a non-audio URL.

        Search results carry a playable/known-page URL plus a resolvable key,
        so only *directly pasted* URLs (``source == "url"``) that don't look
        like audio (a website, an expired link) are refused at the queue door.
        This is the last line of defense preventing junk from ever reaching the
        voice manager.
        """
        source_url = track.source_url or ""
        if track.source == "url" and is_http_url(source_url) and not looks_like_audio(source_url):
            raise ValueError(f"Refusing to queue a non-audio URL: {source_url}")

    # ------------------------------------------------------------------ #
    # public command API
    # ------------------------------------------------------------------ #
    async def enqueue(self, track: Track) -> int:
        self._validate_track(track)
        pos = await self.queue.add(track)
        logger.info("track enqueued chat=%s track=%s pos=%s", self.chat_id, track.id, pos)
        # if idle, trigger playback
        if self.state in (PlaybackState.IDLE, PlaybackState.STOPPING):
            self._spawn_playback()
        return pos

    async def enqueue_next(self, track: Track) -> int:
        self._validate_track(track)
        pos = await self.queue.add_next(track)
        logger.info("track queued next chat=%s track=%s", self.chat_id, track.id)
        if self.state in (PlaybackState.IDLE, PlaybackState.STOPPING):
            self._spawn_playback()
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
        source = await self._resolve_source(cur)
        try:
            result = await self.voice.set_volume(self.chat_id, source, volume)
            mode = result.get("mode") if isinstance(result, dict) else None
            if mode == "engine":
                self._watch_task = asyncio.create_task(self._handle_track_end(result.get("engine")))
            return True
        except Exception:
            logger.exception("failed to apply volume change chat=%s", self.chat_id)
            # The stream was torn down before the restart attempt; requeue the
            # current track so it can be replayed once the VC is available.
            try:
                await self.queue.add_next(cur)
            except Exception:
                logger.exception("failed to requeue current track after volume failure")
            self.current = None
            self.state = PlaybackState.IDLE
            return False

    def _cancel_watch(self) -> None:
        """Cancel any in-flight track-end watcher."""
        if self._watch_task is not None and not self._watch_task.done():
            self._watch_task.cancel()
        self._watch_task = None

    def _cancel_stall_watch(self) -> None:
        """Cancel the stall watchdog (must not be called from within it)."""
        if self._stall_task is not None and not self._stall_task.done():
            self._stall_task.cancel()
        self._stall_task = None

    def _spawn_playback(self) -> asyncio.Task | None:
        """Start the playback coroutine as a tracked task (or no-op if running)."""
        if self._play_task is not None and not self._play_task.done():
            return self._play_task
        # Avoid a pile-up of concurrent _ensure_playback runners.
        if self._ensure_playback_running:
            return None
        self._ensure_playback_running = True

        async def _run() -> None:
            try:
                await self._ensure_playback()
            finally:
                self._ensure_playback_running = False
                if self._play_task is asyncio.current_task():
                    self._play_task = None

        task = asyncio.create_task(_run())
        self._play_task = task
        return task

    async def _resolve_source(self, track: Track) -> str:
        """Return a playable URL for `track`, freshly resolved when possible."""
        fallback = track.source_url or track.source_id or track.id or ""
        if self.resolver is None:
            return fallback
        try:
            fresh = await self.resolver(track)
            return fresh or fallback
        except Exception:
            logger.debug("track re-resolution failed chat=%s track=%s", self.chat_id, track.id, exc_info=True)
            return fallback

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
            # Resolve a fresh playable URL only now that the track is about to
            # play; URLs resolved at enqueue time may have expired (SoundCloud
            # signs and expires stream URLs within minutes).
            self._cancel_watch()
            self._cancel_stall_watch()

            try:
                result = await self.voice.play(
                    self.chat_id,
                    await self._resolve_source(next_track),
                    volume=self.volume,
                )
                self.state = PlaybackState.PLAYING
                self._retries = 0
                mode = result.get("mode") if isinstance(result, dict) else None

                # Guard against streams that start but never produce audio or
                # signal an end (e.g. an expired signed URL): if nothing ends
                # within the track's nominal duration, force-advance.
                if next_track.duration:
                    self._stall_task = asyncio.create_task(self._watch_for_stall(next_track.duration))

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
            except asyncio.CancelledError:
                # Player is being shut down; unwind without requeueing.
                self.current = None
                self.state = PlaybackState.IDLE
                raise
            except Exception as e:
                # A deliberate user-facing guard ("no active voice chat yet")
                # is not a playback fault: keep the likely-first track at the
                # front of the queue and pause instead of logging a scary
                # traceback, burning retries, or dropping the track. Once the
                # user starts a voice chat, the next /play/enqueue picks up
                # where we left off.
                if isinstance(e, RuntimeError) and str(e) == NO_ACTIVE_VOICE_CHAT_REASON:
                    logger.warning(
                        "playback paused chat=%s track=%s: %s",
                        self.chat_id,
                        next_track.id,
                        e,
                    )
                    await self.queue.add_next(next_track)
                    self.current = None
                    self.state = PlaybackState.IDLE
                    return
                logger.exception("playback failed chat=%s track=%s %s", self.chat_id, next_track.id, e)
                # Retry the failed track up to `max_retries` (e.g. until a voice
                # chat exists), then drop it so a bad track can't wedge the
                # queue forever. The lock is held by the surrounding block.
                self.current = None
                self.state = PlaybackState.IDLE
                self._retries += 1
                if self.max_retries > 0 and self._retries > self.max_retries:
                    logger.warning(
                        "dropping track chat=%s track=%s after %s failed attempts",
                        self.chat_id,
                        next_track.id,
                        self._retries,
                    )
                    return
                await self.queue.add_next(next_track)

    async def _watch_for_stall(self, duration: int) -> None:
        """Force-advance the queue if playback never produces/ends audio.

        A stream URL that expired before play (e.g. SoundCloud) will start
        "playing" silently and never emit a stream-end event, wedging the
        queue. After `duration + stall_margin` without an end event, tear the
        stream down and move on to the next track.
        """
        try:
            await asyncio.sleep(int(duration) + self.stall_margin)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if self.state != PlaybackState.PLAYING or self._stall_task is None:
                return
            logger.warning(
                "playback stalled chat=%s track=%s (no stream end within %ss); advancing queue",
                self.chat_id,
                self.current.id if self.current else "?",
                int(duration) + self.stall_margin,
            )
            if self._watch_task is not None and not self._watch_task.done():
                self._watch_task.cancel()
            self._watch_task = None
            self._stall_task = None
            self.current = None
            self.state = PlaybackState.IDLE
            try:
                await self.voice.stop_playback(self.chat_id)
            except Exception:
                logger.debug("stall recovery stop failed chat=%s", self.chat_id, exc_info=True)
            self._spawn_playback()

    async def skip(self) -> None:
        async with self._lock:
            self._cancel_watch()
            self._cancel_stall_watch()
            await self.voice.stop_playback(self.chat_id)
            self.state = PlaybackState.IDLE
            self.current = None
            # start next
            self._spawn_playback()

    async def _handle_track_end(self, engine) -> None:
        try:
            await engine.wait_finished()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("error while waiting for engine to finish")

        # cleanup and start next track
        async with self._lock:
            self._cancel_watch()
            self._cancel_stall_watch()
            self.current = None
            self.state = PlaybackState.IDLE
            self._spawn_playback()

    async def _on_pytgcalls_end(self, chat_id: int) -> None:
        # Called when pytgcalls reports the stream ended in this chat
        async with self._lock:
            if chat_id != self.chat_id:
                return
            self._cancel_watch()
            self._cancel_stall_watch()
            self.current = None
            self.state = PlaybackState.IDLE
            self._spawn_playback()

    async def clear(self) -> None:
        """Clear pending queued tracks without stopping the current playback."""
        async with self._lock:
            await self.queue.clear()

    async def stop(self) -> None:
        async with self._lock:
            self._cancel_watch()
            self._cancel_stall_watch()
            await self.voice.stop_playback(self.chat_id)
            self.state = PlaybackState.IDLE
            self.current = None
            await self.queue.clear()

    async def shutdown(self) -> None:
        """Cancel all background tasks and stop this chat's playback.

        Called on bot shutdown so orphaned watchers/playback tasks cannot keep
        the event loop busy (or crash during interpreter teardown) and so the
        voice stream is torn down cleanly.
        """
        # Cancel the watchers first (safe while holding the lock; they never
        # acquire it while being cancelled).
        self._cancel_watch()
        self._cancel_stall_watch()

        task = self._play_task
        self._play_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        # Tear down any active stream so audio stops on exit.
        try:
            await self.voice.stop_playback(self.chat_id)
        except Exception:
            logger.debug("shutdown stop_playback failed chat=%s", self.chat_id, exc_info=True)

        self.current = None
        self.state = PlaybackState.IDLE
        await self.queue.clear()


class PlayerManager:
    def __init__(
        self,
        voice_manager,
        session_factory=None,
        resolver=None,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        max_retries: int = DEFAULT_MAX_PLAY_RETRIES,
    ) -> None:
        self._players: dict[int, Player] = {}
        self._voice = voice_manager
        self._session_factory = session_factory
        self._resolver = resolver
        self._max_queue_size = max_queue_size
        self._max_retries = max_retries
        self._lock = asyncio.Lock()

    async def get_player(self, chat_id: int) -> Player:
        async with self._lock:
            p = self._players.get(chat_id)
            if p is None:
                p = Player(
                    chat_id,
                    self._voice,
                    session_factory=self._session_factory,
                    resolver=self._resolver,
                    # QUEUE_MAX_SIZE=0 means "no cap"
                    max_queue_size=self._max_queue_size if self._max_queue_size > 0 else None,
                    max_retries=self._max_retries,
                )
                self._players[chat_id] = p
            return p

    async def shutdown(self) -> None:
        """Shut down every known player (cancels tasks, clears queues)."""
        async with self._lock:
            players = list(self._players.values())
            self._players.clear()
        for player in players:
            try:
                await player.shutdown()
            except Exception:
                logger.exception("error shutting down player chat=%s", player.chat_id)
