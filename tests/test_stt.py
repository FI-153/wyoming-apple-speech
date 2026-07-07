"""Tests for the streaming STT module: worker protocol, sessions, and the pool."""

import asyncio
import stat

import pytest

from wyoming_apple_stt.stt import (
    SttWorker,
    SttWorkerError,
    SttWorkerPool,
)

# A stand-in for `apple-stt --worker`: prints ready, then serves transcription
# sessions. Each audio frame produces a partial with the cumulative decoded
# text; stop produces a final with the full text. Payload "explode" triggers
# an error frame at stop, "die" kills the process immediately.
FAKE_WORKER = """#!/usr/bin/env python3
import json, os, sys

def frame(header):
    sys.stdout.buffer.write(json.dumps(header).encode() + b"\\n")
    sys.stdout.buffer.flush()

if "--fail-start" in sys.argv:
    sys.exit(1)

frame({"type": "ready"})

while True:
    line = sys.stdin.buffer.readline()
    if not line:
        break
    command = json.loads(line)
    assert command["type"] == "transcribe"
    received = b""
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            sys.exit(0)
        command = json.loads(line)
        if command["type"] == "audio":
            received += sys.stdin.buffer.read(command["length"])
            if b"die" in received:
                os._exit(1)
            frame({"type": "partial", "text": received.decode(errors="replace")})
        elif command["type"] == "stop":
            if b"explode" in received:
                frame({"type": "error", "message": "recognizer refused"})
            elif b"stall" in received:
                pass  # never answer: simulates a hung recognizer
            else:
                frame({"type": "final", "text": received.decode(errors="replace")})
            break
"""


@pytest.fixture
def fake_worker_bin(tmp_path):
    """Write the fake apple-stt worker script and return its path."""
    path = tmp_path / "fake-apple-stt"
    path.write_text(FAKE_WORKER)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


# --- SttWorker / SttSession ---


async def test_worker_start_ready(fake_worker_bin):
    worker = SttWorker(fake_worker_bin, language="en")
    await worker.start(timeout=10)
    try:
        assert worker.is_alive
    finally:
        await worker.stop()


async def test_worker_start_failure_raises():
    worker = SttWorker("/nonexistent/apple-stt", language="en")
    with pytest.raises(SttWorkerError):
        await worker.start(timeout=5)


async def test_worker_exit_before_ready_raises(fake_worker_bin):
    worker = SttWorker(fake_worker_bin, language="en", extra_args=["--fail-start"])
    with pytest.raises(SttWorkerError):
        await worker.start(timeout=5)


async def test_session_streams_partials_and_final(fake_worker_bin):
    worker = SttWorker(fake_worker_bin, language="en")
    await worker.start(timeout=10)
    try:
        session = await worker.transcribe(language="en")
        await session.send_audio(b"hello ")
        await session.send_audio(b"world")

        partials = []

        async def collect():
            async for text in session.partials():
                partials.append(text)

        collector = asyncio.create_task(collect())
        final = await session.finish(timeout=10)
        await collector

        assert final == "hello world"
        assert partials == ["hello ", "hello world"]
        assert worker.is_alive
    finally:
        await worker.stop()


async def test_worker_serves_multiple_sessions(fake_worker_bin):
    worker = SttWorker(fake_worker_bin, language="en")
    await worker.start(timeout=10)
    try:
        for text in (b"first", b"second"):
            session = await worker.transcribe(language="en")
            await session.send_audio(text)
            assert await session.finish(timeout=10) == text.decode()
        assert worker.is_alive
    finally:
        await worker.stop()


async def test_session_error_frame_raises_but_worker_survives(fake_worker_bin):
    worker = SttWorker(fake_worker_bin, language="en")
    await worker.start(timeout=10)
    try:
        session = await worker.transcribe(language="en")
        await session.send_audio(b"explode")
        with pytest.raises(SttWorkerError, match="recognizer refused"):
            await session.finish(timeout=10)
        # The protocol stayed in sync, so the worker remains usable.
        assert worker.is_alive
        session = await worker.transcribe(language="en")
        await session.send_audio(b"ok")
        assert await session.finish(timeout=10) == "ok"
    finally:
        await worker.stop()


async def test_worker_death_midsession_raises(fake_worker_bin):
    worker = SttWorker(fake_worker_bin, language="en")
    await worker.start(timeout=10)
    try:
        session = await worker.transcribe(language="en")
        await session.send_audio(b"die")
        with pytest.raises(SttWorkerError):
            await session.finish(timeout=10)
        assert not worker.is_alive
    finally:
        await worker.stop()


async def test_finish_timeout_marks_worker_unusable(fake_worker_bin):
    worker = SttWorker(fake_worker_bin, language="en")
    await worker.start(timeout=10)
    try:
        session = await worker.transcribe(language="en")
        await session.send_audio(b"stall")
        with pytest.raises(SttWorkerError):
            await session.finish(timeout=0.2)
        # The worker may still hold unread frames, so it must not be reused.
        assert not worker.is_alive
    finally:
        await worker.stop()


# --- SttWorkerPool ---


async def test_pool_prewarms_idle_worker(fake_worker_bin):
    pool = SttWorkerPool(fake_worker_bin, language="en", idle_target=1)
    await pool.start()
    try:
        assert len(pool._idle) == 1
        assert pool._idle[0].is_alive
    finally:
        await pool.stop()


async def test_pool_acquire_returns_warm_worker_and_replenishes(fake_worker_bin):
    pool = SttWorkerPool(fake_worker_bin, language="en", idle_target=1)
    await pool.start()
    try:
        warm_worker = pool._idle[0]
        worker = await pool.acquire()
        assert worker is warm_worker  # no spawn wait on the request path

        # A replacement spawn was scheduled in the background.
        await asyncio.gather(*pool._spawn_tasks)
        assert len(pool._idle) == 1
        assert pool._idle[0] is not worker

        await pool.release(worker)
        # Pool is full again, so the released worker is disposed.
        assert len(pool._idle) == 1
        assert not worker.is_alive
    finally:
        await pool.stop()


async def test_pool_acquire_spawns_when_empty(fake_worker_bin):
    pool = SttWorkerPool(fake_worker_bin, language="en", idle_target=1)
    # No start(): pool is empty, acquire must spawn synchronously.
    worker = await pool.acquire()
    try:
        assert worker.is_alive
    finally:
        await pool.release(worker)
        await pool.stop()


async def test_pool_release_disposes_dead_worker(fake_worker_bin):
    pool = SttWorkerPool(fake_worker_bin, language="en", idle_target=2)
    await pool.start()
    try:
        worker = await pool.acquire()
        session = await worker.transcribe(language="en")
        await session.send_audio(b"die")
        with pytest.raises(SttWorkerError):
            await session.finish(timeout=10)
        await pool.release(worker)
        assert worker not in pool._idle
    finally:
        await pool.stop()


async def test_pool_stop_kills_idle_workers(fake_worker_bin):
    pool = SttWorkerPool(fake_worker_bin, language="en", idle_target=1)
    await pool.start()
    worker = pool._idle[0]
    await pool.stop()
    assert not worker.is_alive
