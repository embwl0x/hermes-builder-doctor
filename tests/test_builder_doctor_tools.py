from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS_PATH = ROOT / "plugin" / "builder-doctor" / "tools.py"
PLUGIN_INIT_PATH = ROOT / "plugin" / "builder-doctor" / "__init__.py"


def load_tools_module():
    spec = importlib.util.spec_from_file_location("builder_doctor_tools", TOOLS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load tools module at {TOOLS_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_plugin_module():
    name = "builder_doctor_plugin_under_test"
    spec = importlib.util.spec_from_file_location(
        name,
        PLUGIN_INIT_PATH,
        submodule_search_locations=[str(PLUGIN_INIT_PATH.parent)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load plugin module at {PLUGIN_INIT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


class BuilderDoctorToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tools = load_tools_module()

    def record_objective(self, root: Path, objective: str = "Sample build objective") -> None:
        self.tools.builder_resume(
            {
                "project_path": str(root),
                "action": "update",
                "objective": objective,
            }
        )

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
            self.record_objective(root)

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

    def test_swift_write_gate_allows_coherent_six_edit_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Package.swift").write_text(
                "// swift-tools-version: 6.0\nimport PackageDescription\n",
                encoding="utf-8",
            )
            self.tools.builder_map({"project_path": str(root)})
            self.record_objective(root, "Build a Swift runtime parser with focused tests.")

            for index in range(6):
                allowed = self.tools.builder_pre_tool_call(
                    tool_name="write_file",
                    args={"path": str(root / f"File{index}.swift")},
                )
                self.assertIsNone(allowed)

            blocked = self.tools.builder_pre_tool_call(
                tool_name="write_file",
                args={"path": str(root / "File6.swift")},
            )

        self.assertIsNotNone(blocked)
        self.assertIn("write budget", blocked["message"])

    def test_new_acceptance_contract_reopens_verified_stage_for_edits(self) -> None:
        command = "python3 -m compileall -q ."
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text("print('ok')\n", encoding="utf-8")
            self.tools.builder_map({"project_path": str(root)})
            self.record_objective(root, "Build and prove a parser feature.")
            self.tools.builder_verify({"project_path": str(root), "commands": [command]})
            self.tools.builder_budget({"project_path": str(root), "after_verify": True})

            locked = self.tools.builder_pre_tool_call(
                tool_name="write_file",
                args={"path": str(root / "before_contract.py")},
            )
            acceptance = json.loads(
                self.tools.builder_acceptance(
                    {
                        "project_path": str(root),
                        "action": "replace",
                        "criteria": [
                            {
                                "id": "parser",
                                "description": "Parser source and tests exist and pass.",
                                "evidence_paths": ["parser.py", "test_parser.py"],
                                "verification_commands": [command],
                            }
                        ],
                    }
                )
            )
            reopened = self.tools.builder_pre_tool_call(
                tool_name="write_file",
                args={"path": str(root / "parser.py")},
            )
            state = json.loads((root / ".hermes-builder" / "state.json").read_text(encoding="utf-8"))

        self.assertIsNotNone(locked)
        self.assertFalse(acceptance["all_satisfied"])
        self.assertIsNone(reopened)
        self.assertIsNone(state["guard"]["last_verify_success"])
        self.assertFalse(state["guard"]["receipt_required"])
        self.assertTrue(state["guard"]["scope_phase_required"])

    def test_swift_placeholder_only_test_is_not_handoff_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Package.swift").write_text(
                "// swift-tools-version: 6.0\nimport PackageDescription\n",
                encoding="utf-8",
            )
            tests = root / "Tests" / "CoreTests"
            tests.mkdir(parents=True)
            (tests / "CoreTests.swift").write_text(
                "import XCTest\nfinal class CoreTests: XCTestCase {\n"
                "  func testPlaceholder() { XCTAssertTrue(true) }\n}\n",
                encoding="utf-8",
            )
            reasons = self.tools._missing_required_tests(root)

        self.assertTrue(any("trivial placeholder" in reason for reason in reasons))

    def test_source_edit_requires_saved_objective_after_state_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = json.loads(self.tools.builder_plan({"project_path": str(root)}))
            blocked = self.tools.builder_pre_tool_call(
                tool_name="write_file",
                args={"path": str(root / "Package.swift")},
            )
            self.record_objective(root, "Build a Swift tactical kernel with tests.")
            allowed = self.tools.builder_pre_tool_call(
                tool_name="write_file",
                args={"path": str(root / "Package.swift")},
            )

        self.assertTrue(plan["state_recorded"])
        self.assertIsNotNone(blocked)
        self.assertIn("no saved objective", blocked["message"])
        self.assertIsNone(allowed)

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
            self.record_objective(root)

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
            self.record_objective(root)
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
        self.assertIn(str(root), blocked_before_plan["message"])
        self.assertIn("automatically loads", blocked_before_plan["message"])
        self.assertTrue(plan["success"])
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertIsNotNone(third)
        self.assertIn("smallest relevant command", third["message"])

    def test_failure_plan_loads_latest_failed_verify_from_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.tools.builder_map({"project_path": str(root)})
            self.record_objective(root)
            verify = json.loads(
                self.tools.builder_verify(
                    {
                        "project_path": str(root),
                        "commands": ["python3 -c 'raise RuntimeError(\"stored failure\")'"],
                    }
                )
            )
            plan = json.loads(
                self.tools.builder_failure_plan({"project_path": str(root)})
            )
            state = json.loads(
                (root / ".hermes-builder" / "state.json").read_text(encoding="utf-8")
            )

        self.assertFalse(verify["success"])
        self.assertTrue(plan["success"])
        self.assertIn("python3 -c", plan["command"])
        self.assertIn("stored failure", json.dumps(plan))
        self.assertFalse(state["guard"]["failure_plan_required"])

    def test_verify_blocks_dependency_mutation_commands(self) -> None:
        blocked = [
            "npm install",
            "pnpm add react",
            "python3 -m pip install pytest",
            "python3 -m venv .venv",
            "uv venv",
            "uv pip install -e .",
            "cargo add anyhow",
            "go get example.com/pkg",
        ]
        for command in blocked:
            with self.subTest(command=command):
                self.assertTrue(self.tools._is_blocked_verify_command(command))

    def test_python_default_verifier_prefers_unittest_without_declared_pytest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\nname = \"plain-python\"\nversion = \"0.1.0\"\n",
                encoding="utf-8",
            )
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_core.py").write_text(
                "import unittest\n\nclass CoreTests(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )

            commands = self.tools._default_verify_commands(root, {}, {})

        self.assertEqual(commands, ["python3 -m unittest discover -s tests"])

    def test_python_default_verifier_uses_pytest_only_when_declared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\n"
                "name = \"pytest-python\"\n"
                "version = \"0.1.0\"\n"
                "dependencies = [\"pytest\"]\n",
                encoding="utf-8",
            )
            (root / "uv.lock").write_text("", encoding="utf-8")
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_core.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

            commands = self.tools._default_verify_commands(root, {}, {})

        self.assertEqual(commands, ["uv run pytest"])

    def test_terminal_dependency_env_mutation_is_blocked_inside_mapped_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\nname = \"install-noise\"\nversion = \"0.1.0\"\n",
                encoding="utf-8",
            )
            self.tools.builder_map({"project_path": str(root)})

            uv_block = self.tools.builder_pre_tool_call(
                tool_name="terminal",
                args={"command": "uv pip install -e .", "workdir": str(root)},
            )
            pip_block = self.tools.builder_pre_tool_call(
                tool_name="terminal",
                args={"command": "python3 -m venv .venv", "workdir": str(root)},
            )

        self.assertIsNotNone(uv_block)
        self.assertIn("dependency/environment mutation", uv_block["message"])
        self.assertIsNotNone(pip_block)
        self.assertIn("dependency/environment mutation", pip_block["message"])

    def test_builder_budget_flags_environment_artifacts_and_returns_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\nname = \"artifact-noise\"\nversion = \"0.1.0\"\n",
                encoding="utf-8",
            )
            (root / "artifact_noise.py").write_text("def ok():\n    return True\n", encoding="utf-8")
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_artifact_noise.py").write_text(
                "import unittest\n\nclass NoiseTests(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            (root / ".venv").mkdir()

            budget = json.loads(self.tools.builder_budget({"project_path": str(root)}))

        self.assertFalse(budget["over_budget"])
        self.assertFalse(budget["hard_stop"])
        self.assertEqual(budget["policy"]["preset"], "python-stdlib-kernel")
        self.assertIn(".venv", budget["environment_artifacts"])
        self.assertIn("environment-artifact-dirs", {warning["code"] for warning in budget["warnings"]})

    def test_builder_doctor_flags_install_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                '{"name":"node-artifact-noise","type":"module","scripts":{"test":"node --test"}}\n',
                encoding="utf-8",
            )
            (root / "node_modules").mkdir()

            doctor = json.loads(self.tools.builder_doctor({"project_path": str(root)}))

        self.assertTrue(doctor["success"])
        self.assertIn("staged-build-env-artifacts", {finding["code"] for finding in doctor["findings"]})

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

    def test_acceptance_rejects_vacuous_and_duplicate_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vacuous = json.loads(
                self.tools.builder_acceptance(
                    {
                        "project_path": str(root),
                        "action": "replace",
                        "criteria": [{"id": "ui", "description": "Build the UI"}],
                    }
                )
            )
            duplicate = json.loads(
                self.tools.builder_acceptance(
                    {
                        "project_path": str(root),
                        "action": "replace",
                        "criteria": [
                            {
                                "id": "ui",
                                "description": "Build the UI",
                                "evidence_paths": ["ui.py"],
                                "verification_commands": ["python3 -m unittest discover -s ."],
                            },
                            {
                                "id": "ui",
                                "description": "Duplicate UI proof",
                                "evidence_paths": ["test_ui.py"],
                                "verification_commands": ["python3 -m unittest discover -s ."],
                            },
                        ],
                    }
                )
            )

        self.assertFalse(vacuous["success"])
        self.assertTrue(any("evidence_path" in item for item in vacuous["errors"]))
        self.assertTrue(any("verification_command" in item for item in vacuous["errors"]))
        self.assertFalse(duplicate["success"])
        self.assertTrue(any("Duplicate criterion id" in item for item in duplicate["errors"]))

    def test_acceptance_rejects_evidence_outside_project_and_internal_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            outside = Path(tmp) / "outside.py"
            outside.write_text("print('outside')\n", encoding="utf-8")
            result = json.loads(
                self.tools.builder_acceptance(
                    {
                        "project_path": str(root),
                        "action": "replace",
                        "criteria": [
                            {
                                "id": "escape",
                                "description": "Do not accept external or internal state as proof",
                                "evidence_paths": [str(outside), ".hermes-builder/state.json"],
                                "verification_commands": ["python3 -m unittest discover -s ."],
                            }
                        ],
                    }
                )
            )

        self.assertFalse(result["success"])
        self.assertTrue(any("escapes the project root" in item for item in result["errors"]))
        self.assertTrue(any("outside .hermes-builder" in item for item in result["errors"]))

    def test_acceptance_requires_artifacts_and_recorded_builder_verify_command(self) -> None:
        command = "python3 -m unittest discover -s ."
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = json.loads(
                self.tools.builder_acceptance(
                    {
                        "project_path": str(root),
                        "action": "replace",
                        "criteria": [
                            {
                                "id": "behavior",
                                "description": "Core behavior exists and its focused test passes",
                                "evidence_paths": ["feature.py", "test_feature.py"],
                                "verification_commands": [command],
                            }
                        ],
                    }
                )
            )
            (root / "feature.py").write_text("def value():\n    return 7\n", encoding="utf-8")
            (root / "test_feature.py").write_text(
                "import unittest\nfrom feature import value\n\n"
                "class FeatureTests(unittest.TestCase):\n"
                "    def test_value(self):\n        self.assertEqual(value(), 7)\n",
                encoding="utf-8",
            )
            artifacts_only = json.loads(
                self.tools.builder_acceptance({"project_path": str(root), "action": "read"})
            )
            verify = json.loads(
                self.tools.builder_verify(
                    {"project_path": str(root), "commands": [command]}
                )
            )
            satisfied = json.loads(
                self.tools.builder_acceptance({"project_path": str(root), "action": "read"})
            )

        self.assertTrue(saved["success"])
        self.assertFalse(saved["all_satisfied"])
        self.assertFalse(artifacts_only["all_satisfied"])
        self.assertEqual(artifacts_only["unsatisfied"][0]["missing_verification"], [command])
        self.assertTrue(verify["success"])
        self.assertTrue(satisfied["all_satisfied"])
        self.assertEqual(len(satisfied["satisfied"]), 1)

    def test_unsatisfied_acceptance_blocks_receipt_until_proven(self) -> None:
        command = "python3 -m unittest discover -s ."
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test_feature.py").write_text(
                "import unittest\n\nclass FeatureTests(unittest.TestCase):\n"
                "    def test_placeholder(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            self.tools.builder_map({"project_path": str(root)})
            self.record_objective(root, "Deliver the acceptance feature")
            self.tools.builder_acceptance(
                {
                    "project_path": str(root),
                    "action": "replace",
                    "criteria": [
                        {
                            "id": "artifact",
                            "description": "The requested feature artifact exists and tests pass",
                            "evidence_paths": ["feature.py", "test_feature.py"],
                            "verification_commands": [command],
                        }
                    ],
                }
            )
            verify = json.loads(
                self.tools.builder_verify({"project_path": str(root), "commands": [command]})
            )
            self.tools.builder_budget({"project_path": str(root), "after_verify": True})
            blocked = json.loads(self.tools.builder_receipt({"project_path": str(root)}))

            (root / "feature.py").write_text("ENABLED = True\n", encoding="utf-8")
            reproved = json.loads(
                self.tools.builder_verify({"project_path": str(root), "commands": [command]})
            )
            self.tools.builder_budget({"project_path": str(root), "after_verify": True})
            ready = json.loads(self.tools.builder_receipt({"project_path": str(root)}))

        self.assertTrue(verify["success"])
        self.assertFalse(blocked["ready_to_report"])
        self.assertTrue(
            any("acceptance contract" in item.lower() for item in blocked["blocking_warnings"])
        )
        self.assertTrue(reproved["success"])
        self.assertTrue(ready["receipt"]["acceptance_contract"]["all_satisfied"])
        self.assertFalse(
            any("acceptance contract" in item.lower() for item in ready["blocking_warnings"])
        )

    def test_acceptance_requires_verification_after_contract_is_recorded(self) -> None:
        command = "python3 -m unittest discover -s ."
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "test_feature.py").write_text(
                "import unittest\n\nclass FeatureTests(unittest.TestCase):\n"
                "    def test_feature(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            before = json.loads(
                self.tools.builder_verify({"project_path": str(root), "commands": [command]})
            )
            saved = json.loads(
                self.tools.builder_acceptance(
                    {
                        "project_path": str(root),
                        "action": "replace",
                        "criteria": [
                            {
                                "id": "feature",
                                "description": "Feature exists and passes after the contract is set",
                                "evidence_paths": ["feature.py", "test_feature.py"],
                                "verification_commands": [command],
                            }
                        ],
                    }
                )
            )
            after = json.loads(
                self.tools.builder_verify({"project_path": str(root), "commands": [command]})
            )
            proven = json.loads(
                self.tools.builder_acceptance({"project_path": str(root), "action": "read"})
            )

        self.assertTrue(before["success"])
        self.assertFalse(saved["all_satisfied"])
        self.assertEqual(saved["unsatisfied"][0]["missing_verification"], [command])
        self.assertTrue(after["success"])
        self.assertTrue(proven["all_satisfied"])

    def test_acceptance_uses_latest_result_for_each_verifier(self) -> None:
        command = "python3 -m unittest discover -s ."
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            test_path = root / "test_feature.py"
            test_path.write_text(
                "import unittest\n\nclass FeatureTests(unittest.TestCase):\n"
                "    def test_feature(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            self.tools.builder_acceptance(
                {
                    "project_path": str(root),
                    "action": "replace",
                    "criteria": [
                        {
                            "id": "feature",
                            "description": "Latest feature test result must pass",
                            "evidence_paths": ["feature.py", "test_feature.py"],
                            "verification_commands": [command],
                        }
                    ],
                }
            )
            first = json.loads(
                self.tools.builder_verify({"project_path": str(root), "commands": [command]})
            )
            test_path.write_text(
                "import unittest\n\nclass FeatureTests(unittest.TestCase):\n"
                "    def test_feature(self):\n        self.fail('regressed')\n",
                encoding="utf-8",
            )
            second = json.loads(
                self.tools.builder_verify({"project_path": str(root), "commands": [command]})
            )
            evaluated = json.loads(
                self.tools.builder_acceptance({"project_path": str(root), "action": "read"})
            )

        self.assertTrue(first["success"])
        self.assertFalse(second["success"])
        self.assertFalse(evaluated["all_satisfied"])
        self.assertEqual(evaluated["unsatisfied"][0]["missing_verification"], [command])

    def test_acceptance_rejects_symlink_escape_created_after_save(self) -> None:
        command = "python3 -m unittest discover -s ."
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            root.mkdir()
            outside = base / "outside.py"
            outside.write_text("SECRET = True\n", encoding="utf-8")
            saved = json.loads(
                self.tools.builder_acceptance(
                    {
                        "project_path": str(root),
                        "action": "replace",
                        "criteria": [
                            {
                                "id": "artifact",
                                "description": "Artifact must remain within the project",
                                "evidence_paths": ["artifact.py"],
                                "verification_commands": [command],
                            }
                        ],
                    }
                )
            )
            (root / "artifact.py").symlink_to(outside)
            evaluated = json.loads(
                self.tools.builder_acceptance({"project_path": str(root), "action": "read"})
            )

        self.assertTrue(saved["success"])
        self.assertFalse(evaluated["all_satisfied"])
        self.assertEqual(evaluated["unsatisfied"][0]["unsafe_evidence"], ["artifact.py"])

    def test_malformed_persisted_acceptance_contract_blocks_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.tools.builder_map({"project_path": str(root)})
            state_path = root / ".hermes-builder" / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["acceptance_contract"] = {"criteria": "corrupt"}
            state_path.write_text(json.dumps(state), encoding="utf-8")

            evaluated = json.loads(
                self.tools.builder_acceptance({"project_path": str(root), "action": "read"})
            )
            receipt = json.loads(self.tools.builder_receipt({"project_path": str(root)}))

        self.assertFalse(evaluated["all_satisfied"])
        self.assertIn("malformed", evaluated["reason"].lower())
        self.assertTrue(
            any("acceptance contract" in item.lower() for item in receipt["blocking_warnings"])
        )

    def test_acceptance_update_replaces_by_id_and_invalidates_old_proof(self) -> None:
        command = "python3 -m unittest discover -s ."
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("api.py", "ui.py", "test_product.py"):
                (root / name).write_text("VALUE = True\n", encoding="utf-8")
            criteria = [
                {
                    "id": "api",
                    "description": "API exists",
                    "evidence_paths": ["api.py", "test_product.py"],
                    "verification_commands": [command],
                },
                {
                    "id": "ui",
                    "description": "UI exists",
                    "evidence_paths": ["ui.py", "test_product.py"],
                    "verification_commands": [command],
                },
            ]
            self.tools.builder_acceptance(
                {"project_path": str(root), "action": "replace", "criteria": criteria}
            )
            self.tools.builder_verify({"project_path": str(root), "commands": [command]})
            before = json.loads(
                self.tools.builder_acceptance({"project_path": str(root), "action": "read"})
            )
            updated = json.loads(
                self.tools.builder_acceptance(
                    {
                        "project_path": str(root),
                        "action": "update",
                        "criteria": [
                            {
                                "id": "api",
                                "description": "API exists with its public contract",
                                "evidence_paths": ["api.py", "test_product.py"],
                                "verification_commands": [command],
                            }
                        ],
                    }
                )
            )
            reverified = json.loads(
                self.tools.builder_verify({"project_path": str(root), "commands": [command]})
            )
            reproven = json.loads(
                self.tools.builder_acceptance({"project_path": str(root), "action": "read"})
            )

        self.assertTrue(before["all_satisfied"])
        self.assertEqual([item["id"] for item in updated["criteria"]], ["api", "ui"])
        self.assertEqual(updated["criteria"][0]["description"], "API exists with its public contract")
        self.assertFalse(updated["all_satisfied"])
        self.assertTrue(reverified["success"])
        self.assertTrue(reproven["all_satisfied"])

    def test_plugin_registers_acceptance_schema_and_handler(self) -> None:
        plugin = load_plugin_module()

        class FakeContext:
            def __init__(self):
                self.tools = {}

            def register_hook(self, *_args, **_kwargs):
                return None

            def register_tool(self, **kwargs):
                self.tools[kwargs["name"]] = kwargs

        context = FakeContext()
        plugin.register(context)

        registration = context.tools["builder_acceptance"]
        schema = registration["schema"]["parameters"]
        criterion_schema = schema["properties"]["criteria"]["items"]
        self.assertIs(registration["handler"], plugin.builder_acceptance)
        self.assertEqual(
            set(criterion_schema["required"]),
            {"id", "description", "evidence_paths", "verification_commands"},
        )
        self.assertEqual(
            schema["properties"]["action"]["enum"],
            ["read", "replace", "update", "clear"],
        )

    def test_acceptance_invalidates_proof_when_evidence_changes(self) -> None:
        command = "python3 -m unittest discover -s ."
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            feature = root / "feature.py"
            feature.write_text("VALUE = 1\n", encoding="utf-8")
            (root / "test_feature.py").write_text(
                "import unittest\n\nclass FeatureTests(unittest.TestCase):\n"
                "    def test_feature(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            self.tools.builder_acceptance(
                {
                    "project_path": str(root),
                    "action": "replace",
                    "criteria": [
                        {
                            "id": "feature",
                            "description": "The verified feature must not change afterward",
                            "evidence_paths": ["feature.py", "test_feature.py"],
                            "verification_commands": [command],
                        }
                    ],
                }
            )
            first_verify = json.loads(
                self.tools.builder_verify({"project_path": str(root), "commands": [command]})
            )
            before_change = json.loads(
                self.tools.builder_acceptance({"project_path": str(root), "action": "read"})
            )
            feature.write_text("VALUE = 2\n", encoding="utf-8")
            after_change = json.loads(
                self.tools.builder_acceptance({"project_path": str(root), "action": "read"})
            )
            second_verify = json.loads(
                self.tools.builder_verify({"project_path": str(root), "commands": [command]})
            )
            reproven = json.loads(
                self.tools.builder_acceptance({"project_path": str(root), "action": "read"})
            )

        self.assertTrue(first_verify["success"])
        self.assertTrue(before_change["all_satisfied"])
        self.assertFalse(after_change["all_satisfied"])
        self.assertEqual(after_change["unsatisfied"][0]["changed_evidence"], ["feature.py"])
        self.assertTrue(second_verify["success"])
        self.assertTrue(reproven["all_satisfied"])

    def test_acceptance_ignores_untrusted_manual_verification_record(self) -> None:
        command = "python3 -m unittest discover -s ."
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            self.tools.builder_acceptance(
                {
                    "project_path": str(root),
                    "action": "replace",
                    "criteria": [
                        {
                            "id": "feature",
                            "description": "Only builder_verify may establish proof",
                            "evidence_paths": ["feature.py"],
                            "verification_commands": [command],
                        }
                    ],
                }
            )
            self.tools.builder_resume(
                {
                    "project_path": str(root),
                    "action": "update",
                    "verification": [
                        {
                            "command": command,
                            "exit_code": 0,
                            "timed_out": False,
                            "zero_tests_detected": False,
                            "success": True,
                        }
                    ],
                }
            )
            evaluated = json.loads(
                self.tools.builder_acceptance({"project_path": str(root), "action": "read"})
            )

        self.assertFalse(evaluated["all_satisfied"])
        self.assertEqual(evaluated["unsatisfied"][0]["missing_verification"], [command])

    def test_corrupted_duplicate_ids_cannot_satisfy_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            state = self.tools._default_state(root)
            criterion = {
                "id": "feature",
                "description": "Unique feature criterion",
                "evidence_paths": ["feature.py"],
                "verification_commands": ["python3 -m unittest discover -s ."],
            }
            state["acceptance_contract"] = {
                "criteria": [criterion, dict(criterion)],
                "verification_baseline": 0,
            }
            self.tools._save_state(root, state)

            evaluated = json.loads(
                self.tools.builder_acceptance({"project_path": str(root), "action": "read"})
            )

        self.assertFalse(evaluated["all_satisfied"])
        duplicate = next(
            item for item in evaluated["unsatisfied"] if "duplicate id" in item["invalid"]
        )
        self.assertIn("duplicate id", duplicate["invalid"])

    def test_clear_acceptance_resets_guard_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.tools.builder_acceptance(
                {
                    "project_path": str(root),
                    "action": "replace",
                    "criteria": [
                        {
                            "id": "feature",
                            "description": "Feature proof",
                            "evidence_paths": ["feature.py"],
                            "verification_commands": ["python3 -m unittest discover -s ."],
                        }
                    ],
                }
            )
            cleared = json.loads(
                self.tools.builder_acceptance({"project_path": str(root), "action": "clear"})
            )
            state = json.loads(
                (root / ".hermes-builder" / "state.json").read_text(encoding="utf-8")
            )

        self.assertTrue(cleared["all_satisfied"])
        self.assertFalse(state["guard"]["acceptance_required"])
        self.assertTrue(state["guard"]["acceptance_ready"])
        self.assertEqual(state["acceptance_contract"]["criteria"], [])

    def test_duplicate_verify_and_receipt_become_compact_completion_noops(self) -> None:
        command = "python3 -m unittest discover -s ."
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "test_feature.py").write_text(
                "import unittest\n\nclass FeatureTests(unittest.TestCase):\n"
                "    def test_feature(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            self.tools.builder_map({"project_path": str(root)})
            self.record_objective(root, "Deliver feature proof")
            self.tools.builder_acceptance(
                {
                    "project_path": str(root),
                    "action": "replace",
                    "criteria": [
                        {
                            "id": "feature",
                            "description": "Feature exists and passes its test",
                            "evidence_paths": ["feature.py", "test_feature.py"],
                            "verification_commands": [command],
                        }
                    ],
                }
            )
            first_verify = json.loads(
                self.tools.builder_verify({"project_path": str(root), "commands": [command]})
            )
            verification_count = len(
                json.loads(
                    (root / ".hermes-builder" / "state.json").read_text(encoding="utf-8")
                )["verification"]
            )
            duplicate_verify = json.loads(
                self.tools.builder_verify({"project_path": str(root), "commands": [command]})
            )
            verification_count_after = len(
                json.loads(
                    (root / ".hermes-builder" / "state.json").read_text(encoding="utf-8")
                )["verification"]
            )
            self.tools.builder_budget({"project_path": str(root), "after_verify": True})
            receipt = json.loads(self.tools.builder_receipt({"project_path": str(root)}))
            duplicate_receipt = json.loads(
                self.tools.builder_receipt({"project_path": str(root)})
            )
            verify_after_receipt = json.loads(
                self.tools.builder_verify({"project_path": str(root), "commands": [command]})
            )
            acceptance_after_receipt = json.loads(
                self.tools.builder_acceptance({"project_path": str(root), "action": "read"})
            )

        self.assertTrue(first_verify["success"])
        self.assertTrue(duplicate_verify["already_verified"])
        self.assertEqual(verification_count_after, verification_count)
        self.assertTrue(receipt["ready_to_report"])
        self.assertNotIn("node", receipt["receipt"])
        self.assertNotIn("python", receipt["receipt"])
        self.assertEqual(
            receipt["next_required"],
            ["Stage complete. Stop calling builder tools and send the final answer now."],
        )
        self.assertTrue(duplicate_receipt["already_complete"])
        self.assertLess(len(json.dumps(duplicate_receipt)), 2500)
        self.assertTrue(verify_after_receipt["already_complete"])
        self.assertTrue(acceptance_after_receipt["already_complete"])

    def test_new_write_reservation_invalidates_completed_receipt(self) -> None:
        command = "python3 -m unittest discover -s ."
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "test_feature.py").write_text(
                "import unittest\n\nclass FeatureTests(unittest.TestCase):\n"
                "    def test_feature(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            self.tools.builder_map({"project_path": str(root)})
            self.record_objective(root, "Deliver feature proof")
            self.tools.builder_verify({"project_path": str(root), "commands": [command]})
            self.tools.builder_budget({"project_path": str(root), "after_verify": True})
            receipt = json.loads(self.tools.builder_receipt({"project_path": str(root)}))

            allowed = self.tools.builder_pre_tool_call(
                tool_name="write_file",
                args={"path": str(root / "next.py")},
            )
            state = json.loads(
                (root / ".hermes-builder" / "state.json").read_text(encoding="utf-8")
            )

        self.assertTrue(receipt["ready_to_report"])
        self.assertIsNone(allowed)
        self.assertFalse(state["guard"]["last_receipt_ready"])
        self.assertEqual(state["guard"]["writes_since_verify"], 1)


if __name__ == "__main__":
    unittest.main()
