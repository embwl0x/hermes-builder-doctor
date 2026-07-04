# Local Model Playbook

Builder Doctor is designed for local models that can code but need stricter
process boundaries.

## Operating Pattern

1. Create or identify the project root.
2. Run `builder_map`.
3. Run `builder_plan`.
4. Build a small verified kernel:
   - manifest or package config
   - one or two core modules
   - one focused test file
5. Run `builder_verify`.
6. Fix only the first concrete failure.
7. Save state with `builder_resume`.
8. Finish the current layer with `builder_receipt`.

## Why This Helps

Smaller local models often fail when a prompt asks for a full complex project in
one response. The staged-kernel pattern gives the model repeated feedback while
keeping context and tool output bounded.

## Recommended Prompt Shape

```text
Build this as staged verified layers. First create a minimal kernel with config,
one or two core modules, and focused tests. Use builder_map and builder_plan
before broad edits. Use builder_verify for checks. Stop after the first verified
layer and record deferred layers in builder_resume and builder_receipt.
```

## Language-Specific Rules

- Node/TypeScript: use the detected package manager and existing scripts.
- SwiftPM: verify with `swift build` and `swift test`.
- Python: prefer `uv run pytest` or compileall for no-test projects.
- Rust: verify with `cargo test`.
- Go: use one package name per directory and verify with `go test ./...`.
