# Local Model Playbook

Builder Doctor is designed for local models that can code but need stricter
process boundaries.

## Operating Pattern

1. Create or identify the project root.
2. Run `builder_map`.
3. Run `builder_plan` with the concrete user request in `objective`.
4. Save the objective/phase with `builder_resume`, then record a small
   `builder_acceptance` contract with artifact paths and exact verifier commands.
5. Build a small verified kernel:
   - manifest or package config
   - one or two core modules
   - one focused test file
6. Run `builder_budget`.
7. Run `builder_verify`.
8. If verification fails, run `builder_failure_plan` before patching.
9. Fix only the first concrete failure.
10. Update `builder_resume` with completed/deferred layers when needed.
11. Run `builder_budget` again with `after_verify: true`.
12. Finish the current layer with one `builder_receipt`.

When a successful verifier is unchanged, Builder Doctor returns
`already_verified: true` instead of rerunning it. When a successful receipt is
unchanged, it returns `already_complete: true`. Treat either completion signal
literally and stop the tool loop; do not seek reassurance with another verifier
or receipt.

## Completion Protocol

- `ready_to_report: true` means the stage is complete. Answer the user.
- `already_verified: true` means the exact verifier is still current; do not
  rerun it.
- `already_complete: true` means a successful unchanged receipt already exists;
  stop calling Builder Doctor tools.
- When `ready_to_report: false`, address only `blocking_warnings`. Rerunning an
  unchanged verifier or receipt cannot fix a scope, test, or acceptance warning.
- Checkpoint before roughly 70% of the model's context limit. Use 45K only when
  the active limit is unknown.

## Why This Helps

Smaller local models often fail when a prompt asks for a full complex project in
one response. The staged-kernel pattern gives the model repeated feedback while
keeping context and tool output bounded.

## Recommended Prompt Shape

```text
Build this as staged verified layers. First create a minimal kernel with config,
one or two core modules, and focused tests. Use builder_map and builder_plan
before broad edits, passing the concrete user request as the builder_plan
objective. Use builder_budget after each source/test batch and builder_verify
for checks. Before source edits, record builder_acceptance criteria with real
project artifacts and the smallest exact verifier commands. Stop after the first
verified layer, run builder_budget with after_verify set, and record deferred
layers in builder_resume and one builder_receipt. Stop immediately on an
already_complete response.
```

## Stress-Test Lessons

- A successful `builder_verify` is enough; do not rerun the same verifier
  through a raw terminal command for reassurance.
- An unchanged duplicate `builder_verify` is cached. Treat `already_verified`
  as proof, not as a reason to invoke another verifier.
- A test command that reports zero tests is not a successful checkpoint. Add one
  focused test for the current kernel, then rerun `builder_verify`.
- A compile/vet/build command that passes while source exists but tests are
  missing opens a test phase; it is not ready for final receipt.
- A passing verifier can still be too thin. If `scope_contract` says the saved
  objective is under-covered, add one focused source/test batch for the listed
  missing anchors before receipt.
- If a source edit is blocked with `objective-required`, call `builder_resume`
  with the concrete user request in `objective`, then retry the edit.
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
- SwiftPM: verify with `swift build` and `swift test`. Keep target layout
  conventional: `Sources/<Target>/...` and `Tests/<Target>Tests/...`. If SwiftPM
  reports overlapping sources, check for missing `Tests/<TestTarget>/` before
  trying broad `path` or `exclude` changes.
- Python: prefer `uv run pytest` or unittest. Compileall is an intermediate
  syntax check for a no-test scaffold, not final proof for behavior-bearing code.
- Rust: verify with `cargo test`; compile-only checks such as `cargo check`
  are not enough to receipt a completed stage. Targeted commands such as
  `cargo test test_name` are diagnostic only; finish with full `cargo test`
  before `builder_receipt`.
- Go: use one package name per directory and verify with `go test ./...`.
## Acceptance Before Breadth

For a substantial build, record a `builder_acceptance` contract before source
edits. Keep it small and concrete: each criterion names the project artifacts
that must exist and the exact `builder_verify` commands that prove them. Read
the contract again before `builder_receipt`; an unsatisfied contract means the
current layer is not complete even if a smaller test happened to pass. Keep the
verifier set minimal: prefer one full behavior test command and, only when
needed, one separate build/typecheck command. Do not add targeted duplicates
for reassurance.
