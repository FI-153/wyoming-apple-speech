"""Tests for the streaming STT side of the Wyoming event handler."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from wyoming.asr import (
    Transcribe,
    Transcript,
    TranscriptChunk,
    TranscriptStart,
    TranscriptStop,
)
from wyoming.audio import AudioChunk, AudioStop

from wyoming_apple_stt.handler import AppleSTTEventHandler
from wyoming_apple_stt.stt import SttService, SttWorkerError


class FakeSession:
    """In-memory stand-in for an SttSession."""

    def __init__(self, worker: "FakeSttWorker") -> None:
        self._worker = worker
        self.audio = b""
        self.partial_texts: list[str] = []
        self.fail_send: str | None = None
        self.fail_finish: str | None = None
        self.finished = False

    async def send_audio(self, pcm: bytes) -> None:
        if self.fail_send:
            raise SttWorkerError(self.fail_send)
        self.audio += pcm
        self.partial_texts.append(self.audio.decode(errors="replace"))

    async def finish(self, timeout: float = 30) -> str:
        self.finished = True
        if self.fail_finish:
            self._worker.is_alive = False
            raise SttWorkerError(self.fail_finish)
        return self.audio.decode(errors="replace")

    async def partials(self):
        for text in self.partial_texts:
            yield text


class FakeSttWorker:
    """In-memory stand-in for an SttWorker."""

    def __init__(self) -> None:
        self.is_alive = True
        self.stopped = False
        self.sessions: list[FakeSession] = []

    async def transcribe(self, language=None) -> FakeSession:
        session = FakeSession(self)
        self.sessions.append(session)
        return session

    async def stop(self) -> None:
        self.stopped = True
        self.is_alive = False


class FakeSttPool:
    """In-memory stand-in for an SttWorkerPool."""

    def __init__(self) -> None:
        self.workers: list[FakeSttWorker] = []
        self.released: list[FakeSttWorker] = []
        self.fail_acquire = False

    async def acquire(self) -> FakeSttWorker:
        if self.fail_acquire:
            raise SttWorkerError("no worker available")
        worker = FakeSttWorker()
        self.workers.append(worker)
        return worker

    async def release(self, worker) -> None:
        self.released.append(worker)
        if not worker.is_alive:
            worker.stopped = True


@pytest.fixture
def stt_service() -> SttService:
    return SttService(pool=FakeSttPool(), timeout=5)


@pytest.fixture
def stt_handler(wyoming_info, cli_args, stt_service):
    """Create a handler with streaming STT enabled and mocked reader/writer."""
    reader = AsyncMock()
    writer = AsyncMock()
    handler = AppleSTTEventHandler(
        wyoming_info,
        cli_args,
        asyncio.Lock(),
        reader,
        writer,
        stt_service=stt_service,
    )
    handler.write_event = AsyncMock()
    return handler


def written_events(handler) -> list:
    return [call.args[0] for call in handler.write_event.call_args_list]


async def run_utterance(handler, chunks: list[bytes]) -> bool:
    """Push one full utterance through the handler."""
    await handler.handle_event(Transcribe(language="en").event())
    for chunk in chunks:
        event = AudioChunk(rate=16000, width=2, channels=1, audio=chunk).event()
        await handler.handle_event(event)
    return await handler.handle_event(AudioStop().event())


async def test_streaming_flow_emits_envelope_and_partials(stt_handler, stt_service):
    """Chunks stream to the worker; partials and final go out as events."""
    result = await run_utterance(stt_handler, [b"hello ", b"world"])
    assert result is False

    events = written_events(stt_handler)
    types = [event.type for event in events]
    assert types[0] == TranscriptStart().event().type
    assert types[-2:] == [Transcript(text="").event().type, TranscriptStop().event().type]

    chunk_texts = [
        TranscriptChunk.from_event(event).text
        for event in events
        if TranscriptChunk.is_type(event.type)
    ]
    assert chunk_texts == ["hello ", "hello world"]

    final = Transcript.from_event(events[-2])
    assert final.text == "hello world"

    # The worker went back to the pool.
    pool = stt_service.pool
    assert pool.workers[0] in pool.released
    assert pool.workers[0].sessions[0].finished


async def test_acquire_failure_falls_back_to_buffered(stt_handler, stt_service):
    """When no worker can be acquired, the old one-shot path still answers."""
    stt_service.pool.fail_acquire = True

    mock_process = AsyncMock()
    mock_process.communicate.return_value = (
        json.dumps({"text": "fallback text"}).encode(),
        b"",
    )
    mock_process.returncode = 0

    with patch(
        "wyoming_apple_stt.handler.asyncio.create_subprocess_exec",
        return_value=mock_process,
    ):
        result = await run_utterance(stt_handler, [b"\x00\x00" * 100])

    assert result is False
    events = written_events(stt_handler)
    finals = [Transcript.from_event(e) for e in events if Transcript.is_type(e.type)]
    assert finals[0].text == "fallback text"
    # Streaming is still advertised, so the envelope is kept.
    assert any(TranscriptStart.is_type(e.type) for e in events)
    assert TranscriptStop.is_type(events[-1].type)


async def test_finish_failure_falls_back_to_buffered(stt_handler, stt_service):
    """A worker error at finish falls back to the buffered transcription."""
    mock_process = AsyncMock()
    mock_process.communicate.return_value = (
        json.dumps({"text": "buffered result"}).encode(),
        b"",
    )
    mock_process.returncode = 0

    await stt_handler.handle_event(Transcribe(language="en").event())
    event = AudioChunk(rate=16000, width=2, channels=1, audio=b"speech").event()
    await stt_handler.handle_event(event)

    session = stt_service.pool.workers[0].sessions[0]
    session.fail_finish = "recognizer exploded"

    with patch(
        "wyoming_apple_stt.handler.asyncio.create_subprocess_exec",
        return_value=mock_process,
    ):
        result = await stt_handler.handle_event(AudioStop().event())

    assert result is False
    events = written_events(stt_handler)
    finals = [Transcript.from_event(e) for e in events if Transcript.is_type(e.type)]
    assert finals[0].text == "buffered result"
    # The broken worker was handed back for disposal, not reused.
    worker = stt_service.pool.workers[0]
    assert worker in stt_service.pool.released
    assert not worker.is_alive


async def test_send_failure_mid_stream_falls_back(stt_handler, stt_service):
    """A pipe failure while streaming audio tears the session down once and
    the buffered fallback still transcribes the full utterance."""
    mock_process = AsyncMock()
    mock_process.communicate.return_value = (
        json.dumps({"text": "recovered"}).encode(),
        b"",
    )
    mock_process.returncode = 0

    await stt_handler.handle_event(Transcribe(language="en").event())
    first = AudioChunk(rate=16000, width=2, channels=1, audio=b"first").event()
    await stt_handler.handle_event(first)

    stt_service.pool.workers[0].sessions[0].fail_send = "pipe broke"

    second = AudioChunk(rate=16000, width=2, channels=1, audio=b"second").event()
    await stt_handler.handle_event(second)
    # No new session is opened after the failure.
    assert len(stt_service.pool.workers) == 1

    with patch(
        "wyoming_apple_stt.handler.asyncio.create_subprocess_exec",
        return_value=mock_process,
    ) as mock_exec:
        result = await stt_handler.handle_event(AudioStop().event())

    assert result is False
    # The buffered fallback got the full audio, including the failed chunk.
    sent_audio = mock_process.communicate.call_args.kwargs.get("input") or b""
    assert b"first" in sent_audio and b"second" in sent_audio
    assert mock_exec.called
    finals = [
        Transcript.from_event(e)
        for e in written_events(stt_handler)
        if Transcript.is_type(e.type)
    ]
    assert finals[0].text == "recovered"


async def test_no_stt_service_keeps_legacy_shape(wyoming_info, cli_args):
    """Without an SttService the handler behaves exactly as before."""
    handler = AppleSTTEventHandler(
        wyoming_info, cli_args, asyncio.Lock(), AsyncMock(), AsyncMock()
    )
    handler.write_event = AsyncMock()

    mock_process = AsyncMock()
    mock_process.communicate.return_value = (
        json.dumps({"text": "legacy"}).encode(),
        b"",
    )
    mock_process.returncode = 0

    with patch(
        "wyoming_apple_stt.handler.asyncio.create_subprocess_exec",
        return_value=mock_process,
    ):
        result = await run_utterance(handler, [b"\x00\x00" * 100])

    assert result is False
    events = written_events(handler)
    assert len(events) == 1
    assert Transcript.from_event(events[0]).text == "legacy"


async def test_disconnect_stops_held_worker(stt_handler, stt_service):
    """A client dropping mid-utterance must not leak the session's worker."""
    await stt_handler.handle_event(Transcribe(language="en").event())
    event = AudioChunk(rate=16000, width=2, channels=1, audio=b"speech").event()
    await stt_handler.handle_event(event)

    worker = stt_service.pool.workers[0]
    await stt_handler.disconnect()
    assert worker.stopped


async def test_second_utterance_after_failure_streams_again(stt_handler, stt_service):
    """A session failure only affects its own utterance."""
    mock_process = AsyncMock()
    mock_process.communicate.return_value = (json.dumps({"text": "x"}).encode(), b"")
    mock_process.returncode = 0

    await stt_handler.handle_event(Transcribe(language="en").event())
    event = AudioChunk(rate=16000, width=2, channels=1, audio=b"boom").event()
    await stt_handler.handle_event(event)
    stt_service.pool.workers[0].sessions[0].fail_finish = "boom"
    with patch(
        "wyoming_apple_stt.handler.asyncio.create_subprocess_exec",
        return_value=mock_process,
    ):
        await stt_handler.handle_event(AudioStop().event())

    stt_handler.write_event.reset_mock()

    result = await run_utterance(stt_handler, [b"clean run"])
    assert result is False
    finals = [
        Transcript.from_event(e)
        for e in written_events(stt_handler)
        if Transcript.is_type(e.type)
    ]
    assert finals[0].text == "clean run"
    assert len(stt_service.pool.workers) == 2
