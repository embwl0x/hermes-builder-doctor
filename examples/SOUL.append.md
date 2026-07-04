# Optional Builder Doctor Soul Snippet

When the user asks to build, create, repair, refactor, test, or verify software:

- Use `builder_map` before broad edits when a project path is known.
- Use `builder_plan` for non-trivial work.
- For a new project, create only the root folder first, then map and plan.
- Build large systems as staged verified kernels, not one uninterrupted burst.
- Use `builder_budget` after source/test batches and after successful checks.
- Use `builder_verify` for build/test/check commands.
- Use `builder_resume` at phase boundaries or before compaction.
- Use `builder_receipt` before the final answer.
- After the first verification, fix only verification failures before adding
  more features.

Language lanes:

- Node/TypeScript uses the detected package manager and bounded scripts.
- SwiftPM uses `swift build` and `swift test`.
- Python uses `uv run pytest`, `python3 -m pytest`, or compileall.
- Rust uses `cargo test`.
- Go uses `go test ./...` and one package name per directory.
