# Hermes Builder Doctor

Generic builder guardrails for Hermes agents running local or OpenAI-compatible
models.

Current release: **0.8.9**. See [CHANGELOG.md](CHANGELOG.md) for upgrade notes.

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
- `builder_acceptance`: measurable artifact-and-verifier contract that prevents a
  thin passing test from being mistaken for completion of the requested scope.
- `builder_receipt`: final handoff summary with files, checks, and warnings.
- Hermes hooks that enforce staging inside Builder Doctor-marked projects:
  three write/patch calls trigger a verification gate before more edits can run,
  passing verification
  requires budget/receipt before more edits, failed verification allows two
  repair patches before another check, edits are anchored to the mapped project
  root, terminal commands cannot `cd` into or execute scripts from another
  project, and raw terminal verifier loops are redirected back to `builder_verify`.

Supported lanes:

- Node, JavaScript, TypeScript, package scripts, ESM, Vitest.
- SwiftPM, Swift, XCTest.
- Python, `uv`, `pytest`, `pyproject.toml`.
- Rust, Cargo.
- Go modules, including mixed-package directory detection.

## Default Build Flow

For a substantial build, the intended sequence is:

1. `builder_map` and `builder_plan` establish project facts and the objective.
2. `builder_doctor` identifies setup risks.
3. `builder_resume` saves the objective and current phase.
4. `builder_acceptance` records concrete artifact paths and exact verifier commands.
5. The model writes a small source/test batch, then calls `builder_budget`.
6. `builder_verify` runs the bounded proof command; failures go through
   `builder_failure_plan` before repair.
7. After a pass, `builder_budget(after_verify=true)` and `builder_receipt` close
   the stage.

If unchanged work is verified or receipted again, 0.8.0+ returns
`already_verified` / `already_complete` instead of rerunning the proof. The
model should follow `next_required` and answer the user rather than cycling.

In 0.8.1, replacing or updating acceptance opens a fresh evidence stage. Swift
gets a six-edit coherent checkpoint batch, and placeholder-only Swift tests no
longer satisfy handoff readiness.

In 0.8.2, a failed `builder_verify` persists a compact latest-failure record.
After context compaction, `builder_failure_plan` can recover it from only the
project path, and blocked repair edits return that exact recovery call.

In 0.8.3, verifier timeouts terminate the verifier's whole process group before
returning. This prevents test runners such as XCTest from surviving as orphaned
processes, while safely preserving partial timeout output for diagnosis.

In 0.8.4, timeout cleanup also snapshots and terminates detached descendants.
This covers XCTest runners that create their own process group before hanging.

In 0.8.5, a verified build artifact may be copied from the project's build
output into a macOS Applications folder. Terminal-based source edits and
unverified exports remain blocked.

In 0.8.6, project-root anchoring also inspects terminal working-directory
changes and directly executed script paths. A mapped build cannot silently
switch into another repository and run that repository's installer.

In 0.8.7, only `builder_verify` can establish verification proof. Manual
checkpoint summaries remain visible history but cannot unlock a receipt or
shadow a trusted passing verifier record.

## Repository Layout

```text
plugin/builder-doctor/      Hermes plugin tool implementation
skills/builder-doctor/      Skill instructions for agent behavior
examples/                   Optional generic config and soul snippets
docs/                       Installation and operating notes
tests/                      Standard-library smoke tests
CHANGELOG.md                Release and upgrade notes
```

## Quick Install

If you are driving this through a Hermes agent, you can point the agent at this
repo and ask it to do the install:

```text
Install Hermes Builder Doctor from
https://github.com/embwl0x/hermes-builder-doctor.

Clone or update the repo into a normal workspace you control, run
./scripts/install.sh --verify for this Hermes home, restart or reload the Hermes
gateway/app so the plugin is visible, then run ./scripts/verify-install.sh.
Do not change model aliases, personas, production configs, or API keys unless I
explicitly ask.
```

Clone the repository:

```bash
git clone https://github.com/embwl0x/hermes-builder-doctor.git
cd hermes-builder-doctor
```

Then install:

```bash
./scripts/install.sh --verify
```

Then restart Hermes or its gateway so the plugin and skill are reloaded.

Confirm the install:

```bash
./scripts/verify-install.sh
```

Optional: copy the ideas from `examples/` into your own Hermes config or agent
soul files. Do not paste examples blindly over an existing production config.

Start here if you are installing for the first time:

- `docs/QUICKSTART.md` — short end-to-end install and first test.
- `docs/HERMES_AGENT_SETUP.md` — custom Hermes homes, force installs with
  backups, and smaller local model prompt guidance.
- `docs/LOCAL_MODEL_PLAYBOOK.md` — operating pattern for weaker local models.

## Test

```bash
uv run --no-project python -m unittest discover -s tests
uv run --no-project python -m py_compile plugin/builder-doctor/tools.py plugin/builder-doctor/__init__.py
uv run --no-project --with pytest pytest -q tests/test_stress_harness.py
./scripts/install.sh --force --dry-run
```

The core suite uses the standard library. The stress-harness tests use an
ephemeral `pytest` environment through `uv`; they do not add project dependencies.

## Stress A Hermes Agent

After installing into a Hermes agent, you can run disposable build stress tests
against that agent:

```bash
export HERMES_BASE_URL="http://127.0.0.1:8644"  # replace with your Hermes api_server URL
export HERMES_MODEL="your-local-model-alias"    # replace with a model listed by that gateway
export API_SERVER_KEY="..."                     # only if your gateway requires it

./scripts/stress_hermes_builds.py \
  --base-url "$HERMES_BASE_URL" \
  --model "$HERMES_MODEL" \
  --tasks node,python,go
```

`127.0.0.1:8644` is only the common local default. Use the host and port from
the target agent's `platforms.api_server.extra.host` / `port` configuration, or
any remote/Tailscale URL that reaches that Hermes gateway.
`your-local-model-alias` must be the model name exposed by that gateway; the
harness no longer assumes a project-specific default model.

Use `--prompt-mode giant` to test whether an intentionally over-scoped product
prompt is converted into staged verified layers instead of a one-shot build.
Use `--prompt-mode probe` first when testing a new or smaller model: the prompts
are compact and rely on the configured Hermes build workflow instead of naming
every Builder Doctor tool explicitly.
The JSON report includes staging signals such as budget use, writes before the
first verifier, receipt use, raw terminal verifier leaks, and completion churn
(verifier/acceptance/receipt calls made after the first receipt).

Start with `--prompt-mode probe`. A healthy run independently passes its language
verifier, has no raw terminal verifier leak, uses acceptance and receipt, and
keeps `completion_churn.excess_completion_calls` close to zero.

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
- `builder_acceptance` rejects empty/vacuous criteria, duplicate IDs, evidence
  outside the project root, and evidence from Builder Doctor's own state. A
  recorded contract blocks `builder_receipt` until every artifact exists and
  every exact verifier command has a successful post-contract `builder_verify`
  record. The latest trusted `builder_verify` result wins, and evidence changes
  invalidate old proof; checkpoint summaries cannot replace verifier evidence.
- Successful unchanged verifier and receipt calls are cached as compact no-ops,
  with an explicit instruction to stop tool cycling and answer the user.
- For Rust projects, compile-only verification such as `cargo check` is paired
  with `cargo test` so a completed stage cannot receipt without the test gate.
- Targeted Rust repair commands such as `cargo test test_name` are treated as
  diagnostics; Builder Doctor requires full `cargo test` before final receipt.
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
