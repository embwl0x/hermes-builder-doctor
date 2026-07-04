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
builder_plan
builder_resume
builder_verify
builder_receipt
```

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
builder_verify for checks. After the first successful verification, stop adding
features, call builder_resume with deferred layers, then call builder_receipt.
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
3. It verifies through `builder_verify`.
4. It records resume state.
5. It finishes with `builder_receipt`.
