from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS_PATH = ROOT / "plugin" / "builder-doctor" / "tools.py"


def load_tools_module():
    spec = importlib.util.spec_from_file_location("builder_doctor_tools", TOOLS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load tools module at {TOOLS_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuilderDoctorToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tools = load_tools_module()

    def test_go_map_reports_mixed_package_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "go.mod").write_text("module example.com/mixed\n\ngo 1.22\n", encoding="utf-8")
            (root / "a.go").write_text("package alpha\n\nfunc A() int { return 1 }\n", encoding="utf-8")
            (root / "b.go").write_text("package beta\n\nfunc B() int { return 2 }\n", encoding="utf-8")

            result = json.loads(self.tools.builder_map({"project_path": str(root)}))

        self.assertTrue(result["success"])
        self.assertEqual(result["map"]["go"]["mixed_package_dirs"], {".": ["alpha", "beta"]})

    def test_go_failure_guidance_parses_found_packages(self) -> None:
        output = (
            "FAIL\texample.com/mixed [setup failed]\n"
            "# example.com/mixed\n"
            "found packages alpha (a.go) and beta (b.go) in /tmp/mixed\n"
            "FAIL\n"
        )
        result = self.tools._failure_guidance("go test ./...", output)

        diagnostics = result["diagnostics"]
        self.assertEqual(diagnostics[0]["kind"], "go-mixed-packages")
        self.assertEqual(diagnostics[0]["packages"], ["alpha", "beta"])
        self.assertIn("one package name", diagnostics[0]["message"])
        self.assertTrue(any("package declarations" in item for item in result["suggested_next"]))

    def test_builder_budget_flags_wide_kernel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "go.mod").write_text("module example.com/wide\n\ngo 1.22\n", encoding="utf-8")
            for index in range(5):
                pkg = root / "internal" / f"pkg{index}"
                pkg.mkdir(parents=True)
                (pkg / f"pkg{index}.go").write_text(
                    f"package pkg{index}\n\nfunc Value() int {{ return {index} }}\n",
                    encoding="utf-8",
                )
            (root / "wide_test.go").write_text(
                "package wide\n\nimport \"testing\"\n\nfunc TestWide(t *testing.T) {}\n",
                encoding="utf-8",
            )

            result = json.loads(
                self.tools.builder_budget(
                    {
                        "project_path": str(root),
                        "phase": "stage 1",
                        "max_source_files": 3,
                        "max_source_dirs": 3,
                    }
                )
            )

        self.assertTrue(result["success"])
        self.assertTrue(result["over_budget"])
        self.assertTrue(result["hard_stop"])
        self.assertEqual(
            result["allowed_next_tools"],
            ["builder_verify", "builder_resume", "builder_receipt"],
        )
        codes = {issue["code"] for issue in result["issues"]}
        self.assertIn("source-file-budget-exceeded", codes)
        self.assertIn("source-dir-budget-exceeded", codes)
        self.assertTrue(any("stop adding" in action.lower() for action in result["actions"]))

    def test_builder_resume_nudges_receipt_after_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = json.loads(
                self.tools.builder_resume(
                    {
                        "project_path": str(root),
                        "action": "update",
                        "verification": [{"command": "npm test", "exit_code": 0}],
                    }
                )
            )

        self.assertTrue(result["success"])
        self.assertTrue(any("builder_budget" in item for item in result["next_required"]))
        self.assertTrue(any("builder_receipt" in item for item in result["next_required"]))
        self.assertTrue(any("do not write" in item.lower() for item in result["next_required"]))

    def test_builder_map_marks_project_and_write_gate_blocks_fourth_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            map_result = json.loads(self.tools.builder_map({"project_path": str(root)}))
            self.assertTrue(map_result["state_recorded"])

            for index in range(3):
                allow = self.tools.builder_pre_tool_call(
                    tool_name="write_file",
                    args={"path": str(root / f"file{index}.py")},
                )
                self.assertIsNone(allow)

            block = self.tools.builder_pre_tool_call(
                tool_name="write_file",
                args={"path": str(root / "file3.py")},
            )

        self.assertIsNotNone(block)
        self.assertEqual(block["action"], "block")
        self.assertIn("write budget", block["message"])

    def test_builder_budget_hard_stops_at_language_source_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "go.mod").write_text("module example.com/cap\n\ngo 1.22\n", encoding="utf-8")
            for index in range(5):
                (root / f"file{index}.go").write_text("package cap\n", encoding="utf-8")
            self.tools.builder_map({"project_path": str(root)})

            result = json.loads(self.tools.builder_budget({"project_path": str(root)}))
            state = json.loads((root / ".hermes-builder" / "state.json").read_text(encoding="utf-8"))

        self.assertTrue(result["over_budget"])
        self.assertTrue(result["hard_stop"])
        self.assertIn("builder_verify", result["allowed_next_tools"])
        self.assertTrue(state["guard"]["verify_required"])

    def test_builder_verify_success_requires_receipt_and_blocks_raw_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text("print('ok')\n", encoding="utf-8")
            self.tools.builder_map({"project_path": str(root)})

            verify = json.loads(
                self.tools.builder_verify(
                    {
                        "project_path": str(root),
                        "commands": ["python3 -m compileall -q ."],
                    }
                )
            )
            state = json.loads((root / ".hermes-builder" / "state.json").read_text(encoding="utf-8"))
            budget = json.loads(
                self.tools.builder_budget(
                    {
                        "project_path": str(root),
                        "after_verify": True,
                    }
                )
            )
            write_block = self.tools.builder_pre_tool_call(
                tool_name="write_file",
                args={"path": str(root / "extra.py")},
            )
            terminal_block = self.tools.builder_pre_tool_call(
                tool_name="terminal",
                args={"command": "python3 -m compileall -q .", "workdir": str(root)},
            )
            node_terminal_block = self.tools.builder_pre_tool_call(
                tool_name="terminal",
                args={"command": "node --test", "workdir": str(root)},
            )
            go_terminal_block = self.tools.builder_pre_tool_call(
                tool_name="terminal",
                args={"command": "go build ./...", "workdir": str(root)},
            )

            receipt = json.loads(self.tools.builder_receipt({"project_path": str(root)}))
            write_after_receipt = self.tools.builder_pre_tool_call(
                tool_name="write_file",
                args={"path": str(root / "next_stage.py")},
            )

        self.assertTrue(verify["success"])
        self.assertTrue(verify["state_recorded"])
        self.assertTrue(budget["success"])
        self.assertTrue(state["guard"]["receipt_required"])
        self.assertIsNotNone(write_block)
        self.assertIn("builder_verify has already passed", write_block["message"])
        self.assertIsNotNone(terminal_block)
        self.assertIn("raw terminal verifier", terminal_block["message"])
        self.assertIsNotNone(node_terminal_block)
        self.assertIn("raw terminal verifier", node_terminal_block["message"])
        self.assertIsNotNone(go_terminal_block)
        self.assertIn("raw terminal verifier", go_terminal_block["message"])
        self.assertTrue(receipt["state_recorded"])
        self.assertIsNone(write_after_receipt)

    def test_terminal_raw_verifier_is_blocked_even_without_root_detection(self) -> None:
        build_block = self.tools.builder_pre_tool_call(
            tool_name="terminal",
            args={"command": "go build ./..."},
        )
        tidy_block = self.tools.builder_pre_tool_call(
            tool_name="terminal",
            args={"command": "go mod tidy"},
        )

        self.assertIsNotNone(build_block)
        self.assertIn("raw terminal verifier", build_block["message"])
        self.assertIsNotNone(tidy_block)
        self.assertIn("terminal file mutation", tidy_block["message"])

    def test_builder_budget_after_passing_verify_requires_receipt_even_without_after_verify_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text("print('ok')\n", encoding="utf-8")
            self.tools.builder_map({"project_path": str(root)})
            self.tools.builder_verify(
                {
                    "project_path": str(root),
                    "commands": ["python3 -m compileall -q ."],
                }
            )

            budget = json.loads(self.tools.builder_budget({"project_path": str(root)}))
            state = json.loads((root / ".hermes-builder" / "state.json").read_text(encoding="utf-8"))

        self.assertEqual(budget["allowed_next_tools"], ["builder_resume", "builder_receipt"])
        self.assertTrue(state["guard"]["receipt_required"])
        self.assertTrue(any("builder_receipt" in action for action in budget["actions"]))

    def test_builder_budget_does_not_reprompt_receipt_after_current_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text("print('ok')\n", encoding="utf-8")
            self.tools.builder_map({"project_path": str(root)})
            self.tools.builder_verify(
                {
                    "project_path": str(root),
                    "commands": ["python3 -m compileall -q ."],
                }
            )
            self.tools.builder_budget({"project_path": str(root), "after_verify": True})
            self.tools.builder_receipt({"project_path": str(root)})

            budget = json.loads(self.tools.builder_budget({"project_path": str(root)}))

        self.assertEqual(
            budget["allowed_next_tools"],
            ["write_file", "patch", "builder_budget", "builder_verify"],
        )
        self.assertFalse(any("builder_receipt now" in action for action in budget["actions"]))

    def test_failed_verify_allows_two_repair_patches_then_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.tools.builder_map({"project_path": str(root)})
            verify = json.loads(
                self.tools.builder_verify(
                    {
                        "project_path": str(root),
                        "commands": ["python3 -c 'import sys; sys.exit(1)'"],
                    }
                )
            )
            blocked_before_plan = self.tools.builder_pre_tool_call(
                tool_name="patch",
                args={"path": str(root / "repair.py")},
            )
            plan = json.loads(
                self.tools.builder_failure_plan(
                    {
                        "project_path": str(root),
                        "verification_result": verify,
                    }
                )
            )
            first = self.tools.builder_pre_tool_call(
                tool_name="patch",
                args={"path": str(root / "repair.py")},
            )
            second = self.tools.builder_pre_tool_call(
                tool_name="patch",
                args={"path": str(root / "repair.py")},
            )
            third = self.tools.builder_pre_tool_call(
                tool_name="patch",
                args={"path": str(root / "repair.py")},
            )

        self.assertFalse(verify["success"])
        self.assertIsNotNone(blocked_before_plan)
        self.assertIn("builder_failure_plan", blocked_before_plan["message"])
        self.assertTrue(plan["success"])
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertIsNotNone(third)
        self.assertIn("smallest relevant command", third["message"])

    def test_verify_blocks_dependency_mutation_commands(self) -> None:
        blocked = [
            "npm install",
            "pnpm add react",
            "python3 -m pip install pytest",
            "cargo add anyhow",
            "go get example.com/pkg",
        ]
        for command in blocked:
            with self.subTest(command=command):
                self.assertTrue(self.tools._is_blocked_verify_command(command))

    def test_rust_compile_only_verifier_gets_cargo_test_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text(
                "[package]\nname = \"compile-only\"\nversion = \"0.1.0\"\nedition = \"2021\"\n",
                encoding="utf-8",
            )
            commands = self.tools._ensure_required_verify_commands(root, ["cargo check"])

        self.assertEqual(commands, ["cargo check", "cargo test"])

    def test_rust_targeted_test_verifier_gets_full_cargo_test_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text(
                "[package]\nname = \"targeted-only\"\nversion = \"0.1.0\"\nedition = \"2021\"\n",
                encoding="utf-8",
            )
            filtered = self.tools._ensure_required_verify_commands(
                root,
                ["cargo test test_resource_locks -- --nocapture"],
            )
            target = self.tools._ensure_required_verify_commands(
                root,
                ["cargo test --test integration_tests"],
            )
            full = self.tools._ensure_required_verify_commands(
                root,
                ["cargo test -- --nocapture"],
            )

        self.assertEqual(filtered, ["cargo test test_resource_locks -- --nocapture", "cargo test"])
        self.assertEqual(target, ["cargo test --test integration_tests", "cargo test"])
        self.assertEqual(full, ["cargo test -- --nocapture"])

    def test_zero_test_outputs_are_not_successful_verification(self) -> None:
        node_output = "TAP version 13\n1..0\n# tests 0\n# pass 0\n"
        cargo_output = "running 0 tests\n\ntest result: ok. 0 passed; 0 failed\n\nrunning 0 tests\n"
        go_output = "?  \texample.com/empty\t[no test files]\n"
        self.assertTrue(self.tools._zero_tests_detected("node --test", node_output))
        self.assertTrue(self.tools._zero_tests_detected("cargo test", cargo_output))
        self.assertTrue(self.tools._zero_tests_detected("go test ./...", go_output))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.tools.builder_map({"project_path": str(root)})
            original_detector = self.tools._zero_tests_detected
            try:
                self.tools._zero_tests_detected = lambda _command, _output: True
                verify = json.loads(
                    self.tools.builder_verify(
                        {
                            "project_path": str(root),
                            "commands": ["python3 -c 'print(\"empty verifier\")'"],
                        }
                    )
                )
            finally:
                self.tools._zero_tests_detected = original_detector
            state = json.loads((root / ".hermes-builder" / "state.json").read_text(encoding="utf-8"))

        self.assertFalse(verify["success"])
        self.assertTrue(verify["failures"][0]["zero_tests_detected"])
        self.assertEqual(verify["failures"][0]["diagnostics"][0]["kind"], "zero-tests")
        self.assertFalse(state["guard"]["last_verify_success"])
        self.assertFalse(state["guard"]["receipt_required"])

    def test_failure_plan_rust_compiler_guides_single_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text(
                "[package]\nname = \"repair-plan\"\nversion = \"0.1.0\"\nedition = \"2021\"\n",
                encoding="utf-8",
            )
            result = json.loads(
                self.tools.builder_failure_plan(
                    {
                        "project_path": str(root),
                        "verification_result": {
                            "success": False,
                            "failures": [
                                {
                                    "command": "cargo test",
                                    "exit_code": 101,
                                    "timed_out": False,
                                    "zero_tests_detected": False,
                                    "output_tail": (
                                        "error[E0308]: mismatched types\n"
                                        "  --> src/core.rs:12:5\n"
                                        "   |\n"
                                        "12 |     value\n"
                                    ),
                                }
                            ],
                        },
                    }
                )
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["language_profile"], "rust")
        self.assertEqual(result["first_diagnostic"]["kind"], "rust-compiler")
        self.assertIn("src/core.rs", result["repair_plan"]["patch_target"])
        self.assertIn("one", result["repair_plan"]["patch_budget"].lower())
        self.assertIn("cargo test", result["repair_plan"]["next_verify_command"])

    def test_failure_plan_zero_tests_requires_discovered_python_test_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\nname = \"zero-tests\"\nversion = \"0.1.0\"\n",
                encoding="utf-8",
            )

            result = json.loads(
                self.tools.builder_failure_plan(
                    {
                        "project_path": str(root),
                        "command": "python3 -m unittest discover -s tests",
                        "output_tail": "Ran 0 tests in 0.000s\n\nOK\n",
                        "zero_tests_detected": True,
                    }
                )
            )

        steps = " ".join(result["repair_plan"]["steps"])
        self.assertTrue(result["success"])
        self.assertEqual(result["recipe"]["mode"], "python-add-focused-test")
        self.assertIn("tests/test_", steps)
        self.assertIn("not only tests/__init__.py", steps)

    def test_root_boundary_blocks_outside_write_when_project_context_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            outside = Path(tmp) / "outside.py"
            self.tools.builder_map({"project_path": str(root)})

            block = self.tools.builder_pre_tool_call(
                tool_name="write_file",
                args={"project_path": str(root), "path": str(outside)},
            )

        self.assertIsNotNone(block)
        self.assertEqual(block["action"], "block")
        self.assertIn("outside the mapped project root", block["message"])

    def test_terminal_file_write_is_blocked_inside_mapped_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "src" / "generated.go"
            self.tools.builder_map({"project_path": str(root)})

            block = self.tools.builder_pre_tool_call(
                tool_name="terminal",
                args={
                    "command": f"cat <<'EOF' > {target}\npackage main\nEOF",
                },
            )

        self.assertIsNotNone(block)
        self.assertEqual(block["action"], "block")
        self.assertIn("terminal file mutation", block["message"])

    def test_terminal_rm_is_blocked_inside_mapped_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "src" / "generated.go"
            target.parent.mkdir()
            target.write_text("package main\n", encoding="utf-8")
            self.tools.builder_map({"project_path": str(root)})

            block = self.tools.builder_pre_tool_call(
                tool_name="terminal",
                args={
                    "command": f"rm -rf {target}",
                },
            )

        self.assertIsNotNone(block)
        self.assertEqual(block["action"], "block")
        self.assertIn("terminal file mutation", block["message"])

    def test_receipt_not_ready_after_zero_test_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.tools.builder_map({"project_path": str(root)})
            self.tools.builder_resume(
                {
                    "project_path": str(root),
                    "action": "update",
                    "verification": [
                        {
                            "command": "cargo test",
                            "exit_code": 0,
                            "timed_out": False,
                            "zero_tests_detected": True,
                            "success": False,
                        }
                    ],
                }
            )

            receipt = json.loads(self.tools.builder_receipt({"project_path": str(root)}))
            state = json.loads((root / ".hermes-builder" / "state.json").read_text(encoding="utf-8"))

        self.assertTrue(receipt["success"])
        self.assertFalse(receipt["ready_to_report"])
        self.assertTrue(receipt["blocking_warnings"])
        self.assertIn("zero executed tests", " ".join(receipt["receipt"]["warnings"]))
        self.assertTrue(state["guard"]["last_receipt_blocked_reason"])


if __name__ == "__main__":
    unittest.main()
