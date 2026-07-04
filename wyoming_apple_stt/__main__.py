#!/usr/bin/env python3
"""Wyoming Apple STT server entry point."""

import argparse
import asyncio
import json
import logging
from functools import partial

from wyoming.info import AsrModel, AsrProgram, Attribution, Info
from wyoming.server import AsyncServer

from .handler import AppleSTTEventHandler

_LOGGER = logging.getLogger(__name__)


async def _discover_languages(bin_path: str, default_language: str) -> list[str]:
    """Query the Swift CLI for supported languages.

    Calls the apple-stt binary with --list-languages and parses the
    JSON array of BCP-47 language codes. Falls back to a single-element
    list containing default_language on any failure.

    Args:
        bin_path: Path to the apple-stt Swift CLI binary.
        default_language: BCP-47 language code to use as fallback.

    Returns:
        List of BCP-47 language codes supported by the binary, or
        [default_language] if discovery fails.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            bin_path, "--list-languages",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            process.communicate(), timeout=10
        )
        if process.returncode == 0:
            languages: list[str] = json.loads(stdout.decode())
            _LOGGER.debug(
                "Discovered %d supported languages: %s",
                len(languages),
                languages,
            )
            return languages
    except (asyncio.TimeoutError, json.JSONDecodeError, OSError) as exc:
        _LOGGER.debug(
            "Language discovery failed (%s), falling back to [%s]",
            exc,
            default_language,
        )
    _LOGGER.debug(
        "Falling back to default language list: [%s]",
        default_language,
    )
    return [default_language]


async def _preload_model(bin_path: str, language: str) -> None:
    """Preload the on-device speech model for a language at startup.

    Invokes the apple-stt binary with --preload so any first-use model
    download or ensure step happens now, with a generous timeout, rather
    than during the first transcription request (which is bounded by the
    much shorter per-request timeout). Failure is logged and swallowed —
    the server must start regardless.

    Args:
        bin_path: Path to the apple-stt Swift CLI binary.
        language: BCP-47 language code whose model to preload.
    """
    _LOGGER.info("Preloading speech model for language '%s'...", language)
    try:
        process = await asyncio.create_subprocess_exec(
            bin_path, "--preload", "--language", language,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
    except (asyncio.TimeoutError, OSError) as exc:
        _LOGGER.warning("Model preload for '%s' failed: %s", language, exc)
        return

    if process.returncode == 0:
        _LOGGER.info("Speech model ready for language '%s'", language)
    else:
        _LOGGER.warning(
            "Model preload for '%s' failed (exit %s): %s",
            language,
            process.returncode,
            stderr.decode().strip() or "(no stderr output)",
        )


async def main() -> None:
    """Parse args, build Info, and start the Wyoming server."""
    parser = argparse.ArgumentParser(
        description="Wyoming server for Apple on-device speech recognition"
    )
    parser.add_argument(
        "--uri",
        default="tcp://0.0.0.0:10300",
        help="URI to listen on (default: tcp://0.0.0.0:10300)",
    )
    parser.add_argument(
        "--apple-stt-bin",
        default="apple-stt",
        help="Path to the apple-stt Swift CLI binary",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Default recognition language (default: en)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Max seconds for a single transcription (default: 30)",
    )
    parser.add_argument(
        "--max-audio-seconds",
        type=int,
        default=60,
        help="Max audio duration to buffer in seconds (default: 60)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )
    _LOGGER.debug("Args: %s", args)

    languages = await _discover_languages(args.apple_stt_bin, args.language)

    await _preload_model(args.apple_stt_bin, args.language)

    wyoming_info = Info(
        asr=[
            AsrProgram(
                name="apple-stt",
                description="Apple on-device speech recognition",
                attribution=Attribution(
                    name="Apple",
                    url="https://developer.apple.com/documentation/speech",
                ),
                installed=True,
                version=None,
                models=[
                    AsrModel(
                        name="apple-stt",
                        description="Apple on-device speech recognition",
                        attribution=Attribution(
                            name="Apple",
                            url="https://developer.apple.com/documentation/speech",
                        ),
                        installed=True,
                        languages=languages,
                        version=None,
                    )
                ],
            )
        ],
    )

    lock = asyncio.Lock()
    server = AsyncServer.from_uri(args.uri)

    _LOGGER.info(
        "Wyoming Apple STT server ready on %s (language=%s)",
        args.uri,
        args.language,
    )

    await server.run(partial(AppleSTTEventHandler, wyoming_info, args, lock))


def run() -> None:
    """Sync wrapper for main()."""
    asyncio.run(main())


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        pass
