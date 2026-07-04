"""Tests for the Wyoming Apple STT server entry point."""

import logging
from unittest.mock import AsyncMock, patch

from wyoming_apple_stt.__main__ import _preload_model


async def test_preload_model_invokes_cli_with_preload_flag(caplog):
    """A successful preload issues --preload --language <lang> and logs INFO."""
    mock_process = AsyncMock()
    mock_process.communicate.return_value = (b"", b"")
    mock_process.returncode = 0

    with patch(
        "wyoming_apple_stt.__main__.asyncio.create_subprocess_exec",
        return_value=mock_process,
    ) as mock_exec:
        with caplog.at_level(logging.INFO):
            await _preload_model("/usr/local/bin/apple-stt", "en")

    call_args = mock_exec.call_args[0]
    assert call_args[0] == "/usr/local/bin/apple-stt"
    assert "--preload" in call_args
    assert "--language" in call_args
    assert "en" in call_args
    assert any(r.levelno == logging.INFO for r in caplog.records)


async def test_preload_model_nonzero_exit_does_not_raise(caplog):
    """A non-zero exit is logged as a warning and never raised."""
    mock_process = AsyncMock()
    mock_process.communicate.return_value = (b"", b"model download failed\n")
    mock_process.returncode = 1

    with patch(
        "wyoming_apple_stt.__main__.asyncio.create_subprocess_exec",
        return_value=mock_process,
    ):
        with caplog.at_level(logging.WARNING):
            await _preload_model("/usr/local/bin/apple-stt", "en")

    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_preload_model_missing_binary_does_not_raise(caplog):
    """A missing binary must not prevent server startup."""
    with patch(
        "wyoming_apple_stt.__main__.asyncio.create_subprocess_exec",
        side_effect=FileNotFoundError("no such file"),
    ):
        with caplog.at_level(logging.WARNING):
            await _preload_model("/bad/path/apple-stt", "en")

    assert any(r.levelno == logging.WARNING for r in caplog.records)
