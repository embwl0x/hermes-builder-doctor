# Install

Builder Doctor has two parts:

1. A Hermes plugin at `plugin/builder-doctor`.
2. A Hermes skill card at `skills/builder-doctor`.

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
builder_verify
builder_failure_plan
builder_receipt
```

## Optional Guidance Snippets

The files in `examples/` are not required. They are short, generic guidance
snippets that tell an agent when to reach for Builder Doctor naturally.

Use them as reference material when editing your own Hermes config or soul file.
Do not overwrite a working production config with the example snippets.
