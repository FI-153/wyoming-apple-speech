# Wyoming Apple STT

On-device speech-to-text for Home Assistant, powered by Apple's Speech framework.
Every word stays on your Mac: no cloud, no API key, no usage limits, full privacy.

## Requirements

- macOS 15 (Sequoia) or later.
- [Home Assistant](https://www.home-assistant.io) with the [Wyoming](https://www.home-assistant.io/integrations/wyoming/) integration.

**Which speech engine runs depends on your macOS version:**

- **macOS 26 (Tahoe) or later** — uses Apple's new [**SpeechAnalyzer**](https://developer.apple.com/documentation/speech/speechanalyzer) API.
  Language models download on-demand the first time a given language is used.
- **macOS 15 (Sequoia) through 25** — falls back to the legacy
  [**SFSpeechRecognizer**](https://developer.apple.com/documentation/speech/sfspeechrecognizer) API. Uses whichever speech-recognition locales are already
  installed via **System Settings → General → Language & Region → Dictation**
  (or that macOS has pre-downloaded).

Both engines run fully on-device. SpeechAnalyzer is faster and more precise.

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
Protocol**. The populate the fields with your mac's IP and the port the server is running on (defaults to 10300)

> [!IMPORTANT]
> On the first transcription, macOS prompts for Speech Recognition permission, one approved it never asks again.

## Configuration ⚙️

The only user-tunable setting is the TCP port, exposed via a small config file:

```bash
# Default location on Apple Silicon:
echo "PORT=10301" > "$(brew --prefix)/etc/wyoming-apple-stt.conf"
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
