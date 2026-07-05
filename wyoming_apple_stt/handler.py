"""Wyoming event handler for Apple STT."""

import argparse
import asyncio
import json
import logging
from typing import Any, Optional

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStop
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler

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

        return True

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
