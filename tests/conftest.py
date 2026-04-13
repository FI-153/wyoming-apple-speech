"""Shared fixtures for Wyoming Apple STT tests."""

import argparse

import pytest
from wyoming.info import AsrModel, AsrProgram, Attribution, Info


@pytest.fixture
def wyoming_info() -> Info:
    """Create a minimal Info object for testing."""
    return Info(
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
                        languages=["en"],
                        version=None,
                    )
                ],
            )
        ],
    )


@pytest.fixture
def cli_args() -> argparse.Namespace:
    """Create mock CLI args."""
    return argparse.Namespace(
        apple_stt_bin="/usr/local/bin/apple-stt",
        timeout=30,
        max_audio_seconds=60,
        language="en",
    )
