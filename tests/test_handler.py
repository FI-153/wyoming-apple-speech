"""Tests for the Wyoming Apple STT event handler."""

import argparse
import asyncio
import json
import logging
from unittest.mock import AsyncMock, patch

import pytest
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStop
from wyoming.info import Describe, Info

from wyoming_apple_stt.handler import AppleSTTEventHandler


@pytest.fixture
def transcription_lock() -> asyncio.Lock:
    """Create an asyncio lock for transcription serialization."""
    return asyncio.Lock()


@pytest.fixture
def handler(wyoming_info, cli_args, transcription_lock):
    """Create handler with mocked reader/writer."""
    reader = AsyncMock()
    writer = AsyncMock()
    handler = AppleSTTEventHandler(
        wyoming_info, cli_args, transcription_lock, reader, writer
    )
    handler.write_event = AsyncMock()
    return handler


async def test_describe_returns_info(handler, wyoming_info):
    """Describe event should return Info with ASR program details."""
    event = Describe().event()
    result = await handler.handle_event(event)

    assert result is True
    handler.write_event.assert_called_once()
    written_event = handler.write_event.call_args[0][0]
    info = Info.from_event(written_event)
    assert len(info.asr) == 1
    assert info.asr[0].name == "apple-stt"
    assert info.asr[0].models[0].languages == ["en"]


async def test_transcription_flow(handler):
    """Full flow: Transcribe → AudioChunk → AudioStop → Transcript."""
    # 1. Transcribe event
    transcribe_event = Transcribe(language="en").event()
    result = await handler.handle_event(transcribe_event)
    assert result is True

    # 2. AudioChunk event (1 second of silence at 16kHz/16-bit/mono = 32000 bytes)
    pcm_data = b"\x00\x00" * 16000
    chunk = AudioChunk(rate=16000, width=2, channels=1, audio=pcm_data)
    result = await handler.handle_event(chunk.event())
    assert result is True

    # 3. AudioStop — mock the subprocess
    mock_process = AsyncMock()
    mock_process.communicate.return_value = (
        json.dumps({"text": "turn on the lights"}).encode(),
        b"",
    )
    mock_process.returncode = 0

    with patch(
        "wyoming_apple_stt.handler.asyncio.create_subprocess_exec",
        return_value=mock_process,
    ) as mock_exec:
        result = await handler.handle_event(AudioStop().event())

    assert result is False  # connection closes after transcript
    handler.write_event.assert_called_once()

    written_event = handler.write_event.call_args[0][0]
    transcript = Transcript.from_event(written_event)
    assert transcript.text == "turn on the lights"

    # Verify subprocess was called correctly
    mock_exec.assert_called_once()
    call_args = mock_exec.call_args
    assert call_args[0][0] == "/usr/local/bin/apple-stt"
    assert "--language" in call_args[0]
    assert "en" in call_args[0]


async def test_empty_audio_returns_empty_transcript(handler):
    """AudioStop with no audio chunks should return empty transcript."""
    await handler.handle_event(Transcribe(language="en").event())
    result = await handler.handle_event(AudioStop().event())

    assert result is False
    handler.write_event.assert_called_once()
    written_event = handler.write_event.call_args[0][0]
    transcript = Transcript.from_event(written_event)
    assert transcript.text == ""


async def test_max_audio_buffer_enforced(handler):
    """Audio beyond max_audio_seconds should be dropped."""
    await handler.handle_event(Transcribe(language="en").event())

    # cli_args.max_audio_seconds = 60, so max bytes = 60 * 16000 * 2 = 1,920,000
    # Send 61 seconds of audio in 1-second chunks
    one_second = b"\x00\x00" * 16000  # 32,000 bytes
    for _ in range(61):
        chunk = AudioChunk(rate=16000, width=2, channels=1, audio=one_second)
        await handler.handle_event(chunk.event())

    # Buffer should be capped at 60 seconds
    assert len(handler._audio_bytes) == 60 * 16000 * 2


async def test_subprocess_failure_returns_empty(handler):
    """If apple-stt exits non-zero, return empty transcript."""
    await handler.handle_event(Transcribe(language="en").event())
    pcm_data = b"\x00\x00" * 16000
    chunk = AudioChunk(rate=16000, width=2, channels=1, audio=pcm_data)
    await handler.handle_event(chunk.event())

    mock_process = AsyncMock()
    mock_process.communicate.return_value = (b"", b"Error: model not available\n")
    mock_process.returncode = 1

    with patch(
        "wyoming_apple_stt.handler.asyncio.create_subprocess_exec",
        return_value=mock_process,
    ):
        result = await handler.handle_event(AudioStop().event())

    assert result is False
    written_event = handler.write_event.call_args[0][0]
    transcript = Transcript.from_event(written_event)
    assert transcript.text == ""


async def test_subprocess_timeout_returns_empty(handler):
    """If apple-stt exceeds timeout, return empty transcript."""
    await handler.handle_event(Transcribe(language="en").event())
    pcm_data = b"\x00\x00" * 16000
    chunk = AudioChunk(rate=16000, width=2, channels=1, audio=pcm_data)
    await handler.handle_event(chunk.event())

    mock_process = AsyncMock()
    mock_process.communicate.side_effect = asyncio.TimeoutError()
    mock_process.kill = AsyncMock()

    with patch(
        "wyoming_apple_stt.handler.asyncio.create_subprocess_exec",
        return_value=mock_process,
    ):
        result = await handler.handle_event(AudioStop().event())

    assert result is False
    mock_process.kill.assert_called_once()
    written_event = handler.write_event.call_args[0][0]
    transcript = Transcript.from_event(written_event)
    assert transcript.text == ""


async def test_serialization_lock(handler, wyoming_info, cli_args, transcription_lock):
    """Concurrent transcriptions should be serialized by the lock."""
    # Create a second handler sharing the same lock
    reader2 = AsyncMock()
    writer2 = AsyncMock()
    handler2 = AppleSTTEventHandler(
        wyoming_info, cli_args, transcription_lock, reader2, writer2
    )
    handler2.write_event = AsyncMock()

    # Track the order of subprocess calls
    call_order = []

    async def slow_communicate(input=None):
        call_order.append("start")
        await asyncio.sleep(0.1)
        call_order.append("end")
        return (json.dumps({"text": "hello"}).encode(), b"")

    # Set up both handlers with audio
    for h in [handler, handler2]:
        await h.handle_event(Transcribe(language="en").event())
        chunk = AudioChunk(rate=16000, width=2, channels=1, audio=b"\x00\x00" * 100)
        await h.handle_event(chunk.event())

    mock_process = AsyncMock()
    mock_process.communicate = slow_communicate
    mock_process.returncode = 0

    with patch(
        "wyoming_apple_stt.handler.asyncio.create_subprocess_exec",
        return_value=mock_process,
    ):
        # Run both transcriptions concurrently
        await asyncio.gather(
            handler.handle_event(AudioStop().event()),
            handler2.handle_event(AudioStop().event()),
        )

    # With serialization: start, end, start, end (not start, start, end, end)
    assert call_order == ["start", "end", "start", "end"]


async def test_default_language_from_cli_args(wyoming_info, cli_args, transcription_lock):
    """When Transcribe has no language, use cli_args.language as default."""
    # Create handler with Italian default
    it_args = argparse.Namespace(
        apple_stt_bin="/usr/local/bin/apple-stt",
        timeout=30,
        max_audio_seconds=60,
        language="it",
    )
    reader = AsyncMock()
    writer = AsyncMock()
    it_handler = AppleSTTEventHandler(
        wyoming_info,
        it_args, transcription_lock, reader, writer,
    )
    it_handler.write_event = AsyncMock()

    # Transcribe with no language specified
    await it_handler.handle_event(Transcribe().event())
    chunk = AudioChunk(rate=16000, width=2, channels=1, audio=b"\x00\x00" * 100)
    await it_handler.handle_event(chunk.event())

    mock_process = AsyncMock()
    mock_process.communicate.return_value = (
        json.dumps({"text": "accendi le luci"}).encode(),
        b"",
    )
    mock_process.returncode = 0

    with patch(
        "wyoming_apple_stt.handler.asyncio.create_subprocess_exec",
        return_value=mock_process,
    ) as mock_exec:
        await it_handler.handle_event(AudioStop().event())

    call_args = mock_exec.call_args
    assert "--language" in call_args[0]
    assert "it" in call_args[0]


async def test_missing_binary_returns_empty_transcript(handler, caplog):
    """A missing/unexecutable apple-stt binary should not crash the handler."""
    await handler.handle_event(Transcribe(language="en").event())
    chunk = AudioChunk(rate=16000, width=2, channels=1, audio=b"\x00\x00" * 16000)
    await handler.handle_event(chunk.event())

    with patch(
        "wyoming_apple_stt.handler.asyncio.create_subprocess_exec",
        side_effect=FileNotFoundError("No such file or directory"),
    ):
        with caplog.at_level(logging.ERROR):
            result = await handler.handle_event(AudioStop().event())

    assert result is False
    handler.write_event.assert_called_once()
    written_event = handler.write_event.call_args[0][0]
    transcript = Transcript.from_event(written_event)
    assert transcript.text == ""
    assert any(r.levelno == logging.ERROR for r in caplog.records)


async def test_subprocess_failure_logs_stderr_at_error(handler, caplog):
    """Non-zero exit should surface the CLI's stderr at ERROR level."""
    await handler.handle_event(Transcribe(language="en").event())
    chunk = AudioChunk(rate=16000, width=2, channels=1, audio=b"\x00\x00" * 16000)
    await handler.handle_event(chunk.event())

    mock_process = AsyncMock()
    mock_process.communicate.return_value = (b"", b"Error: permission denied\n")
    mock_process.returncode = 1

    with patch(
        "wyoming_apple_stt.handler.asyncio.create_subprocess_exec",
        return_value=mock_process,
    ):
        with caplog.at_level(logging.ERROR):
            await handler.handle_event(AudioStop().event())

    error_text = "\n".join(
        r.getMessage() for r in caplog.records if r.levelno == logging.ERROR
    )
    assert "permission denied" in error_text
