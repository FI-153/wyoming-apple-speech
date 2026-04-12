# Wyoming Apple STT

A Wyoming protocol STT server that bridges macOS on-device speech recognition (Apple's Speech
framework) to Home Assistant's Voice pipeline.

## Project Components

- **swift/** — Swift CLI tool that reads PCM audio from stdin and outputs transcribed text as JSON.
  Uses SpeechAnalyzer on macOS 26+ and SFSpeechRecognizer on older systems.
- **wyoming_apple_stt/** — Python Wyoming protocol server. Handles TCP connections from Home
  Assistant, accumulates audio, delegates transcription to the Swift CLI.
- **scripts/** — Install and uninstall scripts for the launchd service.

## Key References

- **Design spec:** [`context/planning/wyoming-apple-stt-design.md`](context/planning/wyoming-apple-stt-design.md) — read before making architectural changes
- **Code style:** [`context/styling/formatting.md`](context/styling/formatting.md) — naming, formatting, docstrings for Swift and Python

## Make Targets

All development commands go through the Makefile. **Always use `make` commands** instead of
invoking tools directly.

```bash
make          # Show help with all available targets
make venv     # Create Python venv and install dev dependencies
make build    # Build the Swift CLI binary
make test     # Run Python tests (creates venv if needed)
make run      # Run the server locally (builds everything first)
make install  # Install as launchd service
make uninstall # Remove the launchd service and files
make clean    # Remove build artifacts and venv
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
