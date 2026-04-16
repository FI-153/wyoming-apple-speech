# Wyoming Apple STT

A Wyoming protocol STT server that bridges macOS on-device speech recognition (Apple's Speech
framework) to Home Assistant's Voice pipeline.

## Project Components

- **swift/** — Swift CLI tool that reads PCM audio from stdin and outputs transcribed text as JSON.
  Uses SpeechAnalyzer on macOS 26+ and SFSpeechRecognizer on older systems.
  - `LocaleMatching.swift` — pure function `bestMatchingLocale(for:in:)` for resolving bare
    language codes (e.g. "en") to full locales (e.g. "en-US") against `SpeechTranscriber.supportedLocales`.
  - `SupportedLanguages.swift` — pure function `languageCodes(from:)` for extracting deduplicated,
    sorted short language codes from a list of locales. Used by `--list-languages` CLI flag.
  - `swift/Tests/AppleSTTTests/` — Swift unit tests (Swift Testing framework).
- **wyoming_apple_stt/** — Python Wyoming protocol server. Handles TCP connections from Home
  Assistant, accumulates audio, delegates transcription to the Swift CLI.
- **scripts/** — Install and uninstall scripts for the launchd service.
- **packaging/** — Release tooling: `build-release-tarball.sh` produces the
  GitHub release artifact; `formula.rb.template` + `python-resources.rb` are
  rendered by CI into the Homebrew formula.

## Key References

- **Design spec:** [`context/planning/wyoming-apple-stt-design.md`](context/planning/wyoming-apple-stt-design.md) — read before making architectural changes
- **Code style:** [`context/styling/formatting.md`](context/styling/formatting.md) — naming, formatting, docstrings for Swift and Python

## Make Targets

All development commands go through the Makefile. **Always use `make` commands** instead of
invoking tools directly.

```bash
make            # Show help with all available targets
make venv       # Create Python venv and install dev dependencies
make build      # Build the Swift CLI binary
make test       # Run Python tests (creates venv if needed)
make swift-test # Run Swift unit tests (LocaleMatching, etc.)
make run        # Run the server locally (builds everything first)
make stop       # Stop the server running on PORT
make install    # Install as launchd service
make uninstall  # Remove the launchd service and files
make clean      # Remove build artifacts and venv
```

**Important**: Never run `pytest`, `swift build`, or `pip install` directly. Always use the
corresponding `make` target. This ensures the correct venv, paths, and build flags are used.

## Planning Workflow

All plans live under `context/planning/`. The design plan and implementation plan for a given task
**must** be in the same file — design first, implementation checklist appended below after approval.

Whenever the user asks Claude to plan a task, Claude **must** write the plan as a `.md` file inside
`context/planning/` before doing any implementation work. File names must be descriptive kebab-case
(e.g., `implement-upload-endpoint.md`). After writing the plan, Claude notifies the user of the file
path and waits for explicit approval before proceeding. The user may edit the plan file directly
using `/user <comment>` annotations; When new additions to the plan are made in response to comments
mark them with `/new`; Delete all the `/new` already present in a plan when updating or adding the
todo list; implementation begins only after the user explicitly approves. Plans are kept locally
and excluded from git via .gitignore.

**Asking Questions** ALWAYS ask any clarifying questions you need and avoid assumptions unless
asked otherwise.

**Important**: When writing a plan, include ONLY the architectural design and approach — no
implementation checklists or checkboxes. The user will ask for implementation steps separately
after approving the plan. Do not add execution details, step numbering, or checkbox lists unless
the user explicitly requests them.

Once a plan is approved and the user asks for implementation steps, Claude must create an
implementation checklist in the plan file. After implementation begins, Claude must follow the
checklist in order, checking each box (`- [x]`) immediately upon completing the corresponding task.

When implementing new methods always add docstrings in accordance to the directives under
the context/styling guidelines.

**Test-Driven Development**: Always write tests before implementation code. Write a failing test
first, then write the minimal code to make it pass, then refactor. This applies to all new
Python server-side functionality. Swift CLI code is tested independently with PCM fixture files.
Use the `superpowers:test-driven-development` skill when implementing features or bugfixes.

**No Auto-Commits**: Never run `git commit`, `git push`, or any git write operations unless
the user explicitly asks. Stage and commit decisions are always the user's to make.

## Releasing

Releases are tag-driven. To cut a new release:

```bash
git tag v<major>.<minor>.<patch>
git push origin v<major>.<minor>.<patch>
```

The `.github/workflows/release.yml` workflow then:

1. Runs `make swift-test` and aborts on failure.
2. Builds the universal Swift binary via `packaging/build-release-tarball.sh` and
   assembles `wyoming-apple-stt-<version>.tar.gz`.
3. Creates a GitHub release named after the tag and uploads the tarball as the
   sole asset.
4. Renders `packaging/formula.rb.template` with the new version, URL, and sha256,
   then pushes the resulting `Formula/wyoming-apple-stt.rb` to `FI-153/homebrew-tap`.

Requirements:

- The `TAP_PUSH_TOKEN` secret must be configured in repo **Settings → Secrets and
  variables → Actions**. It is a fine-grained PAT scoped to `FI-153/homebrew-tap`
  with `contents: write`. Never commit the token to the repo.
- Python `resource` blocks live in `packaging/python-resources.rb`. When
  `pyproject.toml` runtime dependencies change, regenerate that file:

  ```bash
  python3 -m venv /tmp/poet-venv
  /tmp/poet-venv/bin/pip install homebrew-pypi-poet wyoming
  /tmp/poet-venv/bin/poet wyoming > packaging/python-resources.rb
  rm -rf /tmp/poet-venv
  ```

- No file in the repo carries a version. The git tag is the single source of truth;
  `pyproject.toml`'s `version` is static at `0.0.0` and cosmetic only.
