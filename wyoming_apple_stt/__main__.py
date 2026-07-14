#!/usr/bin/env python3
"""Wyoming Apple STT server entry point."""

import argparse
import asyncio
import json
import logging
from functools import partial

from wyoming.info import (
    AsrModel,
    AsrProgram,
    Attribution,
    Info,
    TtsProgram,
    TtsVoice,
)
from wyoming.server import AsyncServer

from .handler import AppleSTTEventHandler
from .stt import SttService, SttWorkerPool
from .tts import (
    SiriVoice,
    TtsService,
    TtsWorkerPool,
    discover_tts_voices,
    resolve_voice,
)

_LOGGER = logging.getLogger(__name__)

_APPLE_STT_ATTRIBUTION = Attribution(
    name="Apple",
    url="https://developer.apple.com/documentation/speech",
)

_APPLE_TTS_ATTRIBUTION = Attribution(
    name="Apple",
    url="https://support.apple.com/guide/mac-help/change-siri-settings-mchl3fd77655/mac",
)


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
    except asyncio.TimeoutError as exc:
        try:
            process.kill()
            await process.wait()
        except ProcessLookupError:
            pass
        _LOGGER.debug(
            "Language discovery failed (%s), falling back to [%s]",
            exc,
            default_language,
        )
    except (json.JSONDecodeError, OSError) as exc:
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
    except asyncio.TimeoutError as exc:
        try:
            process.kill()
            await process.wait()
        except ProcessLookupError:
            pass
        _LOGGER.warning("Model preload for '%s' failed: %s", language, exc)
        return
    except OSError as exc:
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


def _build_asr_program(languages: list[str]) -> AsrProgram:
    """Describe the Apple STT service for Wyoming Info.

    Args:
        languages: BCP-47 language codes the recognizer supports.

    Returns:
        An AsrProgram advertising streaming transcription support.
    """
    return AsrProgram(
        name="apple-stt",
        description="Apple on-device speech recognition",
        attribution=_APPLE_STT_ATTRIBUTION,
        installed=True,
        version=None,
        supports_transcript_streaming=True,
        models=[
            AsrModel(
                name="apple-stt",
                description="Apple on-device speech recognition",
                attribution=_APPLE_STT_ATTRIBUTION,
                installed=True,
                languages=languages,
                version=None,
            )
        ],
    )


def _build_tts_program(voices: list[SiriVoice]) -> TtsProgram:
    """Describe the Siri TTS service and its system voices for Wyoming Info.

    Args:
        voices: Discovered system voices (must be non-empty).

    Returns:
        A TtsProgram advertising streaming synthesis support.
    """
    return TtsProgram(
        name="apple-tts",
        description="Apple Siri on-device text-to-speech",
        attribution=_APPLE_TTS_ATTRIBUTION,
        installed=True,
        version=None,
        supports_synthesize_streaming=True,
        voices=[
            TtsVoice(
                name=voice.voice_id,
                description=voice.description,
                attribution=_APPLE_TTS_ATTRIBUTION,
                installed=True,
                version=str(voice.version),
                languages=[voice.language],
            )
            for voice in voices
        ],
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
        "--stt-idle-workers",
        type=int,
        default=1,
        help="Number of pre-warmed STT worker processes to keep ready (default: 1)",
    )
    parser.add_argument(
        "--apple-tts-bin",
        default="apple-tts",
        help="Path to the apple-tts Swift CLI binary",
    )
    parser.add_argument(
        "--tts-voice",
        default=None,
        help="Default TTS voice name (default: first discovered system voice)",
    )
    parser.add_argument(
        "--tts-timeout",
        type=int,
        default=60,
        help="Max seconds for a single synthesis (default: 60)",
    )
    parser.add_argument(
        "--tts-idle-workers",
        type=int,
        default=1,
        help="Number of pre-warmed TTS engine processes to keep ready (default: 1)",
    )
    parser.add_argument(
        "--tts-rate",
        type=float,
        default=1.0,
        help="TTS speaking rate multiplier (default: 1.0)",
    )
    parser.add_argument(
        "--tts-pitch",
        type=float,
        default=1.0,
        help="TTS pitch multiplier (default: 1.0)",
    )
    parser.add_argument(
        "--tts-volume",
        type=float,
        default=1.0,
        help="TTS volume multiplier (default: 1.0)",
    )
    parser.add_argument(
        "--no-tts",
        action="store_true",
        help="Disable the TTS service even when system voices are available",
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

    tts_service: TtsService | None = None
    tts_programs: list[TtsProgram] = []
    if not args.no_tts:
        tts_voices = await discover_tts_voices(args.apple_tts_bin)
        if tts_voices:
            default_voice = resolve_voice(tts_voices, name=args.tts_voice)
            if default_voice is None:
                if args.tts_voice:
                    _LOGGER.warning(
                        "TTS voice '%s' not found, using '%s'",
                        args.tts_voice,
                        tts_voices[0].voice_id,
                    )
                default_voice = tts_voices[0]

            pool = TtsWorkerPool(
                args.apple_tts_bin,
                preload_voice_path=default_voice.path,
                idle_target=args.tts_idle_workers,
            )
            tts_service = TtsService(
                pool=pool,
                voices=tts_voices,
                default_voice=default_voice,
                rate=args.tts_rate,
                pitch=args.tts_pitch,
                volume=args.tts_volume,
                timeout=args.tts_timeout,
            )
            tts_programs = [_build_tts_program(tts_voices)]
            _LOGGER.info(
                "TTS enabled with %d system voice(s), default '%s'",
                len(tts_voices),
                default_voice.voice_id,
            )
        else:
            _LOGGER.warning(
                "TTS disabled: no system Siri voices found. Select a Siri voice in "
                "System Settings → Siri (or Spoken Content) to enable it."
            )

    stt_service = SttService(
        pool=SttWorkerPool(
            args.apple_stt_bin,
            language=args.language,
            idle_target=args.stt_idle_workers,
        ),
        timeout=args.timeout,
    )

    wyoming_info = Info(
        asr=[_build_asr_program(languages)],
        tts=tts_programs,
    )

    _LOGGER.info(
        "Pre-warming %d STT worker process(es)...", args.stt_idle_workers
    )
    await stt_service.pool.start()

    if tts_service is not None:
        _LOGGER.info(
            "Pre-warming %d TTS engine process(es)...", args.tts_idle_workers
        )
        await tts_service.pool.start()

    lock = asyncio.Lock()
    server = AsyncServer.from_uri(args.uri)

    _LOGGER.info(
        "Wyoming Apple STT server ready on %s (language=%s, tts=%s)",
        args.uri,
        args.language,
        "on" if tts_service is not None else "off",
    )

    try:
        await server.run(
            partial(
                AppleSTTEventHandler,
                wyoming_info,
                args,
                lock,
                stt_service=stt_service,
                tts_service=tts_service,
            )
        )
    finally:
        await stt_service.pool.stop()
        if tts_service is not None:
            await tts_service.pool.stop()


def run() -> None:
    """Sync wrapper for main()."""
    asyncio.run(main())


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        pass
