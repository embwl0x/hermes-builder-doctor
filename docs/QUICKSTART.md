# Quickstart

This is the shortest path to install Builder Doctor into a Hermes agent and run
one small verification.

This guide targets Builder Doctor 0.8.2 or newer.

## What You Need

- A working Hermes install.
- Python 3.11 or newer.
- `git`.
- A Hermes agent or gateway to restart after install.

No model weights, API keys, personas, or private Hermes configs are included in
this repository.

## Agent-Driven Install

If you are using a Hermes agent to install this for you, give it this prompt:

```text
Install Hermes Builder Doctor from
https://github.com/embwl0x/hermes-builder-doctor.

Clone or update the repo into a normal workspace you control. Install it into
this Hermes home with ./scripts/install.sh --verify. Restart or reload this
Hermes gateway/app so the plugin and skill are visible, then run
./scripts/verify-install.sh. Do not change model aliases, personas, production
configs, or API keys unless I explicitly ask.
```

## 1. Clone

```bash
git clone https://github.com/embwl0x/hermes-builder-doctor.git
cd hermes-builder-doctor
```

GitHub CLI also works:

```bash
gh repo clone embwl0x/hermes-builder-doctor
cd hermes-builder-doctor
```

## 2. Install

Default Hermes home:

```bash
./scripts/install.sh --verify
```

Custom Hermes home:

```bash
./scripts/install.sh --hermes-home "$HOME/.hermes" --verify
```

Replacing an existing install:

```bash
./scripts/install.sh --force --verify
```

The installer copies:

- `plugin/builder-doctor` to your Hermes plugins directory.
- `skills/builder-doctor` to your Hermes software-development skills directory.

## 3. Restart Hermes

Restart your Hermes desktop app, gateway, or service after installing. Builder
Doctor is loaded at startup.

After restart, the toolset should include:

```text
builder_map
builder_doctor
builder_budget
builder_plan
builder_resume
builder_acceptance
builder_verify
builder_failure_plan
builder_receipt
```

## 4. Add Optional Guidance

The install works without changing your model. For better behavior, merge the
ideas from these files into your agent guidance:

- `examples/SOUL.append.md`
- `examples/config-snippet.yaml`

Do not overwrite a working Hermes config. Treat the examples as copy/paste
snippets to adapt.

## 5. First Test Prompt

Give your Hermes agent a small build first:

```text
Build a tiny deterministic task scheduler library in a new temporary project.
Use Builder Doctor naturally. Create the project root, run builder_map and
builder_plan, save the objective with builder_resume, and record a small
builder_acceptance contract before source edits. Build only one verified kernel
with one or two source files and focused tests, run builder_budget and
builder_verify, repair with builder_failure_plan if needed, then finish with
builder_budget(after_verify=true) and one builder_receipt. Defer extra features.
```

Expected behavior:

1. The agent maps and plans before broad edits.
2. It records acceptance criteria before source edits.
3. It builds a small first slice.
4. It verifies through `builder_verify`, not repeated raw terminal test loops.
5. It calls `builder_failure_plan` before repair patches.
6. It finishes with one successful `builder_receipt` and stops when
   `ready_to_report` or `already_complete` is true.

## 6. Optional Stress Test

After the tools are visible in Hermes, run disposable stress tests:

```bash
export HERMES_BASE_URL="http://127.0.0.1:8644"  # replace with your Hermes api_server URL
export HERMES_MODEL="your-local-model-alias"    # replace with a model listed by that gateway

./scripts/stress_hermes_builds.py \
  --base-url "$HERMES_BASE_URL" \
  --model "$HERMES_MODEL" \
  --prompt-mode probe \
  --tasks node,python,go
```

`127.0.0.1:8644` is only a local default. If your Hermes gateway is configured
on another host or port, use that URL instead.
`your-local-model-alias` must be the model ID exposed by that gateway.

The harness creates temporary projects, watches Hermes tool events,
independently verifies the generated code, writes a JSON report, and deletes
the projects by default.

Inspect `event_summary.completion_churn` in the report. Repeated verifier,
acceptance, or receipt calls after the first receipt indicate model endgame
churn; 0.8.0 turns unchanged repeats into compact no-ops.

Use `--keep-projects` only when debugging a failed generated project.

## Common Fixes

- Tools not visible: restart Hermes again.
- Existing install blocks install: use `./scripts/install.sh --force --verify`.
- Agent writes too many files before testing: ask for one staged verified
  kernel and use `--prompt-mode probe` in the stress harness.
- Rust repairs only a targeted test: Builder Doctor requires full `cargo test`
  before the stage can receipt.
- Agent keeps verifying after a successful receipt: confirm 0.8.0 is installed,
  then follow `already_verified`, `already_complete`, and `next_required`
  literally instead of calling another Builder Doctor tool.
