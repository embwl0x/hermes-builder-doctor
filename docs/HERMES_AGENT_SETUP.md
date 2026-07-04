# Hermes Agent Setup

This guide is for installing Builder Doctor into another Hermes agent, including
agents backed by smaller local models.

The repository is private right now. The person installing it needs GitHub
access to the repository before they can clone it.

## 1. Download From The Private Repo

Using GitHub CLI:

```bash
gh auth login
gh repo clone embwl0x/hermes-builder-doctor
cd hermes-builder-doctor
```

Using SSH, if the account already has repo access:

```bash
git clone git@github.com:embwl0x/hermes-builder-doctor.git
cd hermes-builder-doctor
```

## 2. Install Into Hermes

Default install:

```bash
./scripts/install.sh --verify
```

Install into a custom Hermes home:

```bash
./scripts/install.sh --hermes-home "$HOME/.hermes" --verify
```

Replace an existing Builder Doctor install with backups:

```bash
./scripts/install.sh --force --verify
```

Preview without changing files:

```bash
./scripts/install.sh --dry-run
```

## 3. Restart Hermes

Restart the Hermes desktop app or gateway after install. Builder Doctor is loaded
by Hermes at startup.

After restart, confirm the `builder-doctor` toolset contains:

```text
builder_map
builder_doctor
builder_budget
builder_plan
builder_resume
builder_verify
builder_failure_plan
builder_receipt
```

Builder Doctor also registers `pre_tool_call` and `post_tool_call` hooks. The
hooks stay inactive until a project has a `.hermes-builder/state.json` marker,
which `builder_map`, `builder_budget`, `builder_verify`, `builder_resume`, and
`builder_receipt` create or update.

If the toolset is not visible, run:

```bash
./scripts/verify-install.sh
```

That confirms files are installed and compile locally. If that passes but Hermes
does not show the tools, restart Hermes again and inspect the Hermes plugin logs.

## 4. Add Optional Agent Guidance

The install works without changing model config. For better model behavior,
merge the ideas from these files into the target Hermes agent configuration:

- `examples/SOUL.append.md`
- `examples/config-snippet.yaml`

Do not overwrite an existing production config with the example file.

## Smaller Local Model Prompt

Use this prompt shape when testing smaller local coding models:

```text
Use Builder Doctor for this build. First create or identify the project root,
then run builder_map and builder_plan. Build only a staged verified kernel:
manifest/config, one or two core modules, and one focused test file. Use
builder_budget after source/test batches and builder_verify for checks. After
the first successful verification, stop adding features, call builder_resume
with deferred layers, run builder_budget with after_verify set, then call
builder_receipt. If a test command reports zero tests, add one focused test and
rerun builder_verify before calling the layer complete. If builder_verify fails,
call builder_failure_plan before patching.
```

This keeps the model out of long one-shot project generation and gives it a
small feedback loop it can actually finish.

## Recommended First Test

Ask the agent for a small but real project:

```text
Create a tiny deterministic task scheduler library with three modules and tests.
Use Builder Doctor. Stop after the first verified kernel and report deferred
next layers.
```

Expected behavior:

1. The agent maps and plans before broad edits.
2. It writes only a small first slice.
3. It checks phase size through `builder_budget`.
4. It verifies through `builder_verify`.
5. It calls `builder_failure_plan` before any repair patch if verification fails.
6. It follows hook blocks instead of forcing extra writes or raw terminal test loops.
7. It finishes with `builder_receipt`.

## Automated Stress Test

For a repeatable check, run the bundled stress harness from this repository:

```bash
./scripts/stress_hermes_builds.py \
  --base-url http://127.0.0.1:8644 \
  --model your-local-model-alias \
  --tasks node,python,go
```

If `API_SERVER_KEY` is not already exported, the harness tries to read
`$HOME/.hermes/.env`. You can also point it at a different env file:

```bash
./scripts/stress_hermes_builds.py --env-file /path/to/hermes.env
```

The harness creates disposable projects, watches Hermes tool events, runs
independent verification commands, optionally asks for one repair pass, writes a
JSON report, and deletes generated projects by default. On timeout or
interruption it stops active Hermes runs and skips deletion unless Hermes reports
a terminal run status. Use `--keep-projects` when debugging a failure.
