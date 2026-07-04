# Hermes Builder Doctor

Generic builder guardrails for Hermes agents running local or OpenAI-compatible
models.

Local coding models can be useful builders, but they tend to fail in predictable
ways: too many files before the first test, repeated full-suite loops, weak
resume state after compaction, and language-specific setup mistakes. Builder
Doctor gives Hermes agents a small toolset and skill card that makes large
software builds incremental, resumable, and verifiable.

This repository is model-agnostic. It does not include model weights, private
Hermes configuration, API keys, session logs, or machine-specific paths.

## What It Provides

- `builder_map`: compact project facts before editing.
- `builder_plan`: phased build plan with small batches and verification gates.
- `builder_doctor`: static project risk scan for common setup mistakes.
- `builder_verify`: bounded build/test runner with compact diagnostics.
- `builder_resume`: project-local checkpoint state in `.hermes-builder/state.json`.
- `builder_receipt`: final handoff summary with files, checks, and warnings.

Supported lanes:

- Node, JavaScript, TypeScript, package scripts, ESM, Vitest.
- SwiftPM, Swift, XCTest.
- Python, `uv`, `pytest`, `pyproject.toml`.
- Rust, Cargo.
- Go modules, including mixed-package directory detection.

## Repository Layout

```text
plugin/builder-doctor/      Hermes plugin tool implementation
skills/builder-doctor/      Skill instructions for agent behavior
examples/                   Optional generic config and soul snippets
docs/                       Installation and operating notes
tests/                      Standard-library smoke tests
```

## Quick Install

Because this repository is private right now, first clone it with an authorized
GitHub account:

```bash
gh repo clone embwl0x/hermes-builder-doctor
cd hermes-builder-doctor
```

Then install:

```bash
./scripts/install.sh --verify
```

Then restart Hermes or its gateway so the plugin and skill are reloaded.

Optional: copy the ideas from `examples/` into your own Hermes config or agent
soul files. Do not paste examples blindly over an existing production config.

See `docs/HERMES_AGENT_SETUP.md` for private-repo cloning, custom Hermes homes,
force installs with backups, and smaller local model prompt guidance.

## Test

```bash
python3 -m unittest discover -s tests
python3 -m py_compile plugin/builder-doctor/tools.py plugin/builder-doctor/__init__.py
./scripts/install.sh --dry-run
```

The tests avoid external Python dependencies.

## Safety Notes

- `builder_verify` blocks install/mutation commands such as `npm install`,
  `pip install`, `cargo add`, and `go get`.
- The tools do not run dev servers, watchers, or long-lived app processes.
- Verification output is compacted and tailed to reduce context growth.
- The skill encourages staged vertical slices instead of one-shot large systems.

See `docs/SAFETY.md` for packaging and secret-hygiene notes.
