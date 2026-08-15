# Safety And Packaging Notes

This repository is public and is intended to stay safe to clone, fork, inspect,
and install into a Hermes agent.

It should not contain:

- API keys, OAuth tokens, or provider credentials.
- Hermes `.env` files.
- Private session logs or request dumps.
- Local model paths.
- User-specific absolute paths.
- Production persona files.
- Model weights or quantized model files.

The package includes generic tool code, skill instructions, and examples only.

The plugin stores per-project state in `.hermes-builder/state.json` inside the
project being built. That file records compact build flow metadata such as tool
gates, acceptance criteria, verification commands, evidence fingerprints,
touched files, and receipt status. Fingerprints contain paths and file metadata
(plus bounded hashes for smaller files), not artifact contents. The state file
should not store credentials, raw logs, or model transcripts.

## Runtime Boundaries

- Acceptance evidence must resolve inside the mapped project root and cannot
  use `.hermes-builder` itself as proof. Symlink escapes are rejected.
- A verifier run before the current acceptance contract does not satisfy it.
  The latest result for each exact command wins.
- Changing a named evidence artifact invalidates its old verification proof.
- Cached `already_verified` and `already_complete` responses are returned only
  when no tracked write occurred and acceptance evidence remains unchanged.
- Compact receipts omit verbose language maps by default. Set `compact: false`
  only when a human or integration explicitly needs the full map.

## Before Releases Or Pull Requests

Run:

```bash
rg -n "/Users|API_SERVER|OPENAI|ANTHROPIC|HF_|hf_|gho_|token|secret|password|private_key" .
uv run --no-project python -m unittest discover -s tests
uv run --no-project --with pytest pytest -q tests/test_stress_harness.py
uv run --no-project python -m py_compile plugin/builder-doctor/tools.py plugin/builder-doctor/__init__.py
gitleaks detect --source . --redact --no-git
```

Review every search hit manually. Some words, such as `token` or `key`, may be
normal code variables; credentials must never be present.
