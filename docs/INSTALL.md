# Install

Builder Doctor has two parts:

1. A Hermes plugin at `plugin/builder-doctor`.
2. A Hermes skill card at `skills/builder-doctor`.

The current plugin and skill version is 0.8.2. Upgrades replace both parts
together so tool schemas and agent guidance stay in sync.

## Ask A Hermes Agent To Install It

You can point a Hermes agent at this public repo and ask it to install Builder
Doctor for itself:

```text
Install Hermes Builder Doctor from
https://github.com/embwl0x/hermes-builder-doctor.

Clone or update the repo into a normal workspace you control. Install it into
this Hermes home with ./scripts/install.sh --verify. Restart or reload this
Hermes gateway/app so the plugin and skill are visible, then run
./scripts/verify-install.sh. Do not change model aliases, personas, production
configs, or API keys unless I explicitly ask.
```

## Manual Install

Clone the public repository:

```bash
git clone https://github.com/embwl0x/hermes-builder-doctor.git
cd hermes-builder-doctor
```

Install both parts into your Hermes home:

```bash
./scripts/install.sh --verify
```

Restart Hermes after installing. The exact command depends on your Hermes
setup; common options are restarting the desktop app or restarting the gateway
service.

For a custom Hermes home:

```bash
./scripts/install.sh --hermes-home "$HOME/.hermes" --verify
```

To replace an older Builder Doctor install:

```bash
./scripts/install.sh --force --verify
```

The installer creates timestamped backups before replacing an existing install.

Preview replacement without changing files:

```bash
./scripts/install.sh --force --dry-run
```

For the shortest first-time walkthrough, see `QUICKSTART.md`.

## Verify Registration

Ask your Hermes API or UI for available toolsets and confirm these tools exist:

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

`builder_acceptance` is required for the 0.7+ workflow. If it is missing, the
gateway is still running an older plugin or was not restarted after install.

Confirm the installed version and files:

```bash
./scripts/verify-install.sh
grep '^version:' "$HOME/.hermes/plugins/builder-doctor/plugin.yaml"
```

## Optional Guidance Snippets

The files in `examples/` are not required. They are short, generic guidance
snippets that tell an agent when to reach for Builder Doctor naturally.

Use them as reference material when editing your own Hermes config or soul file.
Do not overwrite a working production config with the example snippets.

## Upgrade Notes

- 0.7.0 added persisted acceptance contracts.
- 0.7.1 made proof ordering and evidence fingerprints authoritative.
- 0.8.0 added compact receipts and unchanged-completion caching.
- 0.8.1 reopens new acceptance stages, expands coherent Swift edit batches,
  and rejects placeholder-only Swift test coverage.
- 0.8.2 preserves the latest failed verifier across context compaction and lets
  `builder_failure_plan` recover it from the project path alone.

After any upgrade, restart Hermes. Existing project state remains readable, but
an old acceptance contract may require one fresh `builder_verify` run before it
can satisfy the stricter proof rules. See `../CHANGELOG.md` for details.
