"""Streaming STT support: the apple-stt worker protocol and the warm worker pool."""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Optional

_LOGGER = logging.getLogger(__name__)

# Pipe buffer limit for worker stdout; header lines are small but partials
# can carry long transcripts.
_STREAM_LIMIT = 1024 * 1024


class SttWorkerError(Exception):
    """Raised when a worker fails to start, dies, or reports a session error."""


class SttSession:
    """One live transcription on a worker: audio in, partials and a final out.

    Created by SttWorker.transcribe(). A background reader task dispatches
    the worker's frames: "partial" texts go to the partials() iterator and
    the "final" (or "error") frame resolves finish().
    """

    def __init__(self, worker: "SttWorker") -> None:
        self._worker = worker
        self._partials: asyncio.Queue[Optional[str]] = asyncio.Queue()
        self._final: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._reader_task = asyncio.create_task(self._read_frames())

    async def send_audio(self, pcm: bytes) -> None:
        """Feed one chunk of raw PCM audio (16 kHz, 16-bit, mono) to the worker.

        Args:
            pcm: Raw audio bytes; empty chunks are ignored.

        Raises:
            SttWorkerError: When the worker is gone or the pipe write fails.
        """
        if not pcm:
            return
        await self._worker._write_frame({"type": "audio", "length": len(pcm)}, pcm)

    async def finish(self, timeout: float = 30) -> str:
        """Signal end of audio and wait for the final transcript.

        Args:
            timeout: Max seconds to wait for the worker's final frame.

        Returns:
            The final transcript text.

        Raises:
            SttWorkerError: When the worker reports an error, dies, or the
                final frame does not arrive in time (the worker is marked
                broken in the latter cases, so the pool disposes of it).
        """
        try:
            await self._worker._write_frame({"type": "stop"})
        except SttWorkerError:
            pass  # The reader task will surface the underlying failure.

        try:
            return await asyncio.wait_for(asyncio.shield(self._final), timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._worker._broken = True
            self._reader_task.cancel()
            self._close_partials()
            raise SttWorkerError(f"transcription timed out after {timeout}s") from exc

    async def partials(self) -> AsyncIterator[str]:
        """Yield partial transcripts as the worker produces them.

        The iterator ends when the session's final (or error) frame arrives.
        """
        while (text := await self._partials.get()) is not None:
            yield text

    async def _read_frames(self) -> None:
        """Dispatch worker frames until the session completes or fails."""
        try:
            while True:
                header = await self._worker._read_header()
                frame_type = header.get("type")
                if frame_type == "partial":
                    self._partials.put_nowait(str(header.get("text", "")))
                elif frame_type == "final":
                    self._resolve_final(text=str(header.get("text", "")))
                    return
                elif frame_type == "error":
                    self._resolve_final(
                        error=SttWorkerError(
                            header.get("message", "unknown transcription error")
                        )
                    )
                    return
                else:
                    self._worker._broken = True
                    self._resolve_final(
                        error=SttWorkerError(f"unexpected worker frame: {header}")
                    )
                    return
        except SttWorkerError as exc:
            self._worker._broken = True
            self._resolve_final(error=exc)
        except asyncio.CancelledError:
            pass

    def _resolve_final(
        self, text: Optional[str] = None, error: Optional[SttWorkerError] = None
    ) -> None:
        """Complete the final future once and end the partials iterator."""
        if not self._final.done():
            if error is not None:
                self._final.set_exception(error)
            else:
                self._final.set_result(text or "")
        self._close_partials()

    def _close_partials(self) -> None:
        """Signal the end of the partials iterator."""
        self._partials.put_nowait(None)


class SttWorker:
    """One `apple-stt --worker` subprocess with a preloaded recognition model.

    The worker is started once, keeps its model warm for its lifetime, and
    serves one transcription session at a time over its stdin/stdout pipe.
    """

    def __init__(
        self,
        bin_path: str,
        language: str = "en",
        extra_args: Optional[list[str]] = None,
    ) -> None:
        self._bin_path = bin_path
        self._language = language
        self._extra_args = extra_args or []
        self._process: Optional[asyncio.subprocess.Process] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._broken = False

    @property
    def is_alive(self) -> bool:
        """Whether the worker process is running and its protocol is in sync."""
        return (
            not self._broken
            and self._process is not None
            and self._process.returncode is None
        )

    async def start(self, timeout: float = 60) -> None:
        """Launch the worker and wait until its recognition model is warm.

        Args:
            timeout: Max seconds to wait for the worker's ready signal.

        Raises:
            SttWorkerError: When the worker cannot be launched or never
                becomes ready.
        """
        command = [self._bin_path, "--worker", "--language", self._language]
        command += self._extra_args

        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=_STREAM_LIMIT,
            )
        except OSError as exc:
            raise SttWorkerError(f"failed to launch {self._bin_path}: {exc}") from exc

        self._stderr_task = asyncio.create_task(self._drain_stderr())

        try:
            header = await asyncio.wait_for(self._read_header(), timeout=timeout)
        except (asyncio.TimeoutError, SttWorkerError) as exc:
            await self.stop()
            raise SttWorkerError(f"worker did not become ready: {exc}") from exc

        if header.get("type") != "ready":
            await self.stop()
            raise SttWorkerError(f"unexpected worker greeting: {header}")

        _LOGGER.debug("STT worker ready (pid %s)", self._process.pid)

    async def transcribe(self, language: Optional[str] = None) -> SttSession:
        """Open a transcription session on this worker.

        Args:
            language: BCP-47 language code; defaults to the worker's language.

        Returns:
            The live session; feed it audio and call finish().

        Raises:
            SttWorkerError: When the worker is not running.
        """
        if not self.is_alive:
            raise SttWorkerError("worker is not running")
        await self._write_frame(
            {"type": "transcribe", "language": language or self._language}
        )
        return SttSession(self)

    async def stop(self) -> None:
        """Terminate the worker process and clean up."""
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None

        if self._process is None:
            return
        process, self._process = self._process, None
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    async def _write_frame(self, header: dict, payload: bytes = b"") -> None:
        """Write one header line plus optional binary payload to the worker."""
        if not self.is_alive:
            raise SttWorkerError("worker is not running")
        assert self._process is not None and self._process.stdin is not None
        try:
            self._process.stdin.write(json.dumps(header).encode() + b"\n" + payload)
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            self._broken = True
            raise SttWorkerError(f"worker pipe closed: {exc}") from exc

    async def _read_header(self) -> dict:
        """Read one JSON header line from the worker's stdout."""
        assert self._process is not None and self._process.stdout is not None
        line = await self._process.stdout.readline()
        if not line:
            raise SttWorkerError("worker closed its output (process died?)")
        try:
            header: dict = json.loads(line)
            return header
        except json.JSONDecodeError as exc:
            raise SttWorkerError(f"invalid worker frame: {line[:200]!r}") from exc

    async def _drain_stderr(self) -> None:
        """Forward the worker's stderr lines to the debug log."""
        assert self._process is not None and self._process.stderr is not None
        try:
            while line := await self._process.stderr.readline():
                _LOGGER.debug("%s", line.decode(errors="replace").rstrip())
        except (asyncio.CancelledError, ValueError):
            pass


class SttWorkerPool:
    """Keeps pre-warmed apple-stt workers so recognition starts with zero delay.

    The pool maintains idle_target idle workers with warm recognition models.
    acquire() hands out a warm worker and immediately begins spawning a
    replacement in the background, so a concurrent second request never
    waits for model initialization.
    """

    def __init__(
        self,
        bin_path: str,
        language: str = "en",
        idle_target: int = 1,
    ) -> None:
        self._bin_path = bin_path
        self._language = language
        self._idle_target = max(1, idle_target)
        self._idle: list[SttWorker] = []
        self._spawning = 0
        self._spawn_tasks: set[asyncio.Task] = set()
        self._closed = False

    async def start(self) -> None:
        """Pre-warm the initial idle workers, waiting until they are ready."""
        workers = await asyncio.gather(
            *(self._spawn_worker() for _ in range(self._idle_target)),
            return_exceptions=True,
        )
        for worker in workers:
            if isinstance(worker, BaseException):
                _LOGGER.warning("Failed to pre-warm STT worker: %s", worker)
            else:
                self._idle.append(worker)

    async def acquire(self) -> SttWorker:
        """Take a warm worker, triggering a background replacement spawn.

        Returns:
            A ready worker. The caller must hand it back via release().

        Raises:
            SttWorkerError: When no worker is available and a fresh spawn fails.
        """
        worker: Optional[SttWorker] = None
        while self._idle and worker is None:
            candidate = self._idle.pop()
            if candidate.is_alive:
                worker = candidate
            else:
                await candidate.stop()

        self._replenish()

        if worker is None:
            worker = await self._spawn_worker()
        return worker

    async def release(self, worker: SttWorker) -> None:
        """Return a worker to the pool, or dispose of it if surplus or dead."""
        if self._closed or not worker.is_alive or len(self._idle) >= self._idle_target:
            await worker.stop()
        else:
            self._idle.append(worker)

    async def stop(self) -> None:
        """Shut down all idle workers and cancel pending spawns."""
        self._closed = True
        for task in self._spawn_tasks:
            task.cancel()
        self._spawn_tasks.clear()
        idle, self._idle = self._idle, []
        await asyncio.gather(*(worker.stop() for worker in idle))

    def _replenish(self) -> None:
        """Spawn background workers until the idle target is covered."""
        if self._closed:
            return
        while len(self._idle) + self._spawning < self._idle_target:
            self._spawning += 1
            task = asyncio.create_task(self._spawn_into_pool())
            self._spawn_tasks.add(task)
            task.add_done_callback(self._spawn_tasks.discard)

    async def _spawn_into_pool(self) -> None:
        """Spawn one worker and park it in the idle pool."""
        try:
            worker = await self._spawn_worker()
        except SttWorkerError as exc:
            _LOGGER.warning("Failed to spawn replacement STT worker: %s", exc)
            return
        finally:
            self._spawning -= 1

        if self._closed or len(self._idle) >= self._idle_target:
            await worker.stop()
        else:
            self._idle.append(worker)

    async def _spawn_worker(self) -> SttWorker:
        """Create and start one worker for the pool's language."""
        worker = SttWorker(self._bin_path, self._language)
        await worker.start()
        return worker


@dataclass
class SttService:
    """Everything the event handler needs to serve streaming STT requests."""

    pool: SttWorkerPool
    timeout: float = 30.0
