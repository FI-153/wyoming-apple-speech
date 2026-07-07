"""Wyoming event handler for Apple STT."""

import argparse
import asyncio
import json
import logging
from typing import Any, Optional

from wyoming.asr import (
    Transcribe,
    Transcript,
    TranscriptChunk,
    TranscriptStart,
    TranscriptStop,
)
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStop
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler

from .stt import SttService, SttSession, SttWorker, SttWorkerError

_LOGGER = logging.getLogger(__name__)


class AppleSTTEventHandler(AsyncEventHandler):
    """Wyoming STT event handler — one instance per client connection.

    Handles the Wyoming protocol event flow: Describe, Transcribe,
    AudioChunk, AudioStop. Delegates actual transcription to the
    apple-stt Swift CLI via subprocess.
    """

    def __init__(
        self,
        wyoming_info: Info,
        cli_args: argparse.Namespace,
        transcription_lock: asyncio.Lock,
        *args: Any,
        stt_service: Optional[SttService] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._wyoming_info_event = wyoming_info.event()
        self._cli_args = cli_args
        self._lock = transcription_lock
        self._language: Optional[str] = None
        self._audio_bytes = bytearray()
        self._buffer_full_warned = False
        self._audio_converter = AudioChunkConverter(
            rate=16000, width=2, channels=1
        )
        self._max_audio_bytes = cli_args.max_audio_seconds * 16000 * 2  # 16kHz * 16-bit

        self._stt = stt_service
        self._stt_worker: Optional[SttWorker] = None
        self._stt_session: Optional[SttSession] = None
        self._stt_partial_task: Optional[asyncio.Task] = None
        self._stt_session_failed = False
        self._transcript_started = False

    async def handle_event(self, event: Event) -> bool:
        """Handle a Wyoming protocol event.

        Returns True to keep the connection open, False to disconnect.
        """
        if Describe.is_type(event.type):
            await self.write_event(self._wyoming_info_event)
            return True

        if Transcribe.is_type(event.type):
            transcribe = Transcribe.from_event(event)
            self._language = transcribe.language
            self._audio_bytes.clear()
            self._buffer_full_warned = False
            self._stt_session_failed = False
            self._transcript_started = False
            return True

        if AudioChunk.is_type(event.type):
            chunk = AudioChunk.from_event(event)
            chunk = self._audio_converter.convert(chunk)
            if len(self._audio_bytes) + len(chunk.audio) <= self._max_audio_bytes:
                self._audio_bytes.extend(chunk.audio)
            elif not self._buffer_full_warned:
                _LOGGER.warning("Max audio buffer reached, dropping audio")
                self._buffer_full_warned = True
            await self._stream_audio_to_worker(chunk.audio)
            return True

        if AudioStop.is_type(event.type):
            text = await self._finish_streaming_transcription()
            if text is None:
                text = await self._transcribe()
            if self._stt is not None and not self._transcript_started:
                await self.write_event(
                    TranscriptStart(language=self._language).event()
                )
                self._transcript_started = True
            await self.write_event(Transcript(text=text).event())
            if self._stt is not None:
                await self.write_event(TranscriptStop().event())
            self._audio_bytes.clear()
            self._buffer_full_warned = False
            self._language = None
            self._transcript_started = False
            return False

        return True

    async def disconnect(self) -> None:
        """Dispose of a held worker when the client drops mid-request.

        A worker abandoned mid-transcription has unread frames in its pipe
        and cannot be reused, so it is stopped rather than released.
        """
        if self._stt_session is not None or self._stt_worker is not None:
            await self._teardown_stt_session()

    async def _stream_audio_to_worker(self, audio: bytes) -> None:
        """Feed one audio chunk to the streaming session, opening it lazily.

        The first chunk of an utterance acquires a pre-warmed worker and opens
        a transcription session on it. Any failure marks the session as failed
        for the rest of the utterance; the buffered one-shot path then answers
        at AudioStop, so streaming problems never lose the utterance.
        """
        if self._stt is None or self._stt_session_failed:
            return

        if self._stt_session is None:
            try:
                self._stt_worker = await self._stt.pool.acquire()
                self._stt_session = await self._stt_worker.transcribe(
                    language=self._language or self._cli_args.language
                )
            except SttWorkerError as exc:
                _LOGGER.warning(
                    "Streaming STT unavailable, using buffered fallback: %s", exc
                )
                await self._teardown_stt_session()
                return
            self._stt_partial_task = asyncio.create_task(
                self._forward_partials(self._stt_session)
            )
            _LOGGER.debug("Streaming transcription session opened")

        try:
            await self._stt_session.send_audio(audio)
        except SttWorkerError as exc:
            _LOGGER.warning(
                "Streaming STT failed mid-utterance, using buffered fallback: %s", exc
            )
            await self._teardown_stt_session()

    async def _forward_partials(self, session: SttSession) -> None:
        """Forward the session's partial transcripts to the client as they arrive."""
        async for text in session.partials():
            if not self._transcript_started:
                await self.write_event(
                    TranscriptStart(language=self._language).event()
                )
                self._transcript_started = True
            await self.write_event(TranscriptChunk(text=text).event())

    async def _finish_streaming_transcription(self) -> Optional[str]:
        """Finalize the streaming session and return its transcript.

        Returns:
            The final text, or None when no session is open or it failed —
            the caller then falls back to the buffered one-shot path.
        """
        if self._stt_session is None:
            return None
        assert self._stt is not None

        session, self._stt_session = self._stt_session, None
        try:
            text: Optional[str] = await session.finish(timeout=self._stt.timeout)
        except SttWorkerError as exc:
            _LOGGER.warning(
                "Streaming transcription failed, using buffered fallback: %s", exc
            )
            text = None

        if self._stt_partial_task is not None:
            task, self._stt_partial_task = self._stt_partial_task, None
            if text is None:
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if self._stt_worker is not None:
            worker, self._stt_worker = self._stt_worker, None
            await self._stt.pool.release(worker)
        return text

    async def _teardown_stt_session(self) -> None:
        """Dispose of a failed session's worker and disable streaming until
        the next utterance."""
        self._stt_session_failed = True
        self._stt_session = None
        if self._stt_partial_task is not None:
            task, self._stt_partial_task = self._stt_partial_task, None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._stt_worker is not None:
            worker, self._stt_worker = self._stt_worker, None
            await worker.stop()

    async def _transcribe(self) -> str:
        """Run the apple-stt subprocess and return transcribed text."""
        language = self._language or self._cli_args.language
        audio_data = bytes(self._audio_bytes)

        if not audio_data:
            return ""

        async with self._lock:
            try:
                process = await asyncio.create_subprocess_exec(
                    self._cli_args.apple_stt_bin,
                    "--language", language,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as exc:
                _LOGGER.error(
                    "Failed to launch apple-stt binary '%s': %s",
                    self._cli_args.apple_stt_bin,
                    exc,
                )
                return ""
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(input=audio_data),
                    timeout=self._cli_args.timeout,
                )
            except asyncio.TimeoutError:
                try:
                    process.kill()
                    await process.wait()
                except ProcessLookupError:
                    pass
                _LOGGER.error("Transcription timed out after %ds", self._cli_args.timeout)
                return ""

        stderr_text = stderr.decode().strip()

        if process.returncode != 0:
            _LOGGER.error(
                "apple-stt failed (exit %d): %s",
                process.returncode,
                stderr_text or "(no stderr output)",
            )
            return ""

        if stderr_text:
            for line in stderr_text.splitlines():
                _LOGGER.debug("%s", line)

        try:
            result: dict[str, str] = json.loads(stdout.decode())
            return result.get("text", "")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            _LOGGER.error("Failed to parse apple-stt output: %s", exc)
            return ""
