"""Wyoming event handler for Apple STT and Siri TTS."""

import argparse
import asyncio
import json
import logging
from typing import Any, Optional

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler
from wyoming.tts import (
    Synthesize,
    SynthesizeChunk,
    SynthesizeStart,
    SynthesizeStop,
    SynthesizeStopped,
    SynthesizeVoice,
)

from .tts import (
    CHANNELS,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
    SentenceSplitter,
    SiriVoice,
    TtsService,
    TtsWorker,
    TtsWorkerError,
    resolve_voice,
)

_LOGGER = logging.getLogger(__name__)


class AppleSTTEventHandler(AsyncEventHandler):
    """Wyoming STT + TTS event handler — one instance per client connection.

    Handles the Wyoming protocol event flow: Describe, Transcribe,
    AudioChunk, AudioStop for STT (delegated to the apple-stt Swift CLI via
    subprocess) and Synthesize plus the synthesize-start/chunk/stop streaming
    events for TTS (delegated to a pre-warmed apple-tts worker from the pool).
    """

    def __init__(
        self,
        wyoming_info: Info,
        cli_args: argparse.Namespace,
        transcription_lock: asyncio.Lock,
        *args: Any,
        tts_service: Optional[TtsService] = None,
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

        self._tts = tts_service
        self._tts_worker: Optional[TtsWorker] = None
        self._tts_voice: Optional[SiriVoice] = None
        self._tts_splitter: Optional[SentenceSplitter] = None
        self._tts_audio_started = False
        self._tts_streaming = False
        self._tts_frames_sent_in_call = False

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
            return True

        if AudioChunk.is_type(event.type):
            chunk = AudioChunk.from_event(event)
            chunk = self._audio_converter.convert(chunk)
            if len(self._audio_bytes) + len(chunk.audio) <= self._max_audio_bytes:
                self._audio_bytes.extend(chunk.audio)
            elif not self._buffer_full_warned:
                _LOGGER.warning("Max audio buffer reached, dropping audio")
                self._buffer_full_warned = True
            return True

        if AudioStop.is_type(event.type):
            text = await self._transcribe()
            await self.write_event(Transcript(text=text).event())
            self._audio_bytes.clear()
            self._buffer_full_warned = False
            self._language = None
            return False

        if self._tts is not None:
            if Synthesize.is_type(event.type):
                if self._tts_streaming:
                    # Streaming clients repeat the full text in a legacy
                    # synthesize event for backwards compatibility; ignore it.
                    return True
                await self._synthesize_complete(Synthesize.from_event(event))
                return False

            if SynthesizeStart.is_type(event.type):
                start = SynthesizeStart.from_event(event)
                self._tts_streaming = True
                self._tts_splitter = SentenceSplitter()
                self._tts_audio_started = False
                self._tts_voice = self._resolve_requested_voice(start.voice)
                self._tts_worker = await self._tts.pool.acquire()
                _LOGGER.debug(
                    "Streaming synthesis started (voice=%s)", self._tts_voice.voice_id
                )
                return True

            if SynthesizeChunk.is_type(event.type):
                if not self._tts_streaming or self._tts_splitter is None:
                    return True
                text_chunk = SynthesizeChunk.from_event(event)
                for sentence in self._tts_splitter.add(text_chunk.text):
                    await self._speak_text(sentence)
                return True

            if SynthesizeStop.is_type(event.type):
                if self._tts_splitter is not None:
                    remainder = self._tts_splitter.finish()
                    if remainder:
                        await self._speak_text(remainder)
                await self._finish_audio()
                await self.write_event(SynthesizeStopped().event())
                await self._release_tts_worker()
                self._tts_streaming = False
                self._tts_splitter = None
                _LOGGER.debug("Streaming synthesis finished")
                return False

        return True

    async def disconnect(self) -> None:
        """Dispose of a held TTS worker when the client drops mid-request.

        A worker abandoned mid-synthesis has unread frames in its pipe and
        cannot be reused, so it is stopped rather than released.
        """
        if self._tts_worker is not None:
            worker, self._tts_worker = self._tts_worker, None
            await worker.stop()

    def _resolve_requested_voice(self, requested: Optional[SynthesizeVoice]) -> SiriVoice:
        """Map a client's voice request onto an installed system voice.

        Falls back to the server's default voice when nothing was requested
        or the request matches no voice.
        """
        assert self._tts is not None
        name = requested.name if requested else None
        language = requested.language if requested else None
        resolved = resolve_voice(self._tts.voices, name=name, language=language)
        if resolved is None:
            if name or language:
                _LOGGER.warning(
                    "No system voice matches name=%s language=%s, using default %s",
                    name,
                    language,
                    self._tts.default_voice.voice_id,
                )
            return self._tts.default_voice
        return resolved

    async def _synthesize_complete(self, synthesize: Synthesize) -> None:
        """Serve a non-streaming synthesize request end to end."""
        assert self._tts is not None
        self._tts_voice = self._resolve_requested_voice(synthesize.voice)
        self._tts_audio_started = False
        text = " ".join(synthesize.text.splitlines()).strip()
        _LOGGER.debug(
            "Synthesizing %d chars (voice=%s)", len(text), self._tts_voice.voice_id
        )

        self._tts_worker = await self._tts.pool.acquire()
        try:
            if text:
                await self._speak_text(text)
            await self._finish_audio()
        finally:
            await self._release_tts_worker()

    async def _speak_text(self, text: str) -> None:
        """Synthesize one piece of text and stream its audio to the client.

        When the worker fails before producing any audio, it is swapped for a
        fresh one and the text retried once; partial-output failures are only
        logged, since retrying would duplicate the already-sent audio.
        """
        _LOGGER.debug("Speaking %d chars", len(text))
        try:
            await self._stream_frames(text)
        except TtsWorkerError as exc:
            _LOGGER.error("Synthesis failed for %d chars: %s", len(text), exc)
            if self._tts_frames_sent_in_call:
                # Retrying now would duplicate the audio already delivered.
                return
            await self._replace_tts_worker()
            try:
                await self._stream_frames(text)
            except TtsWorkerError as retry_exc:
                _LOGGER.error("Retry failed, skipping text: %s", retry_exc)

    async def _stream_frames(self, text: str) -> None:
        """Run one worker synthesis, forwarding audio frames to the client.

        Sets `_tts_frames_sent_in_call` as soon as any frame goes out, so a
        caller catching a mid-stream failure can tell partial output from none.
        """
        assert self._tts is not None and self._tts_worker is not None
        assert self._tts_voice is not None
        self._tts_frames_sent_in_call = False
        async for frame in self._tts_worker.synthesize(
            text=text,
            voice_path=self._tts_voice.path,
            rate=self._tts.rate,
            pitch=self._tts.pitch,
            volume=self._tts.volume,
            timeout=self._tts.timeout,
        ):
            if not self._tts_audio_started:
                await self.write_event(
                    AudioStart(
                        rate=frame.rate, width=frame.width, channels=frame.channels
                    ).event()
                )
                self._tts_audio_started = True
            await self.write_event(
                AudioChunk(
                    audio=frame.audio,
                    rate=frame.rate,
                    width=frame.width,
                    channels=frame.channels,
                ).event()
            )
            self._tts_frames_sent_in_call = True

    async def _finish_audio(self) -> None:
        """Close the audio envelope, opening an empty one if nothing was sent."""
        if not self._tts_audio_started:
            await self.write_event(
                AudioStart(rate=SAMPLE_RATE, width=SAMPLE_WIDTH, channels=CHANNELS).event()
            )
            self._tts_audio_started = True
        await self.write_event(AudioStop().event())

    async def _release_tts_worker(self) -> None:
        """Hand the held worker back to the pool."""
        assert self._tts is not None
        if self._tts_worker is not None:
            worker, self._tts_worker = self._tts_worker, None
            await self._tts.pool.release(worker)

    async def _replace_tts_worker(self) -> None:
        """Swap a failed worker for a fresh one from the pool."""
        assert self._tts is not None
        if self._tts_worker is not None:
            await self._tts_worker.stop()
            self._tts_worker = None
        self._tts_worker = await self._tts.pool.acquire()

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
