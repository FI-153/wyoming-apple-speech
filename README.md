# Wyoming Apple STT

On-device speech-to-text **and Siri text-to-speech** for Home Assistant, powered by
Apple's Speech framework and the Siri speech synthesizer.
Every word stays on your Mac: no cloud, no API key, no usage limits, full privacy.

<img src="https://github.com/user-attachments/assets/a66196c6-065c-44d0-a381-12261da7d0c8" />

## Requirements

- macOS 15 (Sequoia) or later.
- [Home Assistant](https://www.home-assistant.io) with the [Wyoming](https://www.home-assistant.io/integrations/wyoming/) integration.

**Which speech engine runs depends on your macOS version:**

- **macOS 26 (Tahoe) or later** — uses Apple's new [**SpeechAnalyzer**](https://developer.apple.com/documentation/speech/speechanalyzer) API.
  Language models download on-demand the first time a given language is used.
- **macOS 15 (Sequoia)** — falls back to the legacy
  [**SFSpeechRecognizer**](https://developer.apple.com/documentation/speech/sfspeechrecognizer) API. Uses whichever speech-recognition locales are already
  installed via **System Settings → General → Language & Region → Dictation**
  (or that macOS has pre-downloaded).

Both engines run fully on-device. SpeechAnalyzer is faster and more precise.

## Streaming transcription ⚡

Recognition runs **while you are still speaking**, not after. Audio chunks are fed to a
persistent `apple-stt --worker` process as they arrive from Home Assistant, partial
transcripts stream back as Wyoming `transcript-chunk` events, and the final transcript is
ready within milliseconds of the audio ending — instead of paying for a full recognition
pass at that point.

Workers are pre-warmed: a worker with a loaded recognition model sits idle at all times
(`--stt-idle-workers`, default 1), and taking one triggers a background replacement spawn
so concurrent utterances never wait for model initialization. If the streaming path fails
for any reason, the server transparently falls back to buffered one-shot transcription of
the same utterance — no audio is lost.

## Text-to-speech (Siri voices) 🗣️

The server also exposes the Mac's Siri voices as a Wyoming TTS service with **streaming
synthesis**: audio starts flowing to Home Assistant as soon as the first samples are
generated, sentence by sentence, instead of waiting for the whole reply. To keep latency
near zero, a synthesis engine is pre-warmed in an idle worker process at all times — when a
request arrives it starts speaking immediately while a replacement engine spins up in the
background for concurrent requests.

Only voices that macOS manages itself are offered, because they always match the system's
Siri engine. macOS only keeps a *full* voice bundle on disk for a voice you've actually
selected — the language's other voice slots stay as tiny preview-only resource bundles
until you switch to them, so each voice you want available has to be selected at least
once:

1. Open **System Settings → Siri → Siri Voice** (or **Accessibility → Spoken Content →
   System Voice**), pick the language, then select **each voice slot you want** (e.g.
   *Voice 1*/German "Martin" and *Voice 2*/German "Helena" are separate downloads) and let
   it finish downloading. You can select a different voice afterwards as your live Siri
   voice — once downloaded, the bundle stays and stays usable by this server regardless of
   which one is currently active.
2. Restart the server. The voices appear automatically in Home Assistant's voice picker
   (run `swift/.build/release/apple-tts --list-voices` to check what the server currently
   sees without restarting anything).

If no Siri voice is installed, the server logs a warning and runs STT-only. TTS can also be
turned off explicitly with `--no-tts` (via `EXTRA_ARGS`).

Useful `EXTRA_ARGS` flags: `--tts-voice <name>` (default voice), `--tts-rate 1.2`
(speaking speed), `--tts-idle-workers 2` (more pre-warmed engines), `--no-tts`.

### Concurrency model

Each in-flight synthesis runs in its own `apple-tts` worker process, so requests never
queue behind each other. Beyond the pre-warmed pool (`--tts-idle-workers`, default 1), a
worker is spawned on demand for every concurrent request with no upper limit — the pool
only bounds how many engines are kept *pre-warmed*, not how many can run at once. A worker
that finishes while the pool already has enough idle workers is torn down immediately;
there's no idle timeout — surplus workers aren't kept around "just in case," and the
`idle_target` workers that are kept live until the server stops.

In practice this scales with the Mac's CPU: tested on an 8-core Mac mini, 1–80 concurrent
requests all completed successfully (no errors, no dropped connections) at roughly 55 MB
resident memory per active worker. Latency stays near-instant (first audio in under
100 ms) up to about as many concurrent requests as physical cores; beyond that, requests
queue for CPU time and first-audio latency grows roughly linearly (e.g. ~3–11s at 80
concurrent requests on 8 cores). This comfortably covers realistic Home Assistant usage
(one or a handful of simultaneous voice satellites); it isn't tuned for large numbers of
simultaneous conversations on constrained hardware.

## Install (Homebrew) 🍺

```bash
brew tap FI-153/tap
brew install wyoming-apple-stt
brew services start wyoming-apple-stt
```

> [!NOTE]
> With both installation methods the server starts at login.

## Install (Manual) 💾

If you'd rather run from source without Homebrew:

```bash
git clone https://github.com/FI-153/wyoming-apple-stt.git
cd wyoming-apple-stt
make install           # defaults: PORT=10300 LANGUAGE=en
```

## Connect to Home Assistant 🏠 

In Home Assistant: **Settings → Devices & services → Add integration → Wyoming
Protocol**. Then populate the fields with your Mac's IP and the port the server is running on (defaults to 10300).

> [!IMPORTANT]
> On the first transcription, macOS prompts for Speech Recognition permission; once approved, it never asks again.

## Configuration ⚙️

Three user-tunable settings are exposed via a small config file: `PORT` (the TCP port,
default `10300`), `LANGUAGE` (the default recognition language used when Home Assistant
doesn't specify one, default `en`), and `EXTRA_ARGS` (extra flags passed to the server,
e.g. `--debug` or `--timeout 60`, default empty):

```bash
# Default location on Apple Silicon:
cat > "$(brew --prefix)/etc/wyoming-apple-stt.conf" <<EOF
PORT=10301
LANGUAGE=it
EXTRA_ARGS="--timeout 60"
EOF
brew services restart wyoming-apple-stt
```

Logs live at `$(brew --prefix)/var/log/wyoming-apple-stt.log`.

> [!IMPORTANT]
> For the server to start on boot without user intervention you need to enable [automatic login](https://support.apple.com/en-us/102316) from the Mac's settings

## Uninstall 🗑️

```bash
brew services stop wyoming-apple-stt
brew uninstall wyoming-apple-stt
```
