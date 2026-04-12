# Context Folder Guide

This folder provides background knowledge, style rules, and agent plans for the wyoming-apple-stt
project. Claude Code loads this file automatically when accessing any file under `context/`.

## Folder Map

```
context/
├── knowledge/     API references, Apple docs, Wyoming protocol docs
├── styling/       Code style rules for Swift and Python
└── planning/      Design specs and implementation plans (same file per task)
```

---

## `knowledge/` — Read before writing or modifying code

- (empty — add API references, hardware docs, etc. as needed)

---

## `styling/` — Read before writing any new code

- [`formatting.md`](styling/formatting.md) — Code style rules for both the Swift CLI and the
  Python server: naming conventions, formatting, docstrings, project structure.

---

## `planning/` — Design specs and implementation plans

- [`wyoming-apple-stt-design.md`](planning/wyoming-apple-stt-design.md) — Full design spec:
  architecture, Swift CLI details, Python Wyoming server, launchd service, and distribution.
- Design and implementation plans for a given task **must** be in the same file.
- Before implementing any non-trivial task, write a plan here and wait for user approval.
- File names must be descriptive kebab-case (e.g., `implement-upload-endpoint.md`).
- Plans are kept locally and excluded from git via .gitignore.
- User review comments inside plan files are preceded by `/user`.
- When new additions to the plan are made in response to comments mark them with `/new`.
- Delete all the `/new` already present in a plan when updating it or adding the todo list.
- Always ask ANY clarifying questions you need to create a plan, avoid assumptions if not asked
  to do otherwise.
- Once a plan is approved and the user asks for implementation steps, create an implementation
  checklist in the plan file. Follow the checklist in order and check each box (`- [x]`)
  immediately upon completing the corresponding task.

**Important**: When writing a plan, include ONLY the architectural design and approach — no
implementation checklists or checkboxes. The user will ask for implementation steps separately
after approving the plan.
