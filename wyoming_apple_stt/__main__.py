#!/usr/bin/env python3
"""Wyoming Apple STT server entry point."""

import argparse
import asyncio
import logging
from functools import partial

from wyoming.info import AsrModel, AsrProgram, Attribution, Info
from wyoming.server import AsyncServer

from .handler import AppleSTTEventHandler

_LOGGER = logging.getLogger(__name__)


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
                        languages=[args.language],
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
