# Changelog

All notable Builder Doctor behavior changes are recorded here.

## 0.8.8 — 2026-08-15

- Preserve acceptance contracts and their guard state when `builder_resume`
  uses `action=replace`, preventing parallel acceptance/checkpoint calls from
  silently erasing the build contract.
- Add regression coverage for the acceptance-then-resume replacement sequence
  observed in autonomous Hermes builds.

## 0.8.7 — 2026-08-15

- Make `builder_verify` the sole authority for verification proof and receipt
  guard state; `builder_resume` verification entries are now explicitly stored
  as non-authoritative checkpoint notes.
- Prevent later human-style verification summaries from shadowing trusted,
  passing verifier records during acceptance evaluation or duplicate-verifier
  caching.
- Select the latest trusted verifier record when building receipts, so verbose
  or confused local models cannot trap a green build in receipt churn.
- Add a regression reproducing the Qwen3.8 Hermes build failure: valid passing
  proof followed by a spoofed/incomplete checkpoint summary for the same
  command must still complete acceptance and receipt end to end.

## 0.8.6 — 2026-07-11

- Inspect terminal `cd` destinations and directly executed relative or absolute
  script paths as project-boundary candidates.
- Block commands such as `cd /another/project && ./script/install_app.sh` when
  the destination is outside the mapped project root.
- Preserve same-project script execution and the verified macOS artifact-export
  exception introduced in 0.8.5.

## 0.8.5 — 2026-07-11

- Allow a successfully verified artifact under a project's build-output folder
  to be copied into `/Applications` or the user's Applications folder.
- Continue blocking terminal-based source/config mutations and unverified or
  arbitrary outbound copies.
- Prefer explicit command path candidates over ambient terminal cwd when
  locating the mapped project for a terminal operation.

## 0.8.4 — 2026-07-11

- Snapshot and terminate verifier descendants directly in addition to the root
  process group, covering XCTest children that create a separate process group.
- Strengthen timeout regression coverage with a deliberately detached child.

## 0.8.3 — 2026-07-11

- Run each verifier in its own process group and terminate the entire group on
  timeout, preventing orphaned XCTest and other child processes from lingering.
- Normalize partial timeout output that Python may return as bytes, avoiding a
  secondary `TypeError` that hid the original verifier failure.
- Add a regression that launches a child process, forces a timeout, and proves
  the child is gone before `builder_verify` returns.

## 0.8.2 — 2026-07-11

- Persist a compact latest failed-verifier record in project guard state.
- Allow `builder_failure_plan` to recover the latest failure from only
  `project_path`, so local models do not need to retain a large verifier result
  across context compaction.
- Make blocked repair edits return the exact project-scoped recovery call.
- Add regression coverage for compacted-session failure-plan recovery.

## 0.8.1 — 2026-07-11

- Reopen the edit stage when `builder_acceptance` is replaced or updated, so a
  previous successful verifier cannot receipt-lock a new evidence contract.
- Allow six tracked edits per Swift checkpoint while retaining the three-edit
  cap for other language lanes.
- Treat placeholder-only Swift tests such as `XCTAssertTrue(true)` and
  `#expect(true)` as missing meaningful coverage.
- Add regressions for acceptance-stage reopening, Swift batch sizing, and
  placeholder-test rejection based on a real long-context ModelPulse run.

## 0.8.0 — 2026-07-11

- Cache unchanged successful `builder_verify` calls as `already_verified`
  instead of rerunning the command.
- Cache unchanged successful `builder_receipt` calls as `already_complete` and
  return an explicit stop-and-answer instruction.
- Make receipts compact by default; pass `compact: false` only when full
  language maps are needed.
- Add completion-churn metrics to stress reports.
- Change context checkpoint guidance from a fixed 45K tokens to roughly 70% of
  the active model limit, with 45K as the fallback when the limit is unknown.
- Add deterministic tests for completion caching, receipt invalidation after a
  new write, compact output, and harness churn accounting.

## 0.7.1 — 2026-07-11

- Require acceptance verification to occur after the current contract is saved.
- Make the latest result for each exact verifier command authoritative.
- Fingerprint acceptance evidence so artifact changes invalidate old proof.
- Reject malformed persisted contracts, symlink escapes, duplicate IDs, and
  untrusted manual verification records.

## 0.7.0 — 2026-07-11

- Add `builder_acceptance` with persisted artifact-and-verifier criteria.
- Block final receipt while recorded acceptance criteria are unsatisfied.
- Register and document the ninth Builder Doctor tool across the installer,
  skill card, and stress harness.
