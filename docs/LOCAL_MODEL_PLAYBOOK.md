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
5. Run `builder_budget`.
6. Run `builder_verify`.
7. If verification fails, run `builder_failure_plan` before patching.
8. Fix only the first concrete failure.
9. Save state with `builder_resume`.
10. Run `builder_budget` again with `after_verify: true`.
11. Finish the current layer with `builder_receipt`.

## Why This Helps

Smaller local models often fail when a prompt asks for a full complex project in
one response. The staged-kernel pattern gives the model repeated feedback while
keeping context and tool output bounded.

## Recommended Prompt Shape

```text
Build this as staged verified layers. First create a minimal kernel with config,
one or two core modules, and focused tests. Use builder_map and builder_plan
before broad edits. Use builder_budget after each source/test batch and
builder_verify for checks. Stop after the first verified layer, run
builder_budget with after_verify set, and record deferred layers in
builder_resume and builder_receipt.
```

## Stress-Test Lessons

- A successful `builder_verify` is enough; do not rerun the same verifier
  through a raw terminal command for reassurance.
- A test command that reports zero tests is not a successful checkpoint. Add one
  focused test for the current kernel, then rerun `builder_verify`.
- A failed verifier should go through `builder_failure_plan` before patching so
  the model fixes one language-specific failure instead of guessing broadly.
- If `builder_budget` says the slice is over budget, stop adding files,
  verify what exists, and defer the extra scope.
- If `builder_budget` says the slice is within budget, still cap the next batch
  at two files or three write/patch calls before another budget/verify gate.
- If a language lane starts creating many packages or directories, use
  `builder_receipt` to close the verified kernel before adding integrations.

## Language-Specific Rules

- Node/TypeScript: use the detected package manager and existing scripts.
- SwiftPM: verify with `swift build` and `swift test`.
- Python: prefer `uv run pytest` or compileall for no-test projects.
- Rust: verify with `cargo test`; compile-only checks such as `cargo check`
  are not enough to receipt a completed stage.
- Go: use one package name per directory and verify with `go test ./...`.
