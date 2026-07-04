#!/usr/bin/env python3
"""Run disposable Hermes build stress tests against a local or remote model.

The harness starts Hermes `/v1/runs`, streams tool events, independently verifies
the generated project, optionally asks for one repair pass, and cleans up the
project directory by default.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
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


def _task_catalog() -> dict[str, StressTask]:
    return {
        "node": StressTask(
            name="node",
            label="Node event-sourced policy engine",
            root_name="node_policy_engine",
            verify_commands=("npm test",),
            prompt="""
Build a substantial Node/ESM project at {project_root}.

Use Builder Doctor deliberately: create only the root folder first, then use
builder_map, builder_plan, builder_doctor, builder_budget, builder_resume,
builder_verify, and builder_receipt. This is a stress test of those tools, so
every one of those builder_* tools should appear naturally.
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

Use Builder Doctor deliberately: create only the root folder first, then use
builder_map, builder_plan, builder_doctor, builder_budget, builder_resume,
builder_verify, and builder_receipt. This is a stress test of those tools, so
every one of those builder_* tools should appear naturally.
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

Use Builder Doctor deliberately: create only the root folder first, then use
builder_map, builder_plan, builder_doctor, builder_budget, builder_resume,
builder_verify, and builder_receipt. This is a stress test of those tools, so
every one of those builder_* tools should appear naturally.
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

Use Builder Doctor deliberately: create only the root folder first, then use
builder_map, builder_plan, builder_doctor, builder_budget, builder_resume,
builder_verify, and builder_receipt. This is a stress test of those tools, so
every one of those builder_* tools should appear naturally.
Call builder_budget again after every 3 write_file/patch calls and immediately
after successful builder_verify.

Build stage 1 of a deterministic workflow scheduler using only Rust standard
library dependencies. Stage 1 must include:
- Cargo.toml library package.
- DAG model with cycle detection, stable topological ordering, resource locks,
  retry budgets, and cancellation propagation.
- Scheduler that returns deterministic runnable batches and records a replay
  trace.
- Error types with useful Display output.
- Unit tests covering cycle detection, batch ordering, locks, retries,
  cancellation propagation, replay trace stability, and invalid dependency
  handling.

Keep this as a verified kernel, not a distributed executor. Defer async runtime,
database, HTTP API, and worker pool integration in builder_resume/receipt.
After the first builder_verify call, fix only verification failures.
""",
        ),
        "go": StressTask(
            name="go",
            label="Go lease and quorum simulator",
            root_name="go_quorum_simulator",
            verify_commands=("go test ./...",),
            prompt="""
Build a substantial Go module at {project_root}.

Use Builder Doctor deliberately: create only the root folder first, then use
builder_map, builder_plan, builder_doctor, builder_budget, builder_resume,
builder_verify, and builder_receipt. This is a stress test of those tools, so
every one of those builder_* tools should appear naturally.
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
            final_status["stop_result"] = stop_run(base_url, api_key, run_id)
            time.sleep(3)
            try:
                final_status = poll_run(base_url, api_key, run_id) | {
                    "stop_result": final_status.get("stop_result")
                }
            except Exception:
                pass
            break
        time.sleep(3)

    stream_stop.set()
    stream_thread.join(timeout=3)
    completed_at = time.time()
    return {
        "run_id": run_id,
        "session_id": session_id,
        "status": final_status.get("status", "unknown"),
        "status_payload": final_status,
        "timed_out": timed_out,
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
    return {
        "exists": project_root.exists(),
        "commands": command_results,
        "passed": bool(project_root.exists()) and all(item["exit_code"] == 0 for item in command_results),
        "source_file_count": len(source_files),
        "test_count": tests,
    }


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
        "builder_verify",
        "builder_receipt",
    }
    terminal_verify_leaks: list[str] = []
    event_times: list[float] = []

    verify_needles = [cmd.lower() for cmd in verify_commands]
    for event in events:
        if isinstance(event.get("timestamp"), (int, float)):
            event_times.append(float(event["timestamp"]))
        if event.get("event") == "tool.started":
            tool = str(event.get("tool") or "")
            preview = str(event.get("preview") or "")
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
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
    }


def usage_from_run(run: dict[str, Any]) -> dict[str, int]:
    payload = run.get("status_payload") or {}
    usage = payload.get("usage") or {}
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def make_repair_prompt(project_root: Path, verification: dict[str, Any]) -> str:
    failed = [item for item in verification.get("commands", []) if item.get("exit_code") != 0]
    tail = "\n\n".join(
        f"COMMAND: {item['command']}\nEXIT: {item['exit_code']}\nTAIL:\n{item.get('output_tail','')[-2500:]}"
        for item in failed[:2]
    )
    return f"""
Repair the existing project at {project_root}.

Use Builder Doctor tools. Start with builder_map and builder_doctor, patch only
the first concrete verification failure, rerun builder_verify with the same
verification command, update builder_resume, call builder_budget, and finish
with builder_receipt. Do not add new features.

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
    output_tps = round(usage["output_tokens"] / wall, 3) if wall > 0 else 0.0

    deleted = False
    if cleanup and project_root.exists():
        shutil.rmtree(project_root)
        deleted = True

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
        "deleted": deleted,
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
                "missing_tools": event_summary["required_tools_missing"],
                "terminal_verify_leaks": bool(event_summary["terminal_verify_leaks"]),
                "deleted": deleted,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return result


def parse_args() -> argparse.Namespace:
    catalog = _task_catalog()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("HERMES_BASE_URL", "http://127.0.0.1:8644"))
    parser.add_argument("--model", default=os.environ.get("HERMES_MODEL", "step-3.7-flash"))
    parser.add_argument("--env-file", default=os.environ.get("HERMES_ENV_FILE", str(Path.home() / ".hermes" / ".env")))
    parser.add_argument("--workspace", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--tasks", default=",".join(catalog), help=f"Comma-separated tasks: {', '.join(catalog)}")
    parser.add_argument("--max-run-seconds", type=int, default=900)
    parser.add_argument("--verify-timeout", type=int, default=180)
    parser.add_argument("--repairs", type=int, default=1)
    parser.add_argument("--keep-projects", action="store_true")
    parser.add_argument("--quiet-tools", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_file = Path(args.env_file).expanduser()
    load_env_file(env_file)
    api_key = os.environ.get("API_SERVER_KEY", "")
    catalog = _task_catalog()
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
