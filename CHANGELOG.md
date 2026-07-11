# Changelog

All notable Builder Doctor behavior changes are recorded here.

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
