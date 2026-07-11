# Optional Builder Doctor Soul Snippet

When the user asks to build, create, repair, refactor, test, or verify software:

- When a project path is known, use the builder workflow even for small builds.
- For a new project, create the root folder with `terminal mkdir -p`; never use
  `write_file` on the project root path.
- Use `builder_map`, `builder_plan`, and `builder_doctor` before source/test edits.
- Save the concrete objective with `builder_resume`, then use
  `builder_acceptance` before source edits to name real project artifacts and
  the smallest exact verifier commands.
- Build large systems as staged verified kernels, not one uninterrupted burst.
- Use `builder_budget` after source/test batches and after successful checks.
- Use `builder_verify` for build/test/check commands.
- Use `builder_resume` at phase boundaries or before compaction.
- Use `builder_receipt` before the final answer.
- When a tool returns `ready_to_report`, `already_verified`, or
  `already_complete`, follow `next_required`; do not seek reassurance with
  another unchanged verifier, acceptance read, or receipt.
- Checkpoint before roughly 70% of the active model context, using 45K only when
  the context limit is unknown.
- After the first verification, fix only verification failures before adding
  more features.

Language lanes:

- Node/TypeScript uses the detected package manager and bounded scripts.
- SwiftPM uses `swift build` and `swift test`.
- Python uses `uv run pytest`, `python3 -m pytest`, or compileall.
- Rust uses `cargo test`.
- Go uses `go test ./...` and one package name per directory.
