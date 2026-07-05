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

## Text-to-speech (Siri voices) 🗣️

The server also exposes the Mac's Siri voices as a Wyoming TTS service with **streaming
synthesis**: audio starts flowing to Home Assistant as soon as the first samples are
generated, sentence by sentence, instead of waiting for the whole reply. To keep latency
near zero, a synthesis engine is pre-warmed in an idle worker process at all times — when a
request arrives it starts speaking immediately while a replacement engine spins up in the
background for concurrent requests.

Only voices that macOS manages itself are offered, because they always match the system's
Siri engine:

1. Open **System Settings → Siri** (or **Accessibility → Spoken Content**) and select or
   download the Siri voice(s) you want (e.g. *Siri Voice 5 (German)*).
2. Restart the server. The voices appear automatically in Home Assistant's voice picker.

If no Siri voice is installed, the server logs a warning and runs STT-only. TTS can also be
turned off explicitly with `--no-tts` (via `EXTRA_ARGS`).

Useful `EXTRA_ARGS` flags: `--tts-voice <name>` (default voice), `--tts-rate 1.2`
(speaking speed), `--tts-idle-workers 2` (more pre-warmed engines), `--no-tts`.

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
