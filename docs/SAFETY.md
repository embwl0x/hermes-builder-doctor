# Safety And Packaging Notes

This repository is intended to be safe to share or make public later.

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
gates, verification commands, touched files, and receipt status. It should not
store credentials, raw logs, or model transcripts.

## Before Publishing Publicly

Run:

```bash
rg -n "/Users|API_SERVER|OPENAI|ANTHROPIC|HF_|hf_|gho_|token|secret|password|private_key" .
python3 -m unittest discover -s tests
python3 -m py_compile plugin/builder-doctor/tools.py plugin/builder-doctor/__init__.py
```

Review every search hit manually. Some words, such as `token` or `key`, may be
normal code variables; credentials must never be present.
