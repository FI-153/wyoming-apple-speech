"""Tests for the TTS side of the Wyoming event handler."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.tts import (
    Synthesize,
    SynthesizeChunk,
    SynthesizeStart,
    SynthesizeStop,
    SynthesizeStopped,
    SynthesizeVoice,
)

from wyoming_apple_stt.handler import AppleSTTEventHandler
from wyoming_apple_stt.tts import AudioFrame, SiriVoice, TtsService, TtsWorkerError


class FakeWorker:
    """In-memory stand-in for a TtsWorker."""

    def __init__(self) -> None:
        self.is_alive = True
        self.requests: list[dict] = []
        self.fail_next: str | None = None
        self.stopped = False

    async def synthesize(self, text, voice_path, rate, pitch, volume, timeout):
        self.requests.append({"text": text, "voice_path": voice_path})
        if self.fail_next:
            message, self.fail_next = self.fail_next, None
            raise TtsWorkerError(message)
        yield AudioFrame(audio=text.encode(), rate=48000, width=2, channels=1)

    async def stop(self):
        self.stopped = True
        self.is_alive = False


class FakePool:
    """In-memory stand-in for a TtsWorkerPool."""

    def __init__(self) -> None:
        self.workers: list[FakeWorker] = []
        self.released: list[FakeWorker] = []

    async def acquire(self) -> FakeWorker:
        worker = FakeWorker()
        self.workers.append(worker)
        return worker

    async def release(self, worker) -> None:
        self.released.append(worker)


@pytest.fixture
def siri_voice() -> SiriVoice:
    return SiriVoice(
        name="helena", language="de-DE", type="neural", footprint="premium",
        gender="female", version=1328, path="/assets/helena.asset",
    )


@pytest.fixture
def tts_service(siri_voice) -> TtsService:
    return TtsService(
        pool=FakePool(), voices=[siri_voice], default_voice=siri_voice, timeout=10
    )


@pytest.fixture
def tts_handler(wyoming_info, cli_args, tts_service):
    """Create a handler with TTS enabled and mocked reader/writer."""
    reader = AsyncMock()
    writer = AsyncMock()
    handler = AppleSTTEventHandler(
        wyoming_info,
        cli_args,
        asyncio.Lock(),
        reader,
        writer,
        tts_service=tts_service,
    )
    handler.write_event = AsyncMock()
    return handler


def written_events(handler) -> list:
    return [call.args[0] for call in handler.write_event.call_args_list]


async def test_synthesize_streams_audio_envelope(tts_handler, tts_service):
    """Legacy synthesize: AudioStart → AudioChunk → AudioStop, then disconnect."""
    event = Synthesize(text="Hallo Welt.").event()
    result = await tts_handler.handle_event(event)

    assert result is False
    events = written_events(tts_handler)
    assert AudioStart.is_type(events[0].type)
    assert AudioStart.from_event(events[0]).rate == 48000
    assert AudioChunk.is_type(events[1].type)
    assert AudioChunk.from_event(events[1]).audio == b"Hallo Welt."
    assert AudioStop.is_type(events[-1].type)

    # Worker was returned to the pool.
    pool = tts_service.pool
    assert len(pool.workers) == 1
    assert pool.released == pool.workers


async def test_synthesize_joins_multiline_text(tts_handler, tts_service):
    """Newlines in the request collapse into a single synthesized line."""
    await tts_handler.handle_event(Synthesize(text="Zeile eins.\nZeile zwei.").event())
    worker = tts_service.pool.workers[0]
    assert worker.requests[0]["text"] == "Zeile eins. Zeile zwei."


async def test_synthesize_empty_text_still_sends_envelope(tts_handler):
    """Empty input must still produce AudioStart + AudioStop."""
    await tts_handler.handle_event(Synthesize(text=" \n ").event())
    events = written_events(tts_handler)
    assert AudioStart.is_type(events[0].type)
    assert AudioStop.is_type(events[1].type)
    assert len(events) == 2


async def test_streaming_flow(tts_handler, tts_service):
    """synthesize-start/chunk/stop with per-sentence synthesis."""
    assert await tts_handler.handle_event(SynthesizeStart().event()) is True
    assert await tts_handler.handle_event(SynthesizeChunk(text="Erster Satz. Zwei").event()) is True

    # Legacy synthesize repeating the full text mid-stream is ignored.
    assert await tts_handler.handle_event(Synthesize(text="Erster Satz. Zweiter").event()) is True

    assert await tts_handler.handle_event(SynthesizeChunk(text="ter Satz. ").event()) is True
    result = await tts_handler.handle_event(SynthesizeStop().event())
    assert result is False

    worker = tts_service.pool.workers[0]
    assert [request["text"] for request in worker.requests] == [
        "Erster Satz.",
        "Zweiter Satz.",
    ]

    events = written_events(tts_handler)
    assert AudioStart.is_type(events[0].type)
    audio_chunks = [e for e in events if AudioChunk.is_type(e.type)]
    assert len(audio_chunks) == 2
    assert AudioStop.is_type(events[-2].type)
    assert SynthesizeStopped.is_type(events[-1].type)

    # The one worker served the whole stream and went back to the pool.
    assert len(tts_service.pool.workers) == 1
    assert tts_service.pool.released == [worker]


async def test_streaming_flushes_remainder_on_stop(tts_handler, tts_service):
    """Text without a final sentence boundary is synthesized at stop."""
    await tts_handler.handle_event(SynthesizeStart().event())
    await tts_handler.handle_event(SynthesizeChunk(text="Kein Satzende").event())
    await tts_handler.handle_event(SynthesizeStop().event())

    worker = tts_service.pool.workers[0]
    assert [request["text"] for request in worker.requests] == ["Kein Satzende"]


async def test_streaming_voice_selection(tts_handler, tts_service, siri_voice):
    """A requested voice name resolves to the matching system voice path."""
    start = SynthesizeStart(voice=SynthesizeVoice(name="helena-de-DE-premium"))
    await tts_handler.handle_event(start.event())
    await tts_handler.handle_event(SynthesizeChunk(text="Hallo. ").event())
    await tts_handler.handle_event(SynthesizeStop().event())

    worker = tts_service.pool.workers[0]
    assert worker.requests[0]["voice_path"] == siri_voice.path


async def test_unknown_voice_falls_back_to_default(tts_handler, tts_service, siri_voice):
    """An unknown requested voice falls back to the default voice."""
    event = Synthesize(text="Hallo.", voice=SynthesizeVoice(name="nope")).event()
    await tts_handler.handle_event(event)
    worker = tts_service.pool.workers[0]
    assert worker.requests[0]["voice_path"] == siri_voice.path


async def test_worker_failure_before_audio_retries_once(tts_handler, tts_service):
    """A failure before any audio swaps the worker and retries the text."""
    pool = tts_service.pool

    original_acquire = pool.acquire
    first = True

    async def acquire_with_failing_first():
        nonlocal first
        worker = await original_acquire()
        if first:
            worker.fail_next = "engine died"
            first = False
        return worker

    pool.acquire = acquire_with_failing_first

    await tts_handler.handle_event(Synthesize(text="Hallo Welt.").event())

    # First worker failed and was stopped; second one served the retry.
    assert len(pool.workers) == 2
    assert pool.workers[0].stopped
    events = written_events(tts_handler)
    audio_chunks = [e for e in events if AudioChunk.is_type(e.type)]
    assert len(audio_chunks) == 1
    assert AudioStop.is_type(events[-1].type)


async def test_disconnect_disposes_held_worker(tts_handler, tts_service):
    """A client dropping mid-stream must not leak the held worker."""
    await tts_handler.handle_event(SynthesizeStart().event())
    worker = tts_service.pool.workers[0]

    await tts_handler.disconnect()

    assert worker.stopped
    assert tts_service.pool.released == []


async def test_tts_events_ignored_without_tts_service(handler_without_tts):
    """Without a TTS service, synthesize events fall through harmlessly."""
    result = await handler_without_tts.handle_event(Synthesize(text="Hallo.").event())
    assert result is True
    handler_without_tts.write_event.assert_not_called()


@pytest.fixture
def handler_without_tts(wyoming_info, cli_args):
    reader = AsyncMock()
    writer = AsyncMock()
    handler = AppleSTTEventHandler(
        wyoming_info, cli_args, asyncio.Lock(), reader, writer
    )
    handler.write_event = AsyncMock()
    return handler
