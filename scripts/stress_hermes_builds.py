#!/usr/bin/env python3
"""Run disposable Hermes build stress tests against a local or remote model.

The harness starts Hermes `/v1/runs`, streams tool events, independently verifies
the generated project, optionally asks for one repair pass, and cleans up the
project directory by default.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".go",
    ".h",
    ".hpp",
    ".js",
    ".jsx",
    ".mjs",
    ".py",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
}


@dataclass(frozen=True)
class StressTask:
    name: str
    label: str
    root_name: str
    verify_commands: tuple[str, ...]
    prompt: str


ACTIVE_RUNS: dict[str, tuple[str, str]] = {}
ACTIVE_RUNS_LOCK = threading.Lock()
SHUTDOWN_HANDLERS_INSTALLED = False


def register_active_run(base_url: str, api_key: str, run_id: str) -> None:
    with ACTIVE_RUNS_LOCK:
        ACTIVE_RUNS[run_id] = (base_url, api_key)


def unregister_active_run(run_id: str) -> None:
    with ACTIVE_RUNS_LOCK:
        ACTIVE_RUNS.pop(run_id, None)


def stop_active_runs(reason: str = "shutdown") -> None:
    with ACTIVE_RUNS_LOCK:
        active = list(ACTIVE_RUNS.items())
    for run_id, (base_url, api_key) in active:
        try:
            print(f"stopping active Hermes run {run_id} ({reason})", flush=True)
            stop_run(base_url, api_key, run_id)
        finally:
            unregister_active_run(run_id)


def install_shutdown_handlers() -> None:
    global SHUTDOWN_HANDLERS_INSTALLED
    if SHUTDOWN_HANDLERS_INSTALLED:
        return
    SHUTDOWN_HANDLERS_INSTALLED = True
    atexit.register(stop_active_runs, "process-exit")

    def _handle_signal(signum: int, _frame: Any) -> None:
        stop_active_runs(f"signal-{signum}")
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)


def _task_catalog(prompt_mode: str = "kernel") -> dict[str, StressTask]:
    if prompt_mode == "probe":
        return _probe_task_catalog()
    if prompt_mode == "giant":
        return _giant_task_catalog()
    return _kernel_task_catalog()


def _probe_task_catalog() -> dict[str, StressTask]:
    return {
        "node": StressTask(
            name="node",
            label="Probe Node ESM scoring kernel",
            root_name="node_probe_scoring",
            verify_commands=("npm test",),
            prompt="""
Build a small verified Node/ESM project at {project_root}.

Use the configured Hermes build workflow naturally: create the root folder with
`terminal mkdir -p` first, never `write_file` on the project root path,
then map, plan, run a diagnostic scan, budget, verify, checkpoint, and receipt.
Keep the slice compact:
package.json with a bounded `npm test` script using Node's built-in test
runner, one scoring module, and focused tests. Implement weighted event scoring
with deterministic tie-breaking and clear invalid-input errors. Verify it and
finish with a concise handoff. Do not add external dependencies.
""",
        ),
        "swift": StressTask(
            name="swift",
            label="Probe SwiftPM cooldown kernel",
            root_name="swift_probe_cooldowns",
            verify_commands=("swift test",),
            prompt="""
Build a small verified SwiftPM project at {project_root}.

Use the configured Hermes build workflow naturally: create the root folder with
`terminal mkdir -p` first, never `write_file` on the project root path,
then map, plan, run a diagnostic scan, budget, verify, checkpoint, and receipt.
Keep the slice compact:
Package.swift with one library target, one XCTest target, a cooldown tracker
that advances deterministic turns, and focused tests for activation, expiry,
refresh, and invalid durations. Verify it and finish with a concise handoff.
Do not add external package dependencies.
""",
        ),
        "python": StressTask(
            name="python",
            label="Probe Python log classifier",
            root_name="python_probe_log_classifier",
            verify_commands=("python3 -m unittest discover -s tests",),
            prompt="""
Build a small verified Python project at {project_root}.

Use the configured Hermes build workflow naturally: create the root folder with
`terminal mkdir -p` first, never `write_file` on the project root path,
then map, plan, run a diagnostic scan, budget, verify, checkpoint, and receipt.
Keep the slice compact:
pyproject.toml metadata, one standard-library log classifier module, tests under
`tests/`, and unittest coverage for severity parsing, file/line extraction, stable
fingerprints, and malformed lines. Verify it and finish with a concise handoff.
Do not add third-party dependencies.
""",
        ),
        "rust": StressTask(
            name="rust",
            label="Probe Rust retry budget kernel",
            root_name="rust_probe_retry_budget",
            verify_commands=("cargo test",),
            prompt="""
Build a small verified Rust project at {project_root}.

Use the configured Hermes build workflow naturally: create the root folder with
`terminal mkdir -p` first, never `write_file` on the project root path,
then map, plan, run a diagnostic scan, budget, verify, checkpoint, and receipt.
Keep the slice compact:
Cargo.toml library package, a retry-budget state machine, useful Display
errors, and unit tests for consume, reset, exhaustion, and invalid budgets.
Prefer one `src/lib.rs` with inline unit tests for the first verified slice.
Verify with full `cargo test` and finish with a concise handoff. Do not add
external crates.
""",
        ),
        "go": StressTask(
            name="go",
            label="Probe Go lease clock kernel",
            root_name="go_probe_lease_clock",
            verify_commands=("go test ./...",),
            prompt="""
Build a small verified Go module at {project_root}.

Use the configured Hermes build workflow naturally: create the root folder with
`terminal mkdir -p` first, never `write_file` on the project root path,
then map, plan, run a diagnostic scan, budget, verify, checkpoint, and receipt.
Keep the slice compact:
go.mod, one package name per directory, a logical lease clock with renew,
expire, and compare behavior, plus tests for renewal, expiry, ordering, and
invalid durations. Verify it and finish with a concise handoff. Do not add
third-party dependencies.
""",
        ),
    }


def _kernel_task_catalog() -> dict[str, StressTask]:
    return {
        "node": StressTask(
            name="node",
            label="Node event-sourced policy engine",
            root_name="node_policy_engine",
            verify_commands=("npm test",),
            prompt="""
Build a substantial Node/ESM project at {project_root}.

Use Builder Doctor deliberately: create the root folder with `terminal mkdir -p`
first, never `write_file` on the project root path, then use
builder_map, builder_plan, builder_doctor, builder_budget, builder_resume,
builder_verify, builder_failure_plan after failed verification, and
builder_receipt. This is a stress test of those tools, so every relevant
builder_* tool should appear naturally.
Pass this full requested objective into builder_plan's objective field and
builder_resume's objective field so receipt can check scope coverage.
Call builder_budget again after every 3 write_file/patch calls and immediately
after successful builder_verify.

Build stage 1 of a larger event-sourced policy engine. Stage 1 must include:
- package.json using ESM and a bounded `npm test` script with Node's built-in
  test runner, no external npm dependencies.
- Core event log with append, replay, snapshot, optimistic conflict detection,
  deterministic serialization, and branch/rebase helpers.
- Policy rule compiler for simple JSON rules with AND/OR/NOT, numeric/string
  comparisons, and explainable decisions.
- Scenario simulator that applies events to rule decisions and emits a compact
  audit trail.
- Focused tests covering replay determinism, conflicts, snapshots, rule
  explanations, scenario audit output, and invalid rule handling.

Keep this as a verified kernel, not the full platform. Defer UI, persistence
adapters, distributed storage, and plugin systems in builder_resume/receipt.
After the first builder_verify call, fix only verification failures.
""",
        ),
        "swift": StressTask(
            name="swift",
            label="SwiftPM tactical simulation kernel",
            root_name="swift_tactics_kernel",
            verify_commands=("swift test",),
            prompt="""
Build a substantial SwiftPM project at {project_root}.

Use Builder Doctor deliberately: create the root folder with `terminal mkdir -p`
first, never `write_file` on the project root path, then use
builder_map, builder_plan, builder_doctor, builder_budget, builder_resume,
builder_verify, builder_failure_plan after failed verification, and
builder_receipt. This is a stress test of those tools, so every relevant
builder_* tool should appear naturally.
Pass this full requested objective into builder_plan's objective field and
builder_resume's objective field so receipt can check scope coverage.
Call builder_budget again after every 3 write_file/patch calls and immediately
after successful builder_verify.

Build stage 1 of a tactical simulation kernel. Stage 1 must include:
- Package.swift with a library target and XCTest target.
- Grid coordinate/path model with deterministic neighbor ordering.
- Entity state, action queue, initiative ordering, cooldowns, and status
  effects.
- Deterministic combat resolver with seeded pseudo-random rolls implemented
  without external dependencies.
- Replay log that can reconstruct final state from actions.
- Focused XCTest coverage for movement, initiative, cooldowns, combat
  determinism, replay, and invalid action rejection.

Keep this as a verified kernel, not a full game app. Defer UI, assets, save
files, AI enemies, and SpriteKit/SwiftUI integration in builder_resume/receipt.
After the first builder_verify call, fix only verification failures.
""",
        ),
        "python": StressTask(
            name="python",
            label="Python offline CI triage planner",
            root_name="python_ci_triage",
            verify_commands=("python3 -m unittest discover -s tests",),
            prompt="""
Build a substantial Python project at {project_root}.

Use Builder Doctor deliberately: create the root folder with `terminal mkdir -p`
first, never `write_file` on the project root path, then use
builder_map, builder_plan, builder_doctor, builder_budget, builder_resume,
builder_verify, builder_failure_plan after failed verification, and
builder_receipt. This is a stress test of those tools, so every relevant
builder_* tool should appear naturally.
Call builder_budget again after every 3 write_file/patch calls and immediately
after successful builder_verify.

Build stage 1 of an offline CI triage planner using only Python standard
library dependencies. Stage 1 must include:
- pyproject.toml metadata.
- Log parser for compiler/test output with severity, file, line, and fingerprint
  extraction.
- Failure clustering and deterministic prioritization.
- Repair plan generator that emits bounded next actions and avoids dependency
  installs.
- Checkpoint serializer/deserializer for resumable triage state.
- unittest coverage for parser edge cases, clustering, priority stability,
  repair plan output, and checkpoint round trips.

Keep this as a verified kernel, not a full service. Defer web UI, database
storage, hosted CI integrations, and LLM calls in builder_resume/receipt.
After the first builder_verify call, fix only verification failures.
""",
        ),
        "rust": StressTask(
            name="rust",
            label="Rust deterministic workflow scheduler",
            root_name="rust_workflow_scheduler",
            verify_commands=("cargo test",),
            prompt="""
Build a substantial Rust project at {project_root}.

Use Builder Doctor deliberately: create the root folder with `terminal mkdir -p`
first, never `write_file` on the project root path, then use
builder_map, builder_plan, builder_doctor, builder_budget, builder_resume,
builder_verify, builder_failure_plan after failed verification, and
builder_receipt. This is a stress test of those tools, so every relevant
builder_* tool should appear naturally.
Call builder_budget again after every 3 write_file/patch calls and immediately
after successful builder_verify.

Build stage 1 of a deterministic workflow scheduler using only Rust standard
library dependencies. Stage 1 must include:
- Cargo.toml library package.
- Keep the first verified slice compact: prefer one `src/lib.rs` plus inline
  `#[cfg(test)]` tests before splitting into multiple modules.
- DAG model with cycle detection and stable topological ordering.
- Scheduler that returns deterministic runnable batches and enforces retry
  budgets.
- Error types with useful Display output.
- Unit tests covering cycle detection, batch ordering, retry exhaustion, and
  invalid dependency handling.

Keep this as a verified kernel, not a distributed executor. Defer async runtime,
database, HTTP API, and worker pool integration in builder_resume/receipt.
After the first builder_verify call, fix only verification failures. Targeted
`cargo test name` commands are diagnostic only; final verification must include
full `cargo test` before builder_receipt.
""",
        ),
        "go": StressTask(
            name="go",
            label="Go lease and quorum simulator",
            root_name="go_quorum_simulator",
            verify_commands=("go test ./...",),
            prompt="""
Build a substantial Go module at {project_root}.

Use Builder Doctor deliberately: create the root folder with `terminal mkdir -p`
first, never `write_file` on the project root path, then use
builder_map, builder_plan, builder_doctor, builder_budget, builder_resume,
builder_verify, builder_failure_plan after failed verification, and
builder_receipt. This is a stress test of those tools, so every relevant
builder_* tool should appear naturally.
Call builder_budget again after every 3 write_file/patch calls and immediately
after successful builder_verify.

Build stage 1 of a lease and quorum simulator using only Go standard library
dependencies. Stage 1 must include:
- go.mod.
- One package name per directory, including tests.
- Node/lease model with monotonic logical clock.
- Quorum vote collection, split-brain detection, lease renewal/expiry, and
  deterministic event ordering.
- Simulator that applies scripted events and emits a stable report.
- Tests covering quorum success/failure, split-brain detection, expiry,
  renewal, report determinism, and invalid scripts.

Keep this as a verified kernel, not a networked service. Defer RPC, storage,
CLI flags, dashboards, and distributed processes in builder_resume/receipt.
After the first builder_verify call, fix only verification failures.
""",
        ),
    }


def _giant_task_catalog() -> dict[str, StressTask]:
    tool_preamble = """
Use Builder Doctor naturally and deliberately: create the root folder with
`terminal mkdir -p` first, never `write_file` on the project root path,
then use builder_map, builder_plan, builder_doctor, builder_budget,
builder_resume, builder_verify, builder_failure_plan after failed verification,
and builder_receipt. This prompt is intentionally too large for a one-shot
build. Your job is to convert it into staged verified layers. Complete the
first useful verified kernel, record the deferred layers, and stop instead of
trying to build the whole product in one burst. Call builder_budget after every
3 write_file/patch calls and immediately after successful builder_verify.
Pass this full requested objective into builder_plan's objective field and
builder_resume's objective field so receipt can check scope coverage.
"""
    return {
        "node": StressTask(
            name="node",
            label="Giant Node local-first automation studio",
            root_name="node_automation_studio",
            verify_commands=("npm test",),
            prompt=f"""
Build a complete local-first Node/ESM automation studio at {{project_root}}.

{tool_preamble}

The full product request includes: workflow DAG editor core, event-sourced run
history, rule engine, plugin runtime, encrypted secrets vault abstraction,
offline sync queue, import/export format, audit trail, CLI, HTTP API, dashboard
adapters, and deterministic test fixtures. Use no external npm dependencies.

For this run, prove the architecture with a verified kernel that meaningfully
supports the larger product and records the rest as deferred layers.
""",
        ),
        "swift": StressTask(
            name="swift",
            label="Giant Swift tactical macOS game foundation",
            root_name="swift_tactical_game_foundation",
            verify_commands=("swift test",),
            prompt=f"""
Build a complete SwiftPM foundation for a macOS tactical game at {{project_root}}.

{tool_preamble}

The full product request includes: map generation, entity/component state,
turn scheduler, combat, status effects, deterministic replay, save/load model,
AI tactics, scenario scripting, asset manifest validation, settings, campaign
progression, and future SwiftUI/SpriteKit integration. Use no external package
dependencies.

For this run, prove the game foundation with a verified kernel that can support
the larger app and records the rest as deferred layers.
""",
        ),
        "python": StressTask(
            name="python",
            label="Giant Python offline observability workbench",
            root_name="python_observability_workbench",
            verify_commands=("python3 -m unittest discover -s tests",),
            prompt=f"""
Build a complete Python offline observability workbench at {{project_root}}.

{tool_preamble}

The full product request includes: log ingestion, metric rollups, trace spans,
incident clustering, anomaly scoring, retention policy, plugin analyzers,
checkpointed repair plans, CLI commands, report rendering, and portable archive
import/export. Use only Python standard library dependencies.

For this run, prove the workbench with a verified kernel that can support the
larger product and records the rest as deferred layers.
""",
        ),
        "rust": StressTask(
            name="rust",
            label="Giant Rust workflow orchestration kernel",
            root_name="rust_orchestration_kernel",
            verify_commands=("cargo test",),
            prompt=f"""
Build a complete Rust workflow orchestration kernel at {{project_root}}.

{tool_preamble}

The full product request includes: DAG compiler, resource scheduler, retries,
cancellation propagation, lease/lock model, replay trace, durable checkpoint
format, worker protocol abstraction, policy engine, CLI boundary, and future
HTTP/runtime integration. Use only Rust standard library dependencies.

For this run, prove the orchestration kernel with a verified layer that can
support the larger product and records the rest as deferred layers. Keep the
first Rust layer to one crate and the fewest modules possible; targeted
`cargo test name` commands are diagnostic only, and final verification must
include full `cargo test` before builder_receipt.
""",
        ),
        "go": StressTask(
            name="go",
            label="Giant Go distributed systems lab",
            root_name="go_distributed_lab",
            verify_commands=("go test ./...",),
            prompt=f"""
Build a complete Go distributed systems lab at {{project_root}}.

{tool_preamble}

The full product request includes: logical clock, node state, quorum protocols,
lease simulation, partition model, deterministic event runner, invariant
checker, scenario script parser, report generator, CLI boundary, persistence
adapter boundary, and future RPC/dashboard integration. Use only Go standard
library dependencies.

For this run, prove the lab with a verified kernel that can support the larger
product and records the rest as deferred layers.
""",
        ),
    }


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def request_json(
    method: str,
    url: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code}: {detail}") from exc
    return json.loads(data) if data else {}


def stream_events(
    base_url: str,
    api_key: str,
    run_id: str,
    events: list[dict[str, Any]],
    errors: list[str],
    stop_event: threading.Event,
) -> None:
    headers = {"Accept": "text/event-stream"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(f"{base_url}/v1/runs/{run_id}/events", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            for raw_line in response:
                if stop_event.is_set():
                    return
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                events.append(event)
                if event.get("event") in {"run.completed", "run.failed", "run.cancelled"}:
                    return
    except Exception as exc:  # pragma: no cover - network behavior
        if not stop_event.is_set():
            errors.append(str(exc))


def start_run(base_url: str, api_key: str, model: str, session_id: str, prompt: str) -> str:
    response = request_json(
        "POST",
        f"{base_url}/v1/runs",
        api_key,
        {
            "model": model,
            "session_id": session_id,
            "input": prompt,
        },
    )
    run_id = response.get("run_id")
    if not run_id:
        raise RuntimeError(f"no run_id in response: {response}")
    return str(run_id)


def stop_run(base_url: str, api_key: str, run_id: str) -> dict[str, Any]:
    try:
        return request_json("POST", f"{base_url}/v1/runs/{run_id}/stop", api_key, {})
    except Exception as exc:
        return {"error": str(exc)}


def poll_run(base_url: str, api_key: str, run_id: str) -> dict[str, Any]:
    return request_json("GET", f"{base_url}/v1/runs/{run_id}", api_key, timeout=15)


def run_hermes_task(
    *,
    base_url: str,
    api_key: str,
    model: str,
    session_id: str,
    prompt: str,
    max_seconds: int,
    progress: bool,
) -> dict[str, Any]:
    started_at = time.time()
    run_id = start_run(base_url, api_key, model, session_id, prompt)
    register_active_run(base_url, api_key, run_id)
    events: list[dict[str, Any]] = []
    stream_errors: list[str] = []
    stream_stop = threading.Event()
    stream_thread = threading.Thread(
        target=stream_events,
        args=(base_url, api_key, run_id, events, stream_errors, stream_stop),
        daemon=True,
    )
    stream_thread.start()

    printed_events = 0
    timed_out = False
    final_status: dict[str, Any] = {}
    stop_result: dict[str, Any] = {}
    try:
        while True:
            elapsed = time.time() - started_at
            if progress:
                for event in events[printed_events:]:
                    printed_events += 1
                    event_name = event.get("event")
                    if event_name == "tool.started":
                        print(f"    tool {event.get('tool')}: {event.get('preview') or ''}"[:180], flush=True)
                    elif event_name in {"run.completed", "run.failed", "run.cancelled"}:
                        print(f"    {event_name}", flush=True)
            try:
                final_status = poll_run(base_url, api_key, run_id)
            except Exception as exc:
                final_status = {"status_error": str(exc)}
            status = final_status.get("status")
            if status in {"completed", "failed", "cancelled"}:
                break
            if elapsed >= max_seconds:
                timed_out = True
                stop_result = stop_run(base_url, api_key, run_id)
                for _ in range(20):
                    time.sleep(2)
                    try:
                        final_status = poll_run(base_url, api_key, run_id)
                    except Exception as exc:
                        final_status = {"status_error": str(exc)}
                    if final_status.get("status") in {"completed", "failed", "cancelled"}:
                        break
                break
            time.sleep(3)
    finally:
        if final_status.get("status") not in {"completed", "failed", "cancelled"}:
            if not stop_result:
                stop_result = stop_run(base_url, api_key, run_id)
            final_status["stop_result"] = stop_result
        elif stop_result:
            final_status["stop_result"] = stop_result
        stream_stop.set()
        stream_thread.join(timeout=3)
        unregister_active_run(run_id)
    completed_at = time.time()
    cleanup_safe = final_status.get("status") in {"completed", "failed", "cancelled"}
    return {
        "run_id": run_id,
        "session_id": session_id,
        "status": final_status.get("status", "unknown"),
        "status_payload": final_status,
        "timed_out": timed_out,
        "cleanup_safe": cleanup_safe,
        "events": events,
        "stream_errors": stream_errors,
        "wall_seconds": round(completed_at - started_at, 3),
    }


def run_command(command: str, cwd: Path, timeout: int) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=os.environ | {"CI": "1", "NO_COLOR": "1"},
        )
        output = proc.stdout or ""
        return {
            "command": command,
            "exit_code": proc.returncode,
            "timed_out": False,
            "duration_seconds": round(time.time() - started, 3),
            "output_tail": output[-5000:],
        }
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return {
            "command": command,
            "exit_code": 124,
            "timed_out": True,
            "duration_seconds": round(time.time() - started, 3),
            "output_tail": output[-5000:],
        }


def verify_project(task: StressTask, project_root: Path, timeout: int) -> dict[str, Any]:
    command_results = [run_command(command, project_root, timeout) for command in task.verify_commands]
    source_files = [
        path
        for path in project_root.rglob("*")
        if path.is_file()
        and path.suffix in SOURCE_SUFFIXES
        and "__pycache__" not in path.parts
        and ".hermes-builder" not in path.parts
    ]
    tests = count_tests(project_root)
    for item in command_results:
        item["zero_tests_detected"] = (
            item.get("exit_code") == 0
            and not item.get("timed_out")
            and independent_zero_tests_detected(
                str(item.get("command") or ""),
                str(item.get("output_tail") or ""),
                tests,
            )
        )
    return {
        "exists": project_root.exists(),
        "commands": command_results,
        "passed": bool(project_root.exists())
        and all(item["exit_code"] == 0 and not item.get("zero_tests_detected") for item in command_results),
        "source_file_count": len(source_files),
        "test_count": tests,
        "zero_tests_detected": any(item.get("zero_tests_detected") for item in command_results),
    }


def _output_reports_positive_tests(output_tail: str) -> bool:
    patterns = (
        r"(?m)^#\s*tests\s+([1-9][0-9]*)\s*$",
        r"\bRan\s+([1-9][0-9]*)\s+tests?\b",
        r"\bExecuted\s+([1-9][0-9]*)\s+tests?\b",
        r"(?m)^running\s+([1-9][0-9]*)\s+tests?\s*$",
        r"\btest result:\s+ok\.\s+([1-9][0-9]*)\s+passed\b",
        r"\b([1-9][0-9]*)\s+passed\b",
    )
    return any(re.search(pattern, output_tail, flags=re.IGNORECASE) for pattern in patterns)


def independent_zero_tests_detected(command: str, output_tail: str, test_count: int) -> bool:
    lowered_command = command.lower()
    if not any(marker in lowered_command for marker in ("test", "pytest", "unittest")):
        return False
    if test_count <= 0:
        return True
    if _output_reports_positive_tests(output_tail):
        return False
    lowered_output = output_tail.lower()
    patterns = (
        r"\bran 0 tests\b",
        r"\b0 passed\b",
        r"(?m)^\s*#?\s*tests[:\s]+0\b",
        r"\brunning 0 tests\b",
    )
    return any(re.search(pattern, lowered_output) for pattern in patterns)


def verifier_leak_needles(verify_commands: tuple[str, ...]) -> list[str]:
    needles: set[str] = {cmd.lower() for cmd in verify_commands}
    for command in verify_commands:
        lowered = command.lower()
        if "npm test" in lowered or "npm run test" in lowered:
            needles.update({"node --test", "vitest", "jest"})
        if "pnpm" in lowered and "test" in lowered:
            needles.update({"pnpm test", "pnpm run test", "vitest", "jest"})
        if "yarn" in lowered and "test" in lowered:
            needles.update({"yarn test", "vitest", "jest"})
        if "bun" in lowered and "test" in lowered:
            needles.update({"bun test"})
        if "swift test" in lowered:
            needles.add("swift test")
        if "cargo test" in lowered:
            needles.add("cargo test")
        if "go test" in lowered:
            needles.update({"go test", "go build", "go vet"})
        if "unittest" in lowered:
            needles.update({"python -m unittest", "python3 -m unittest"})
        if "pytest" in lowered:
            needles.update({"pytest", "python -m pytest", "python3 -m pytest", "uv run pytest"})
    return sorted(needles)


def count_tests(project_root: Path) -> int:
    patterns = [
        re.compile(r"\btest\s*\(", re.MULTILINE),
        re.compile(r"\bit\s*\(", re.MULTILINE),
        re.compile(r"\bfunc\s+test[A-Za-z0-9_]*\s*\(", re.MULTILINE),
        re.compile(r"\bdef\s+test_[A-Za-z0-9_]*\s*\(", re.MULTILINE),
        re.compile(r"\bfunc\s+Test[A-Za-z0-9_]*\s*\(", re.MULTILINE),
        re.compile(r"#\s*\[\s*(?:tokio::)?test\s*\]", re.MULTILINE),
    ]
    total = 0
    for path in project_root.rglob("*"):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        total += sum(len(pattern.findall(text)) for pattern in patterns)
    return total


def summarize_events(events: list[dict[str, Any]], verify_commands: tuple[str, ...]) -> dict[str, Any]:
    tool_counts: dict[str, int] = {}
    tool_errors: dict[str, int] = {}
    required = {
        "builder_map",
        "builder_doctor",
        "builder_budget",
        "builder_plan",
        "builder_resume",
        "builder_acceptance",
        "builder_verify",
        "builder_receipt",
    }
    terminal_verify_leaks: list[str] = []
    event_times: list[float] = []
    writes_or_patches = 0
    writes_before_first_verify = 0
    budget_before_first_verify = 0
    first_verify_seen = False

    verify_needles = verifier_leak_needles(verify_commands)
    for event in events:
        if isinstance(event.get("timestamp"), (int, float)):
            event_times.append(float(event["timestamp"]))
        if event.get("event") == "tool.started":
            tool = str(event.get("tool") or "")
            preview = str(event.get("preview") or "")
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
            if tool in {"write_file", "patch"}:
                writes_or_patches += 1
                if not first_verify_seen:
                    writes_before_first_verify += 1
            if tool == "builder_budget" and not first_verify_seen:
                budget_before_first_verify += 1
            if tool == "builder_verify":
                first_verify_seen = True
            if tool == "terminal" and any(needle in preview.lower() for needle in verify_needles):
                terminal_verify_leaks.append(preview[:300])
        if event.get("event") == "tool.completed" and event.get("error"):
            tool = str(event.get("tool") or "")
            tool_errors[tool] = tool_errors.get(tool, 0) + 1

    gaps = [
        max(0.0, round(event_times[i] - event_times[i - 1], 3))
        for i in range(1, len(event_times))
    ]
    return {
        "tool_counts": tool_counts,
        "tool_errors": tool_errors,
        "required_tools_missing": sorted(required.difference(tool_counts)),
        "terminal_verify_leaks": terminal_verify_leaks,
        "max_event_gap_seconds": max(gaps) if gaps else 0,
        "staging": {
            "budget_used": tool_counts.get("builder_budget", 0) > 0,
            "budget_before_first_verify": budget_before_first_verify,
            "receipt_used": tool_counts.get("builder_receipt", 0) > 0,
            "resume_used": tool_counts.get("builder_resume", 0) > 0,
            "acceptance_used": tool_counts.get("builder_acceptance", 0) > 0,
            "verify_used": tool_counts.get("builder_verify", 0) > 0,
            "writes_or_patches": writes_or_patches,
            "writes_before_first_verify": writes_before_first_verify,
        },
    }


def usage_from_run(run: dict[str, Any]) -> dict[str, int]:
    payload = run.get("status_payload") or {}
    usage = payload.get("usage") or {}
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def model_transport_issue(run: dict[str, Any]) -> bool:
    payload = json.dumps(run.get("status_payload") or {}, ensure_ascii=True)
    stream_errors = " ".join(str(item) for item in run.get("stream_errors") or [])
    combined = f"{payload}\n{stream_errors}"
    markers = (
        "No user query found in messages",
        "empty stream with no finish_reason",
        "HTTP 404",
        "run_not_found",
    )
    return any(marker in combined for marker in markers)


def estimated_output_tokens_from_stream(run: dict[str, Any]) -> int:
    text = "".join(
        str(event.get("delta") or "")
        for event in run.get("events") or []
        if event.get("event") == "message.delta"
    )
    return int(len(text) / 4) if text else 0


def output_tokens_for_rate(run: dict[str, Any]) -> tuple[int, str]:
    usage = usage_from_run(run)
    if usage["output_tokens"] > 0:
        return usage["output_tokens"], "api"
    estimated = estimated_output_tokens_from_stream(run)
    if estimated > 0:
        return estimated, "stream_estimate"
    return 0, "none"


def make_repair_prompt(project_root: Path, verification: dict[str, Any]) -> str:
    failed = [item for item in verification.get("commands", []) if item.get("exit_code") != 0]
    tail = "\n\n".join(
        f"COMMAND: {item['command']}\nEXIT: {item['exit_code']}\nTAIL:\n{item.get('output_tail','')[-2500:]}"
        for item in failed[:2]
    )
    return f"""
Repair the existing project at {project_root}.

Use Builder Doctor tools. Start with builder_map and builder_doctor, call
builder_failure_plan on the failed verifier output before patching, patch only
the first concrete verification failure, rerun builder_verify with the same
verification command, update builder_resume, call builder_budget, and finish
with builder_receipt. Do not add new features.

For Rust, targeted `cargo test some_name` commands are diagnostic only. The
final verification gate must include full `cargo test` before builder_receipt.

Independent verification failed:

{tail}
"""


def run_task(
    task: StressTask,
    *,
    workspace: Path,
    base_url: str,
    api_key: str,
    model: str,
    max_run_seconds: int,
    verify_timeout: int,
    repairs: int,
    cleanup: bool,
    progress: bool,
) -> dict[str, Any]:
    project_root = workspace / task.root_name
    project_root.parent.mkdir(parents=True, exist_ok=True)
    prompt = task.prompt.format(project_root=project_root)
    print(f"  {task.label}", flush=True)
    print(f"    project={project_root}", flush=True)
    run = run_hermes_task(
        base_url=base_url,
        api_key=api_key,
        model=model,
        session_id=f"builder-doctor-stress-{task.name}-{int(time.time())}",
        prompt=prompt,
        max_seconds=max_run_seconds,
        progress=progress,
    )
    verification = verify_project(task, project_root, verify_timeout)
    repair_runs: list[dict[str, Any]] = []

    for repair_index in range(repairs):
        if verification["passed"]:
            break
        if not project_root.exists():
            break
        print(f"    repair_pass={repair_index + 1}", flush=True)
        repair_run = run_hermes_task(
            base_url=base_url,
            api_key=api_key,
            model=model,
            session_id=f"builder-doctor-stress-{task.name}-repair-{int(time.time())}",
            prompt=make_repair_prompt(project_root, verification),
            max_seconds=max(240, int(max_run_seconds * 0.6)),
            progress=progress,
        )
        repair_runs.append(repair_run)
        verification = verify_project(task, project_root, verify_timeout)

    combined_events: list[dict[str, Any]] = list(run["events"])
    for repair_run in repair_runs:
        combined_events.extend(repair_run["events"])
    event_summary = summarize_events(combined_events, task.verify_commands)
    usage = usage_from_run(run)
    for repair_run in repair_runs:
        repair_usage = usage_from_run(repair_run)
        usage = {key: usage.get(key, 0) + repair_usage.get(key, 0) for key in usage}
    wall = run["wall_seconds"] + sum(item["wall_seconds"] for item in repair_runs)
    rate_tokens = 0
    rate_sources: list[str] = []
    for item in [run] + repair_runs:
        tokens, source = output_tokens_for_rate(item)
        rate_tokens += tokens
        if source not in rate_sources:
            rate_sources.append(source)
    output_tps = round(rate_tokens / wall, 3) if wall > 0 else 0.0
    transport_issue = model_transport_issue(run) or any(model_transport_issue(item) for item in repair_runs)

    deleted = False
    cleanup_skipped_reason = ""
    cleanup_safe = bool(run.get("cleanup_safe", False)) and all(
        bool(item.get("cleanup_safe", False)) for item in repair_runs
    )
    if cleanup and project_root.exists():
        if cleanup_safe:
            shutil.rmtree(project_root)
            deleted = True
        else:
            cleanup_skipped_reason = "Skipped deletion because at least one Hermes run did not stop cleanly."

    result = {
        "task": task.name,
        "label": task.label,
        "project_root": str(project_root),
        "run": run,
        "repair_runs": repair_runs,
        "verification": verification,
        "event_summary": event_summary,
        "usage": usage,
        "combined_wall_seconds": round(wall, 3),
        "output_tokens_per_second_wall": output_tps,
        "output_token_rate_source": "+".join(rate_sources) if rate_sources else "none",
        "model_transport_issue": transport_issue,
        "deleted": deleted,
        "cleanup_safe": cleanup_safe,
        "cleanup_skipped_reason": cleanup_skipped_reason,
    }
    print(
        "    summary="
        + json.dumps(
            {
                "status": run["status"],
                "repairs": len(repair_runs),
                "passed": verification["passed"],
                "tests": verification["test_count"],
                "files": verification["source_file_count"],
                "wall_s": result["combined_wall_seconds"],
                "out_tok_s": output_tps,
                "tok_source": result["output_token_rate_source"],
                "transport_issue": transport_issue,
                "missing_tools": event_summary["required_tools_missing"],
                "terminal_verify_leaks": bool(event_summary["terminal_verify_leaks"]),
                "staging": event_summary["staging"],
                "deleted": deleted,
                "cleanup_safe": cleanup_safe,
                "cleanup_skipped": bool(cleanup_skipped_reason),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("HERMES_BASE_URL", "http://127.0.0.1:8644"),
        help="Hermes api_server base URL. Defaults to HERMES_BASE_URL or the local default http://127.0.0.1:8644.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("HERMES_MODEL", ""),
        help="Model alias exposed by the target Hermes gateway. Can also be set with HERMES_MODEL.",
    )
    parser.add_argument("--env-file", default=os.environ.get("HERMES_ENV_FILE", str(Path.home() / ".hermes" / ".env")))
    parser.add_argument("--workspace", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--prompt-mode", choices=("probe", "kernel", "giant"), default="kernel")
    catalog = _task_catalog("kernel")
    parser.add_argument("--tasks", default=",".join(catalog), help=f"Comma-separated tasks: {', '.join(catalog)}")
    parser.add_argument("--max-run-seconds", type=int, default=900)
    parser.add_argument("--verify-timeout", type=int, default=180)
    parser.add_argument("--repairs", type=int, default=1)
    parser.add_argument("--keep-projects", action="store_true")
    parser.add_argument("--quiet-tools", action="store_true")
    args = parser.parse_args()
    if not str(args.model).strip():
        parser.error("--model is required unless HERMES_MODEL is set")
    return args


def main() -> int:
    install_shutdown_handlers()
    args = parse_args()
    env_file = Path(args.env_file).expanduser()
    load_env_file(env_file)
    api_key = os.environ.get("API_SERVER_KEY", "")
    catalog = _task_catalog(args.prompt_mode)
    selected: list[StressTask] = []
    for name in [item.strip() for item in args.tasks.split(",") if item.strip()]:
        if name not in catalog:
            raise SystemExit(f"unknown task {name!r}; choices: {', '.join(catalog)}")
        selected.append(catalog[name])

    workspace = Path(args.workspace).expanduser() if args.workspace else Path(tempfile.mkdtemp(prefix="hermes-builder-stress-"))
    workspace.mkdir(parents=True, exist_ok=True)
    output_path = Path(args.output).expanduser() if args.output else workspace / "stress-results.json"
    started = datetime.now().isoformat(timespec="seconds")
    print(f"workspace={workspace}", flush=True)
    print(f"output={output_path}", flush=True)
    print(f"model={args.model}", flush=True)

    results = []
    try:
        for task in selected:
            results.append(
                run_task(
                    task,
                    workspace=workspace,
                    base_url=args.base_url.rstrip("/"),
                    api_key=api_key,
                    model=args.model,
                    max_run_seconds=args.max_run_seconds,
                    verify_timeout=args.verify_timeout,
                    repairs=args.repairs,
                    cleanup=not args.keep_projects,
                    progress=not args.quiet_tools,
                )
            )
    finally:
        report = {
            "started_at": started,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "base_url": args.base_url,
            "model": args.model,
            "prompt_mode": args.prompt_mode,
            "workspace": str(workspace),
            "results": results,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote={output_path}", flush=True)

    failed = [item for item in results if not item.get("verification", {}).get("passed")]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
