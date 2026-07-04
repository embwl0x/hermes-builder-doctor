# Install

Builder Doctor has two parts:

1. A Hermes plugin at `plugin/builder-doctor`.
2. A Hermes skill card at `skills/builder-doctor`.

Install both into your Hermes home:

```bash
mkdir -p "$HOME/.hermes/plugins" "$HOME/.hermes/skills/software-development"
cp -R plugin/builder-doctor "$HOME/.hermes/plugins/builder-doctor"
cp -R skills/builder-doctor "$HOME/.hermes/skills/software-development/builder-doctor"
```

Restart Hermes after installing. The exact command depends on your Hermes
setup; common options are restarting the desktop app or restarting the gateway
service.

## Verify Registration

Ask your Hermes API or UI for available toolsets and confirm these tools exist:

```text
builder_map
builder_doctor
builder_plan
builder_resume
builder_verify
builder_receipt
```

## Optional Guidance Snippets

The files in `examples/` are not required. They are short, generic guidance
snippets that tell an agent when to reach for Builder Doctor naturally.

Use them as reference material when editing your own Hermes config or soul file.
Do not overwrite a working production config with the example snippets.
