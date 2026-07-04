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
- `builder_budget`: phase/file budget check so models stop widening scope.
- `builder_verify`: bounded build/test runner with compact diagnostics.
- `builder_failure_plan`: focused language repair plan after a failed verifier.
- `builder_resume`: project-local checkpoint state in `.hermes-builder/state.json`.
- `builder_receipt`: final handoff summary with files, checks, and warnings.
- Hermes hooks that enforce staging inside Builder Doctor-marked projects:
  three write/patch calls trigger a verification gate before more edits can run,
  passing verification
  requires budget/receipt before more edits, failed verification allows two
  repair patches before another check, edits are anchored to the mapped project
  root, and raw terminal verifier loops are redirected back to `builder_verify`.

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

## Stress A Hermes Agent

After installing into a Hermes agent, you can run disposable build stress tests
against that agent:

```bash
API_SERVER_KEY=... ./scripts/stress_hermes_builds.py \
  --base-url http://127.0.0.1:8644 \
  --model your-local-model-alias \
  --tasks node,python,go
```

Use `--prompt-mode giant` to test whether an intentionally over-scoped product
prompt is converted into staged verified layers instead of a one-shot build.
Use `--prompt-mode probe` first when testing a new or smaller model: the prompts
are compact and rely on the configured Hermes build workflow instead of naming
every Builder Doctor tool explicitly.
The JSON report includes staging signals such as budget use, writes before the
first verifier, receipt use, and raw terminal verifier leaks.

The stress harness starts Hermes `/v1/runs`, streams tool events, independently
verifies the generated projects, asks for one repair pass by default, writes a
JSON report, and deletes generated projects unless `--keep-projects` is passed.
On timeout, SIGINT, or SIGTERM it sends Hermes a stop request for active runs
and only deletes generated projects after Hermes reports a terminal run status.

## Safety Notes

- `builder_verify` blocks install/mutation commands such as `npm install`,
  `pip install`, `cargo add`, and `go get`.
- `builder_verify` treats zero-test output from test commands as a failed
  checkpoint, even when the command exits with status 0.
- `builder_failure_plan` turns verifier failures into one-file repair guidance
  before the next patch; after a failed verifier, write/patch calls are blocked
  until a failure plan is recorded.
- For Rust projects, compile-only verification such as `cargo check` is paired
  with `cargo test` so a completed stage cannot receipt without the test gate.
- After `builder_map` marks a project, Builder Doctor's hooks enforce staged
  build flow only inside that project's `.hermes-builder/state.json` boundary
  and block identifiable write/patch/terminal work outside the mapped root.
- Terminal heredoc, `tee`, redirection, `rm`, `cp`, `mv`, and `touch` source
  mutations are blocked inside mapped projects; use `write_file` or `patch` so
  guardrails can count edits.
- The tools do not run dev servers, watchers, or long-lived app processes.
- Verification output is compacted and tailed to reduce context growth.
- The skill encourages staged vertical slices instead of one-shot large systems.

See `docs/SAFETY.md` for packaging and secret-hygiene notes.
