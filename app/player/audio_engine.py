import asyncio
import logging
import signal
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AudioEngineConfig:
    ffmpeg_path: str = "ffmpeg"
    # number of seconds to wait for processes to exit gracefully
    shutdown_timeout: float = 5.0
    # retry attempts for transient failures
    retries: int = 1


class AudioEngine:
    """Simple FFmpeg subprocess manager.

    Responsibilities:
    - Start FFmpeg with a safe argument list
    - Monitor process exit code
    - Provide pause/resume/stop operations
    - Ensure subprocess cleanup

    Note: this class does not implement the actual audio feeding into Telegram;
    it provides a managed FFmpeg process that produces raw PCM to stdout which
    later can be piped into voice clients.
    """

    def __init__(self, config: AudioEngineConfig | None = None) -> None:
        self.config = config or AudioEngineConfig()
        self._process: asyncio.subprocess.Process | None = None
        self._monitor_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._paused = False
        self._exit_future: asyncio.Future | None = None

    async def play(self, input_source: str, *, extra_args: list[str] | None = None) -> None:
        """Start FFmpeg to read `input_source` and output raw s16le pcm to stdout.

        `input_source` can be a file path or URL understood by FFmpeg.
        """
        async with self._lock:
            if self._process is not None:
                raise RuntimeError("FFmpeg already running")

            args = [
                self.config.ffmpeg_path,
                "-re",
                "-i",
                input_source,
                # output raw PCM s16le, 48kHz, stereo
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-",
            ]

            if extra_args:
                # Insert extra args before the final '-' output
                args = args[:-1] + extra_args + [args[-1]]

            logger.info("starting ffmpeg: %s", args)

            try:
                self._process = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except Exception as e:
                logger.exception("failed to start ffmpeg: %s", e)
                self._process = None
                raise

            # Start background task to monitor process stderr and exit
            # reset exit future
            loop = asyncio.get_running_loop()
            self._exit_future = loop.create_future()
            self._monitor_task = asyncio.create_task(self._monitor_process())

    async def _monitor_process(self) -> None:
        proc = self._process
        if proc is None:
            return

        # Read stderr lines for logging
        assert proc.stderr is not None
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                logger.debug("ffmpeg: %s", line.decode(errors="ignore").rstrip())
        except Exception:
            logger.exception("error while reading ffmpeg stderr")

        returncode = await proc.wait()
        logger.info("ffmpeg exited with code %s", returncode)

        # Cleanup process reference
        async with self._lock:
            if self._process is proc:
                self._process = None
        # signal any waiter that the process exited
        if self._exit_future and not self._exit_future.done():
            self._exit_future.set_result(returncode)

    async def wait_finished(self) -> int | None:
        """Await FFmpeg process exit and return its exit code. Returns None if no process."""
        if self._exit_future is None:
            return None
        return await self._exit_future

    async def pause(self) -> None:
        async with self._lock:
            if self._process is None:
                raise RuntimeError("No ffmpeg process to pause")
            if self._paused:
                return
            # Sending SIGSTOP/CONT is not supported on Windows
            if not hasattr(signal, "SIGSTOP"):
                raise NotImplementedError("pause/resume requires a POSIX platform")
            try:
                self._process.send_signal(signal.SIGSTOP)
                self._paused = True
                logger.info("ffmpeg paused")
            except Exception:
                logger.exception("failed to pause ffmpeg")
                raise

    async def resume(self) -> None:
        async with self._lock:
            if self._process is None:
                raise RuntimeError("No ffmpeg process to resume")
            if not self._paused:
                return
            if not hasattr(signal, "SIGCONT"):
                raise NotImplementedError("pause/resume requires a POSIX platform")
            try:
                self._process.send_signal(signal.SIGCONT)
                self._paused = False
                logger.info("ffmpeg resumed")
            except Exception:
                logger.exception("failed to resume ffmpeg")
                raise

    async def stop(self) -> None:
        async with self._lock:
            if self._process is None:
                return

            proc = self._process
            logger.info("stopping ffmpeg (pid=%s)", getattr(proc, "pid", None))
            try:
                proc.terminate()
            except ProcessLookupError:
                logger.debug("process already terminated")

            try:
                await asyncio.wait_for(proc.wait(), timeout=self.config.shutdown_timeout)
            except TimeoutError:
                logger.warning("ffmpeg did not exit in time; killing")
                try:
                    proc.kill()
                except Exception:
                    logger.exception("failed to kill ffmpeg")

            # stop the stderr monitor task; it would otherwise block forever
            if self._monitor_task is not None and not self._monitor_task.done():
                self._monitor_task.cancel()
            self._monitor_task = None

            # Unblock any waiter on wait_finished() so it doesn't hang forever.
            rc = getattr(proc, "returncode", None)
            if self._exit_future is not None and not self._exit_future.done():
                self._exit_future.set_result(rc if rc is not None else -1)

            # clear reference
            self._process = None
            self._paused = False

    async def close(self) -> None:
        await self.stop()
