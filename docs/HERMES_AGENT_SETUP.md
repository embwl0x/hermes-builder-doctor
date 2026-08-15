# Hermes Agent Setup

This guide is for installing Builder Doctor into another Hermes agent, including
agents backed by smaller local models.

It describes the 0.8.7 workflow. Plugin and skill versions should match.

If you just want the shortest path, use `docs/QUICKSTART.md` first.

## Ask The Agent To Do It

For a normal agent-driven install, tell the target Hermes agent:

```text
Install Hermes Builder Doctor from
https://github.com/embwl0x/hermes-builder-doctor.

Clone or update the repo into a normal workspace you control. Install it into
this Hermes home with ./scripts/install.sh --verify. Restart or reload this
Hermes gateway/app so the plugin and skill are visible, then run
./scripts/verify-install.sh. Do not change model aliases, personas, production
configs, or API keys unless I explicitly ask.
```

The agent should use its own Hermes home, gateway restart method, and available
model aliases. It should not assume the host, port, model name, or local paths
from another machine.

## 1. Download The Repository

Using GitHub CLI:

```bash
gh repo clone embwl0x/hermes-builder-doctor
cd hermes-builder-doctor
```

Using HTTPS:

```bash
git clone https://github.com/embwl0x/hermes-builder-doctor.git
cd hermes-builder-doctor
```

Using SSH:

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

Preview replacement of an existing install without changing files:

```bash
./scripts/install.sh --force --dry-run
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
builder_acceptance
builder_verify
builder_failure_plan
builder_receipt
```

Builder Doctor also registers `pre_tool_call` and `post_tool_call` hooks. The
hooks stay inactive until a project has a `.hermes-builder/state.json` marker,
which Builder Doctor tools create or update. The marker contains workflow state,
not source code or model transcripts. Once mapped, terminal commands that change
into or directly execute a path outside that project are blocked.

If the toolset is not visible, run:

```bash
./scripts/verify-install.sh
```

That confirms files are installed and compile locally. If that passes but Hermes
does not show the tools, restart Hermes again and inspect the Hermes plugin logs.

## Troubleshooting

- Toolset missing after install: restart Hermes again; plugins are loaded at
  startup.
- Existing install blocks the installer: rerun with `--force --verify`; the
  installer creates a timestamped backup.
- Custom Hermes home: pass `--hermes-home /path/to/hermes-home`.
- Agent keeps building too much at once: use the smaller local model prompt
  below and ask for one verified kernel, not the whole product.

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
save the concrete objective with builder_resume, then record builder_acceptance
criteria with project artifact paths and the smallest exact verifier command.
Create manifest/config, one or two core modules, and one focused test file. Use
builder_budget after source/test batches and builder_verify for checks. After
the first successful verification, stop adding features, run builder_budget
with after_verify set, then call builder_receipt once. If a test command reports
zero tests, add one focused test and rerun builder_verify before calling the
layer complete. If builder_verify fails, call builder_failure_plan before
patching. If any tool returns already_verified or already_complete, follow
next_required and stop the tool loop.
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
3. It records measurable acceptance criteria before source edits.
4. It checks phase size through `builder_budget`.
5. It verifies through `builder_verify`.
6. It calls `builder_failure_plan` before any repair patch if verification fails.
7. It follows hook blocks instead of forcing extra writes or raw terminal test loops.
8. It finishes with one successful `builder_receipt` and stops on the completion signal.

## Automated Stress Test

For a repeatable check, run the bundled stress harness from this repository:

```bash
export HERMES_BASE_URL="http://127.0.0.1:8644"  # replace with the target agent api_server URL
export HERMES_MODEL="your-local-model-alias"    # replace with a model listed by that gateway

./scripts/stress_hermes_builds.py \
  --base-url "$HERMES_BASE_URL" \
  --model "$HERMES_MODEL" \
  --tasks node,python,go
```

`127.0.0.1:8644` is only the common local default. For another Hermes agent,
use that agent's configured `platforms.api_server.extra.host` and `port`, or the
reachable remote URL if the gateway is exposed through SSH, Tailscale, or a
reverse proxy.
`your-local-model-alias` must be a model ID exposed by the target gateway.

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

For a new model, begin with `--prompt-mode probe`. Review independent verifier
status, missing tools, raw terminal verifier leaks, and
`event_summary.completion_churn`. The churn count distinguishes a model that
finishes cleanly from one that repeatedly seeks reassurance after receipt.
