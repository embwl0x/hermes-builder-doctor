---
name: builder-doctor
description: >
  Default Hermes builder guardrail skill. Use automatically for any request to
  build, create, repair, refactor, test, or verify a software app/project. It
  maps, plans, checkpoints, diagnoses, verifies, and receipts complex software
  builds so agents avoid repeated full-suite thrash and survive compaction.
version: 0.7.0
author: Hermes Builder Doctor Contributors
license: MIT
metadata:
  category: software-development
  platform: all
  tags:
    - node
    - javascript
    - typescript
    - vitest
    - swift
    - swiftpm
    - xctest
    - python
    - uv
    - pytest
    - rust
    - cargo
    - go
    - gomod
    - build
    - verification
---

# builder-doctor

Use the builder toolkit to make complex builds incremental, resumable, and verifiable.
For non-trivial software build prompts, this is the natural default workflow, even if the user does not name the skill or tools.
Builder Doctor also installs Hermes hooks that enforce this flow inside projects
marked by `.hermes-builder/state.json`.

## When to use

- At the start of any non-trivial software build or repair.
- At the start of app/project creation prompts such as "build an app", "make a project", "create a tool", "fix this project", "run tests", or "make sure it works".
- Before broad refactors in a Node/TypeScript monorepo.
- Before or during JavaScript/TypeScript package work, especially when lockfiles,
  packageManager fields, or scripts are unclear.
- Before or during SwiftPM/macOS/SwiftUI/AppKit/SpriteKit package builds.
- Before or during Python package, CLI, service, pytest, or uv-managed builds.
- Before or during Rust package, CLI, service, Tauri, Tokio, Axum, or Cargo workspace builds.
- Before or during Go module, CLI, service, Cobra, Gin/Echo/Fiber, or gRPC builds.
- After a TypeScript or test failure that looks systemic instead of one-off.
- After a Swift compiler or XCTest failure that needs a focused repair loop.
- After a Python traceback, import error, pytest failure, or pyproject/uv mismatch.
- After a Rust compiler/test failure or Cargo workspace layout issue.
- After a Go compiler/test failure, mixed-package issue, or go.mod mismatch.
- Immediately after a failed `builder_verify`, before patching from the output.
- When the agent is about to run the full test/build suite repeatedly to find an issue.
- When ESM, moduleResolution, workspace links, or vitest config is suspected.
- Before final response, so the user gets files changed, verification, and remaining limits.

## How to use

### 1. Map before editing

Call `builder_map` with `project_path` set to the repo root. Use its result as the source of truth for:
- package manager
- scripts
- framework signals
- entrypoints
- Node/TypeScript package manager, lockfiles, packageManager field, scripts, test samples, and TS file counts
- SwiftPM targets, products, imports, `@main`/`main.swift`, and XCTest samples
- Python pyproject/uv metadata, dependencies, tool configs, entrypoints, imports, and pytest samples
- Rust/Cargo package metadata, workspace members, dependencies, entrypoints, test samples, and inline test signals
- Go module metadata, imports, packages, main files, and `*_test.go` samples
- config files
- source/test file samples
- git status

Do not guess the framework or scripts when `builder_map` can tell you.

For a brand-new project path that does not exist yet:
- Create the root folder with `terminal mkdir -p` first.
- Never call `write_file` on the project root path; `write_file` is for files,
  not directories.
- Call `builder_map` on that folder before writing source/test batches.
- Call `builder_plan` before creating more than the manifest and first tiny slice.

### 2. Plan big work in phases

Call `builder_plan` before large builds. Include the user's objective in the
`objective` field, using the concrete feature nouns from the user's request.
Follow the returned phase gates:
- keep file batches small
- verify after each phase
- update resume state after each phase
- avoid unrelated rewrites

Before source edits, call `builder_acceptance` with `action: "replace"` and a
small set of criteria derived from the user's request. Every criterion needs a
unique ID, a concrete description, one or more project artifact paths, and the
exact commands that will later pass through `builder_verify`. Do not use
`.hermes-builder` state as evidence. Use `action: "update"` to replace criteria
by ID when the implementation plan changes.

Hard gate for large builds:
- After three `write_file`/`patch` calls in a phase, the hook will block more edits until you verify.
- Once `.hermes-builder/state.json` exists, Builder Doctor blocks source edits
  until `objective` is recorded. If this fires, call `builder_resume` with
  `action: "update"` and the concrete user request in `objective`.
- Before writing source in a new language lane, choose the project identity and
  keep it stable: Node package type/import style, Swift target/product names,
  Python import root, Rust crate/module names, and Go package name per
  directory.
- For super-complex prompts, build a verified kernel first: manifest/config, one or two core modules, one focused test file, then `builder_verify`.
- Treat broad systems as staged layers. Finish the current verified layer and record deferred layers in `builder_resume` / `builder_receipt` instead of attempting the whole system in one turn.
- After each source/test batch, call `builder_budget`. If it reports `over_budget: true`, do not add files; verify the current slice, receipt it, and defer the extra scope.
- When `builder_budget` reports the phase is still within budget, the next batch is still capped at two files or three write/patch calls before another `builder_budget`/`builder_verify`.
- After the first `builder_verify`, do not expand scope. Fix only verification failures.
- A compile/check-only success is not final if source exists but focused tests are missing. Add a real discovered test file and rerun the language test verifier before `builder_receipt`.
- A passing verifier is not final if `builder_budget` or `builder_receipt`
  reports an under-covered `scope_contract`. Add one small source/test batch
  for the missing objective anchors, then rerun `builder_verify`.
- When `builder_verify` fails, call `builder_failure_plan` with the failed verifier result before patching. Follow its first diagnostic and one-file repair guidance.
- If verification still fails after one focused fix pass, call `builder_failure_plan` again for the new first failure or call `builder_receipt` and report the remaining failure instead of continuing indefinitely.
- Before context grows past roughly 45k tokens, force a receipt/checkpoint instead of starting another feature pass.
- A completed vertical slice is better than an unfinished full wish list.
- A build stage is not complete until `builder_receipt` has been called after
  the final successful verification, even when the requested project is small.

Call `builder_doctor` with `project_path` set to the repo root. Use `focus` to narrow:
- `all` — everything
- `workspace` — workspace package dependency gaps
- `typescript` — tsconfig module/moduleResolution mismatch
- `esm` — NodeNext ESM import extension problems
- `package` — package.json main/types/exports mismatches
- `testing` — vitest jsdom/setup issues
- `scripts` — missing test/build/lint scripts
- `build` — stale generated output assumptions
- `node` / `javascript` — Node package-manager, lockfile, script, and config signals
- `swift` / `swiftpm` — SwiftPM target layout, entrypoints, platforms, and test structure
- `python` / `pyproject` — Python project metadata, uv/pytest layout, dependency-manager signals, and entrypoints
- `rust` / `cargo` — Cargo package/workspace metadata, entrypoints, dependencies, and tests
- `go` / `gomod` — go.mod metadata, package layout, entrypoints, and tests

Work on the findings one category at a time. Do not run a full test suite until the high-severity findings are fixed or acknowledged.

### 3. Checkpoint state

Call `builder_resume` during long builds:
- `action: "update"` after each completed phase
- record `objective`, `phase`, `completed`, `next_steps`, `decisions`, `files`, and `verification`
- keep `objective` aligned with the user request, not only the smaller slice
  you happened to implement
- `action: "read"` after compaction, interruption, or uncertainty

The state file is project-local: `.hermes-builder/state.json`.

### 4. Verify in small loops

Call `builder_verify` with the smallest command that exercises the changed code:
- Prefer `test` over `build` when validating behavior.
- If `commands` is omitted, it picks `npm run test` / `build` / `lint` automatically.
- If `Package.swift` exists, omitted `commands` runs `swift build` and `swift test`.
- If Python project files exist, omitted `commands` runs `uv run pytest` / `python3 -m pytest` when tests exist, otherwise compileall.
- If `Cargo.toml` exists, omitted `commands` runs `cargo test`.
- If `go.mod` exists, omitted `commands` runs `go test ./...`.
- If Node package scripts exist, omitted `commands` uses the detected package manager (`npm`, `pnpm`, `yarn`, or `bun`) for `test`, `build`, `lint`, `typecheck`, or `check`.
- It sets `CI=1` and `NO_COLOR=1` and tails output compactly.
- Test commands that report zero executed tests are failed checkpoints, even if
  the process exits with status 0. Add one focused test for the current kernel
  before calling the layer verified.
- JavaScript/TypeScript failures include structured compiler/file diagnostics when they can be parsed.
- Swift failures include structured compiler or XCTest diagnostics when they can be parsed.
- Python failures include structured pytest, traceback, and file/line diagnostics when they can be parsed.
- Rust failures include structured compiler and test diagnostics when they can be parsed.
- Go failures include structured compiler/test diagnostics when they can be parsed.

After each fix, run only the relevant script again. Do not run the full suite unless the change touches shared config.

After the intended verifier commands pass, call `builder_acceptance` with
`action: "read"`. Missing artifacts, unsafe paths, or verifier commands without
a successful `builder_verify` record must be resolved before final receipt.

Verification discipline:
- Once `builder_verify` has been used for a project, continue verification through `builder_verify`; do not switch to the raw terminal for the same test/build/check command.
- Use raw terminal only for bounded setup/introspection that `builder_verify` is not meant to do, such as creating the initial project folder, listing files, or reading command availability.
- When `builder_verify` succeeds, it records the verification automatically. Do not rerun the same command through raw terminal for reassurance. Call `builder_resume` only if checkpoint notes are needed, then `builder_budget` with `after_verify: true`, then `builder_receipt`.
- If `builder_verify` reports `missing_required_tests`, add the focused test phase next. Do not use `builder_receipt` as final handoff until the test verifier passes.
- If `builder_budget` reports `scope_phase_required` or `builder_receipt`
  blocks on under-covered scope, patch the current kernel toward the listed
  missing anchors and add matching tests before trying receipt again.
- If a write is blocked with `objective-required`, record the objective through
  `builder_resume` before retrying the write.
- If `builder_verify` fails, call `builder_failure_plan`, read the first structured diagnostic, patch one cause, and rerun `builder_verify` once.
- After a failed `builder_verify`, Builder Doctor hooks block write/patch repair edits until `builder_failure_plan` is called for that project.
- If `builder_verify` fails twice on the same command, call `builder_doctor` as a broader scan, then return to `builder_failure_plan` before the next patch.
- If a repair needs more than two patches before verification, stop patching and rerun `builder_verify` or produce `builder_receipt` with the remaining failure.
- If a terminal tool guardrail fires during verification, do not repeat the terminal command. Switch back to `builder_verify`, checkpoint with `builder_resume`, or finish with `builder_receipt`.
- Let `builder_verify` record successful verification automatically; use `builder_resume` for objective, phase, decisions, deferred layers, and any manual verification notes.
- If the build has gone more than one tool cycle without visible verification after several writes, shrink scope immediately instead of continuing broad file creation.
- If you feel the project needs more than 10-12 files, split it into stage 1 kernel, stage 2 hardening, and stage 3 integrations; only build the current stage.

Swift repair loop:
- Run `builder_doctor` with `focus: "swift"` when `swift build` or `swift test` fails repeatedly.
- Patch the first concrete compiler diagnostic before moving to later diagnostics.
- For XCTest failures, read the failing test and implementation, then patch behavior rather than weakening the assertion.
- Use `builder_verify` for `swift build` / `swift test`; do not repeat ad hoc terminal calls when a guardrail fires.

Python repair loop:
- Prefer `uv run pytest` for uv/pyproject projects; use `python3 -m pytest` only for simple non-uv projects.
- If no tests exist yet, verify syntax/importability with `python -m compileall -q .` or `uv run python -m compileall -q .`.
- Patch the first traceback or pytest failure before adding more features.
- Do not run `pip install`, `uv add`, `uv sync`, `poetry install`, or other dependency-mutating commands through `builder_verify`.
- For dependency changes, update `pyproject.toml` intentionally, then verify with a bounded `uv run` command.
- Do not fall back to a raw terminal pytest command after `builder_verify`; use `builder_verify` with the exact pytest command and let it return compact diagnostics.

Node/TypeScript repair loop:
- Use the package manager reported by `builder_map`; do not assume npm if pnpm, yarn, or bun lockfiles exist.
- Prefer `typecheck`, `test`, `build`, `lint`, then `check`, whichever is the smallest relevant script.
- Patch the first TypeScript/file diagnostic before adding more features.
- Do not run install/add/ci commands, dev servers, watchers, or start/serve commands through `builder_verify`.
- If lockfiles or `packageManager` disagree, fix the package-manager authority before doing feature work.

Rust/Cargo repair loop:
- Prefer `cargo test` through `builder_verify`; use narrower Cargo commands only when the project already defines them and they are bounded.
- `cargo check`, `cargo build`, and `cargo clippy` are compile checks, not final
  behavior verification. For Rust projects, Builder Doctor adds `cargo test`
  when those commands are used without a test gate.
- Targeted commands such as `cargo test test_name` are diagnostic only. Before
  `builder_receipt`, run full `cargo test` through `builder_verify`.
- Patch the first Rust compiler diagnostic before moving to later diagnostics.
- For failed tests, read the failing test and implementation, then patch behavior rather than weakening assertions.
- Do not run `cargo add`, `cargo install`, `cargo update`, or `cargo run` through `builder_verify`.
- For workspace issues, fix missing member paths or package metadata before broad source edits.

Go repair loop:
- Prefer `go test ./...` through `builder_verify`; narrow to a package only after a concrete package failure is identified.
- Patch the first Go compiler or `--- FAIL` diagnostic before adding more features.
- Keep one package name per directory; fix mixed-package directories before behavior work.
- For new Go modules, pick one package name before the first `.go` file and use
  it consistently in every `.go` file in that directory, including tests.
- If `builder_verify` reports `found packages ... and ...`, patch only package
  declarations or move files; do not rewrite behavior until `go test ./...`
  passes setup.
- Do not run `go get`, `go install`, `go run`, `go mod tidy`, `go mod download`, or `go mod vendor` through `builder_verify`.

### 5. Final receipt

Call `builder_receipt` before final response. Use it to report:
- files touched
- decisions
- completed phases
- verification commands/results
- known limitations or next steps
- acceptance criteria and their artifact/verifier proof

If `builder_receipt` warns that no verification exists, run `builder_verify` or explicitly state why verification is unavailable.
If `builder_receipt` returns `ready_to_report: false`, do not treat the stage as complete. Address `blocking_warnings` first. If tests are missing, add the focused test phase and rerun `builder_verify`; if objective scope is under-covered, add one small source/test batch for the listed missing anchors and rerun `builder_verify`; if a verifier failed, run `builder_failure_plan`, patch one cause, rerun `builder_verify`, and then call `builder_budget` with `after_verify: true`.

## Reporting rules

- Report exact file paths and commands.
- If a finding is a false positive, note it and skip it on future runs instead of rerunning the whole suite.
- If `builder_verify` times out, shrink scope (subset of tests, smaller build target) before increasing `timeout_seconds`.

## Tool contracts

### builder_map

Inputs:
- `project_path` (string, required)
- `max_files` (integer, optional, default `600`)

Returns JSON:
- `success` (bool)
- `project_path` (string)
- `summary` (string)
- `map` (object with scripts, package manager, frameworks, entrypoints, config files, tests, git status)
- `map.node` for Node/TypeScript projects (lockfiles, packageManager field, manager, scripts, deps, TS/test counts)
- `map.swift` for SwiftPM projects (targets, products, imports, entrypoints, test files)
- `map.python` for Python projects (pyproject metadata, dependencies, tools, imports, entrypoints, tests)
- `map.rust` for Rust/Cargo projects (package metadata, workspace members, deps, entrypoints, tests)
- `map.go` for Go modules (module metadata, packages, imports, entrypoints, tests)
- `recommended_next` (array of strings)

### builder_doctor

Inputs:
- `project_path` (string, required)
- `focus` (string, optional, default `all`)

Returns JSON:
- `success` (bool)
- `project_path` (string)
- `summary` (string)
- `findings` (array of objects with `severity`, `code`, `file`, `message`, `evidence`, `suggested_fix`)

### builder_budget

Inputs:
- `project_path` (string, required)
- `phase` (string, optional)
- `after_verify` (bool, optional, default `false`)
- `max_source_files` (integer, optional; if omitted, Builder Doctor uses language-specific staged-kernel defaults)
- `max_test_files` (integer, optional; if omitted, Builder Doctor uses language-specific staged-kernel defaults)
- `max_source_dirs` (integer, optional; if omitted, Builder Doctor uses language-specific staged-kernel defaults)

Returns JSON:
- `success` (bool)
- `project_path` (string)
- `phase` (string)
- `counts` (object with source file, test file, and source directory totals)
- `limits` (object with configured source/test/directory budgets)
- `over_budget` (bool)
- `hard_stop` (bool; true means do not write or patch more files before verify/receipt)
- `allowed_next_tools` (array of permitted next tool names)
- `issues` (array with budget or mixed-package warnings)
- `scope_contract` (object with saved objective anchors, matched anchors, and
  missing anchors when the stage may be too thin)
- `actions` (array of next-step guidance)
- `enforcement` (object with write counters, verify/receipt gates, and repair-patch allowance)

### builder_plan

Inputs:
- `project_path` (string, required)
- `objective` (string, optional)
- `max_phases` (integer, optional, default `7`)

Returns JSON:
- `success` (bool)
- `project_path` (string)
- `objective` (string)
- `summary` (string)
- `state_recorded` (bool; true when verification was written to `.hermes-builder/state.json`)
- `state_warning` (string)
- `project_signals` (object)
- `scope_contract` (object; when enough objective anchors exist,
  `builder_receipt` will require the verified corpus to cover a minimum subset)
- `phases` (array with phase gates)
- `rules` (array of strings)

### builder_resume

Inputs:
- `project_path` (string, required)
- `action` (string, optional, default `read`; one of `read`, `update`, `replace`, `clear`)
- `objective`, `status`, `phase` / `current_phase` (strings, optional)
- `completed`, `next_steps`, `decisions`, `files` / `files_touched`, `verification`, `notes` (arrays, optional)

Returns JSON:
- `success` (bool)
- `project_path` (string)
- `state_path` (string)
- `state_exists` (bool)
- `summary` (string)
- `state` (object)

### builder_verify

Inputs:
- `project_path` (string, required)
- `commands` (array of strings, optional)
- `timeout_seconds` (integer, optional, default 120)

Returns JSON:
- `success` (bool)
- `project_path` (string)
- `commands` (array with `command`, `exit_code`, `timed_out`, `duration_seconds`, `output_tail`)
- `failures` (array with the same fields)
- `summary` (string)

### builder_acceptance

Inputs:
- `project_path` (string, required)
- `action` (string, optional; one of `read`, `replace`, `update`, `clear`)
- `criteria` (array, required for replace/update; each item requires unique
  `id`, non-empty `description`, `evidence_paths`, and
  `verification_commands`)

Returns JSON:
- `success` (bool)
- `criteria`, `satisfied`, and `unsatisfied` (arrays)
- `all_satisfied` (bool)
- `reason` (string)
- `state_recorded` (bool)

### builder_failure_plan

Inputs:
- `project_path` (string, required)
- `verification_result` (object, optional; pass the full JSON result from `builder_verify` when available)
- `command` (string, optional)
- `output_tail` (string, optional)
- `timed_out` (bool, optional)
- `zero_tests_detected` (bool, optional)

Returns JSON:
- `success` (bool)
- `project_path` (string)
- `summary` (string first-failure summary)
- `language_profile` (string)
- `command` (string)
- `first_diagnostic` (object)
- `diagnostics` (array)
- `repair_plan` (object with `read_files`, `patch_budget`, `patch_target`, `patch_policy`, `steps`, `next_verify_command`, and `stop_conditions`)
- `recipe` (object with language-specific repair mode and steps)
- `suggested_next` (array)

### builder_receipt

Inputs:
- `project_path` (string, required)
- `verification_results` (array, optional)
- `max_files` (integer, optional, default `80`)

Returns JSON:
- `success` (bool)
- `project_path` (string)
- `state_path` (string)
- `ready_to_report` (bool)
- `blocking_warnings` (array)
- `summary` (string)
- `receipt` (object with files, decisions, verification, scripts, git status, warnings)

## Anti-patterns

- Do not run `npm install` / `pnpm install` / `bun install` through `builder_verify`.
- Do not run `pip install` / `uv add` / `uv sync` / `poetry install` through `builder_verify`.
- Do not run `cargo add` / `cargo install` / `cargo update` / `cargo run` through `builder_verify`.
- Do not run `go get` / `go install` / `go run` / `go mod tidy` / `go mod download` through `builder_verify`.
- Do not start dev servers or long-lived watchers.
- Do not rerun the full test suite after every tiny change.
- Do not use raw terminal as a duplicate substitute for a failing `builder_verify` command.
- Do not create, overwrite, delete, move, or copy source/test/config files with terminal heredocs, `tee`, shell redirection, `rm`, `cp`, `mv`, or `touch` inside a mapped project; use `write_file` or `patch`.
- Do not write a large project in one uninterrupted burst before the first verifier.
- Do not try to satisfy every requested feature in a single response when the task is explicitly complex; build a verified kernel and document next layers.
- Do not use `swift run` as verification for GUI/game apps; prefer `swift build` and `swift test`.
- Do not skip `builder_resume` during a long build.
- Do not skip `builder_acceptance` or use empty criteria to make a large build look complete.
- Do not skip `builder_receipt` before final response.
- Do not patch from a failed verifier without first calling `builder_failure_plan`.
- Do not edit project source files through `builder_map`, `builder_doctor`, `builder_plan`, or `builder_receipt`; Builder Doctor tools may write only their project-local `.hermes-builder/state.json`.
