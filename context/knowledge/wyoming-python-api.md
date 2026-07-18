# Wyoming Python Package API Reference

Precise API details extracted from the `wyoming` package source and reference STT server
implementations (`wyoming-faster-whisper`, `wyoming-vosk`).

## Import Paths

```python
# Core event types for STT
from wyoming.event import Event
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStart, AudioStop
from wyoming.info import AsrModel, AsrProgram, Attribution, Describe, Info
from wyoming.server import AsyncEventHandler, AsyncServer, AsyncTcpServer
```

## Core Data Structures

### Event (wyoming.event)

```python
@dataclass
class Event:
    type: str
    data: Dict[str, Any] = field(default_factory=dict)
    payload: Optional[bytes] = None
```

Every Wyoming message is an `Event`. The `type` string identifies it (e.g. `"transcript"`,
`"describe"`). Typed wrappers (below) convert to/from `Event` via `.event()` and
`.from_event()`.

### Eventable (wyoming.event) -- abstract base

```python
class Eventable(ABC):
    @abstractmethod
    def event(self) -> Event: ...

    @staticmethod
    @abstractmethod
    def is_type(event_type: str) -> bool: ...

    @staticmethod
    @abstractmethod
    def from_event(event: Event) -> "Eventable": ...
```

All typed event classes (`Transcript`, `Transcribe`, `AudioChunk`, etc.) inherit from
`Eventable`.

---

## STT Event Types (wyoming.asr)

### Transcribe -- request from HA

```python
@dataclass
class Transcribe(Eventable):
    name: Optional[str] = None       # ASR model name
    language: Optional[str] = None   # Language of spoken audio
    context: Optional[Dict[str, Any]] = None
```

HA sends this before streaming audio. The `language` field is the one you care about.

### Transcript -- response to HA

```python
@dataclass
class Transcript(Eventable):
    text: str                                    # The transcription
    context: Optional[Dict[str, Any]] = None
    language: Optional[str] = None
```

Usage: `await self.write_event(Transcript(text="hello world").event())`

---

## Audio Event Types (wyoming.audio)

### AudioChunk

```python
@dataclass
class AudioChunk(AudioFormat, Eventable):
    audio: bytes                     # Raw PCM bytes
    timestamp: Optional[int] = None  # Milliseconds

    # Inherited from AudioFormat:
    # rate: int       (Hz)
    # width: int      (bytes per sample)
    # channels: int   (1 = mono)
```

### AudioStart / AudioStop

```python
@dataclass
class AudioStart(AudioFormat, Eventable):
    timestamp: Optional[int] = None

@dataclass
class AudioStop(Eventable):
    timestamp: Optional[int] = None
```

### AudioChunkConverter

Converts incoming audio to the format your STT engine expects:

```python
converter = AudioChunkConverter(rate=16000, width=2, channels=1)
chunk = converter.convert(AudioChunk.from_event(event))
```

---

## Info / Describe Events (wyoming.info)

### Describe -- request from HA

```python
@dataclass
class Describe(Eventable):
    # No fields. HA sends this to ask "what can you do?"
```

### Info -- response to Describe

```python
@dataclass
class Info(Eventable):
    asr: List[AsrProgram] = field(default_factory=list)
    tts: List[TtsProgram] = field(default_factory=list)
    handle: List[HandleProgram] = field(default_factory=list)
    intent: List[IntentProgram] = field(default_factory=list)
    wake: List[WakeProgram] = field(default_factory=list)
    mic: List[MicProgram] = field(default_factory=list)
    snd: List[SndProgram] = field(default_factory=list)
    satellite: Optional[Satellite] = None
```

For an STT-only server, only `asr` is populated.

### AsrProgram / AsrModel / Attribution

```python
@dataclass
class Attribution(DataClassJsonMixin):
    name: str    # Who made it
    url: str     # Where it's from

@dataclass
class Artifact(DataClassJsonMixin):
    name: str
    attribution: Attribution
    installed: bool
    description: Optional[str]
    version: Optional[str]

@dataclass
class AsrModel(Artifact):
    languages: List[str]   # e.g. ["en"]

@dataclass
class AsrProgram(Artifact):
    models: List[AsrModel]
    supports_transcript_streaming: bool = False
```

---

## Server Classes (wyoming.server)

### AsyncEventHandler

```python
class AsyncEventHandler(ABC):
    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.reader = reader
        self.writer = writer
        self._is_running = False

    @abstractmethod
    async def handle_event(self, event: Event) -> bool:
        """Handle an event. Return False to disconnect the client."""

    async def write_event(self, event: Event) -> None:
        """Send an event to the client."""

    async def run(self) -> None:
        """Receive events until stopped or handle_event returns False."""

    async def disconnect(self) -> None:
        """Called when client disconnects. Override for cleanup."""

    async def stop(self) -> None:
        """Try to stop the event handler."""
```

Key contract: `handle_event` returns `True` to keep the connection alive, `False` to
disconnect. The handler's `run()` loop reads events and calls `handle_event` until it
returns `False` or the connection drops.

### AsyncServer.from_uri

```python
server = AsyncServer.from_uri("tcp://0.0.0.0:10300")
await server.run(handler_factory)
```

The `handler_factory` is `Callable[[StreamReader, StreamWriter], AsyncEventHandler]`.
Use `functools.partial` to bind extra args.

### AsyncTcpServer

```python
class AsyncTcpServer(AsyncServer):
    def __init__(self, host: str, port: int) -> None: ...
    async def run(self, handler_factory: HandlerFactory) -> None: ...
    async def start(self, handler_factory: HandlerFactory) -> None: ...  # non-blocking
    async def stop(self) -> None: ...
```

---

## Reference Pattern: STT Event Handler

Distilled from `wyoming-faster-whisper` DispatchEventHandler and `wyoming-vosk`
VoskEventHandler. This is the canonical pattern both implementations follow:

```python
import asyncio
import logging
from typing import Optional

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStop
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler

_LOGGER = logging.getLogger(__name__)


class SttEventHandler(AsyncEventHandler):
    """Wyoming STT event handler — one instance per client connection."""

    def __init__(
        self,
        wyoming_info: Info,
        # ... your custom args ...
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        # Pre-compute the info event (it never changes)
        self.wyoming_info_event = wyoming_info.event()

        # Per-request state (reset after each transcription)
        self._language: Optional[str] = None
        self._audio_bytes = bytearray()
        self._audio_converter = AudioChunkConverter(
            rate=16000, width=2, channels=1
        )

    async def handle_event(self, event: Event) -> bool:
        # --- Describe (HA asks "what are you?") ---
        if Describe.is_type(event.type):
            await self.write_event(self.wyoming_info_event)
            return True

        # --- Transcribe (HA starts a new STT request) ---
        if Transcribe.is_type(event.type):
            transcribe = Transcribe.from_event(event)
            self._language = transcribe.language
            return True

        # --- AudioChunk (HA streams PCM audio) ---
        if AudioChunk.is_type(event.type):
            chunk = AudioChunk.from_event(event)
            chunk = self._audio_converter.convert(chunk)
            self._audio_bytes.extend(chunk.audio)
            return True

        # --- AudioStop (HA says "done, transcribe now") ---
        if AudioStop.is_type(event.type):
            text = await self._transcribe(
                bytes(self._audio_bytes), self._language
            )
            await self.write_event(Transcript(text=text).event())

            # Reset state
            self._audio_bytes.clear()
            self._language = None

            # Return False = close connection (one-shot per HA request)
            return False

        return True

    async def disconnect(self) -> None:
        """Called when the client disconnects."""

    async def _transcribe(self, audio: bytes, language: Optional[str]) -> str:
        """Run transcription. Override or inject your engine here."""
        raise NotImplementedError
```

### Key observations from both reference implementations:

1. **One handler per connection.** HA opens a TCP connection, sends Transcribe + audio +
   AudioStop, gets back Transcript, and the handler returns `False` to close.

2. **AudioChunkConverter** normalizes incoming audio to 16kHz/16-bit/mono regardless of what
   HA sends. Both faster-whisper and vosk do this.

3. **wyoming_info.event()** is pre-computed once in `__init__` and reused for every Describe
   response. The `Info` object's `.event()` method serializes to an `Event`.

4. **Return False from AudioStop handler** to signal completion and disconnect. Vosk and
   faster-whisper both do this.

5. **AudioStart is ignored.** Neither implementation acts on `AudioStart`. Audio accumulation
   begins when `AudioChunk` events arrive.

---

## Reference Pattern: __main__.py Entry Point

Distilled from both `wyoming-faster-whisper` and `wyoming-vosk`:

```python
#!/usr/bin/env python3
"""Wyoming Apple Speech server entry point."""

import argparse
import asyncio
import logging
from functools import partial

from wyoming.info import AsrModel, AsrProgram, Attribution, Info
from wyoming.server import AsyncServer

from .handler import AppleSTTEventHandler

_LOGGER = logging.getLogger(__name__)


async def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--uri",
        default="tcp://0.0.0.0:10300",
        help="unix:// or tcp:// URI to listen on",
    )
    parser.add_argument(
        "--apple-stt-bin",
        default="apple-stt",
        help="Path to the apple-stt Swift CLI binary",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Default recognition language",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Max seconds for a single transcription",
    )
    parser.add_argument(
        "--max-audio-seconds",
        type=int,
        default=60,
        help="Max audio duration to buffer",
    )
    parser.add_argument("--debug", action="store_true", help="Log DEBUG messages")
    parser.add_argument(
        "--log-format",
        default=logging.BASIC_FORMAT,
        help="Format for log messages",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format=args.log_format,
    )
    _LOGGER.debug(args)

    # Build the Info response that describes this STT server to HA
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
                version=None,  # or your __version__
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

    server = AsyncServer.from_uri(args.uri)

    _LOGGER.info("Ready")

    # partial() binds extra args; the server passes (reader, writer) as the last two
    await server.run(
        partial(
            AppleSTTEventHandler,
            wyoming_info,
            args,
        )
    )


def run() -> None:
    """Sync wrapper for main()."""
    asyncio.run(main())


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        pass
```

### How partial() and HandlerFactory work:

The server expects `HandlerFactory = Callable[[StreamReader, StreamWriter], AsyncEventHandler]`.
When you do:

```python
partial(AppleSTTEventHandler, wyoming_info, args)
```

...and `AppleSTTEventHandler.__init__` is:

```python
def __init__(self, wyoming_info, args, *args_, **kwargs_):
    super().__init__(*args_, **kwargs_)
```

...then the server calls `handler_factory(reader, writer)` which becomes
`AppleSTTEventHandler(wyoming_info, args, reader, writer)`. The `*args, **kwargs` in
`__init__` absorb the `reader` and `writer` and pass them to `super().__init__()`.

This is the pattern both reference implementations use.

---

## Wire Protocol Summary

The Wyoming protocol is line-delimited JSON over TCP:

1. **Line 1:** JSON metadata: `{"type": "...", "version": 1, "data_length": N, "payload_length": M}`
2. **Bytes [data_length]:** JSON data dict (the event's fields)
3. **Bytes [payload_length]:** Binary payload (e.g. PCM audio for AudioChunk)

The `wyoming` package handles all serialization. You never construct raw JSON -- you call
`event.event()` to get an `Event` and `write_event()` to send it.

## Event Flow for a Complete STT Request

```
HA → Server:  Describe
Server → HA:  Info (with asr=[AsrProgram(...)])

HA → Server:  Transcribe(language="en")
HA → Server:  AudioChunk(audio=<pcm>, rate=16000, width=2, channels=1)
HA → Server:  AudioChunk(audio=<pcm>, ...)
HA → Server:  ... (more chunks)
HA → Server:  AudioStop
Server → HA:  Transcript(text="turn on the lights")
[connection closed]
```

Note: The `Describe`/`Info` exchange may happen on a separate connection from the actual
transcription. HA probes capabilities first, then opens a new connection for each STT request.
