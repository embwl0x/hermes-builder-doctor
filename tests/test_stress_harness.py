from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "stress_hermes_builds.py"


def load_stress_module():
    spec = importlib.util.spec_from_file_location("stress_hermes_builds_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load stress harness at {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class StressHarnessTests(unittest.TestCase):
    def test_probe_catalog_is_available_for_every_language_lane(self) -> None:
        module = load_stress_module()

        catalog = module._task_catalog("probe")

        self.assertEqual(set(catalog), {"go", "node", "python", "rust", "swift"})
        self.assertIn("configured Hermes build workflow", catalog["python"].prompt)

    def test_independent_zero_tests_detected_by_source_count(self) -> None:
        module = load_stress_module()

        self.assertTrue(
            module.independent_zero_tests_detected(
                "go test ./...",
                "?  \texample.com/empty\t[no test files]\n",
                0,
            )
        )
        self.assertFalse(
            module.independent_zero_tests_detected(
                "go test ./...",
                "ok  \texample.com/has-tests\t0.1s\n?  \texample.com/other\t[no test files]\n",
                2,
            )
        )

    def test_independent_zero_tests_ignores_empty_cargo_doc_tests_when_unit_tests_run(self) -> None:
        module = load_stress_module()

        output = """running 8 tests
test tests::test_consume_success ... ok

test result: ok. 8 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out

Doc-tests retry_budget

running 0 tests

test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
"""

        self.assertFalse(module.independent_zero_tests_detected("cargo test", output, 8))

    def test_terminal_verify_leak_detection_includes_equivalent_node_runner(self) -> None:
        module = load_stress_module()

        summary = module.summarize_events(
            [
                {
                    "event": "tool.started",
                    "tool": "terminal",
                    "preview": "node --test",
                    "timestamp": 1.0,
                }
            ],
            ("npm test",),
        )

        self.assertEqual(summary["terminal_verify_leaks"], ["node --test"])

    def test_transport_issue_and_stream_token_estimate_are_reported(self) -> None:
        module = load_stress_module()
        run = {
            "status": "failed",
            "status_payload": {"error": 'HTTP 404: {"error": "No user query found in messages."}'},
            "events": [
                {"event": "message.delta", "delta": "abcd" * 10},
                {"event": "message.delta", "delta": "efgh" * 10},
            ],
            "stream_errors": [],
        }

        self.assertTrue(module.model_transport_issue(run))
        self.assertTrue(
            module.model_transport_issue(
                {
                    "status_payload": {
                        "error": "Provider returned an empty stream with no finish_reason",
                    }
                }
            )
        )
        tokens, source = module.output_tokens_for_rate(run)

        self.assertEqual(tokens, 20)
        self.assertEqual(source, "stream_estimate")

    def test_timeout_stops_active_run_before_return(self) -> None:
        module = load_stress_module()
        stopped: list[str] = []
        polls = 0

        original_start_run = module.start_run
        original_poll_run = module.poll_run
        original_stop_run = module.stop_run
        original_stream_events = module.stream_events
        original_sleep = module.time.sleep
        try:
            module.start_run = lambda *_args, **_kwargs: "run-timeout"

            def fake_poll(*_args, **_kwargs):
                nonlocal polls
                polls += 1
                if polls == 1:
                    return {"status": "running", "usage": {}}
                return {"status": "cancelled", "usage": {}}

            module.poll_run = fake_poll

            def fake_stop(_base_url, _api_key, run_id):
                stopped.append(run_id)
                return {"stopped": True}

            module.stop_run = fake_stop
            module.stream_events = lambda *_args, **_kwargs: None
            module.time.sleep = lambda _seconds: None

            result = module.run_hermes_task(
                base_url="http://127.0.0.1:8644",
                api_key="",
                model="local-test",
                session_id="stress-test",
                prompt="hello",
                max_seconds=0,
                progress=False,
            )
        finally:
            module.start_run = original_start_run
            module.poll_run = original_poll_run
            module.stop_run = original_stop_run
            module.stream_events = original_stream_events
            module.time.sleep = original_sleep

        self.assertTrue(result["timed_out"])
        self.assertTrue(result["cleanup_safe"])
        self.assertEqual(stopped, ["run-timeout"])
        self.assertNotIn("run-timeout", module.ACTIVE_RUNS)

    def test_timeout_is_not_cleanup_safe_until_terminal_status(self) -> None:
        module = load_stress_module()

        original_start_run = module.start_run
        original_poll_run = module.poll_run
        original_stop_run = module.stop_run
        original_stream_events = module.stream_events
        original_sleep = module.time.sleep
        try:
            module.start_run = lambda *_args, **_kwargs: "run-still-running"
            module.poll_run = lambda *_args, **_kwargs: {"status": "running", "usage": {}}
            module.stop_run = lambda *_args, **_kwargs: {"error": "run not found"}
            module.stream_events = lambda *_args, **_kwargs: None
            module.time.sleep = lambda _seconds: None

            result = module.run_hermes_task(
                base_url="http://127.0.0.1:8644",
                api_key="",
                model="local-test",
                session_id="stress-test",
                prompt="hello",
                max_seconds=0,
                progress=False,
            )
        finally:
            module.start_run = original_start_run
            module.poll_run = original_poll_run
            module.stop_run = original_stop_run
            module.stream_events = original_stream_events
            module.time.sleep = original_sleep

        self.assertTrue(result["timed_out"])
        self.assertFalse(result["cleanup_safe"])
        self.assertEqual(result["status"], "running")


if __name__ == "__main__":
    unittest.main()
