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
                self.tools.builder_post_tool_call(
                    tool_name="write_file",
                    args={"path": str(root / f"file{index}.py")},
                    result=json.dumps({"success": True}),
                    status="ok",
                )

            block = self.tools.builder_pre_tool_call(
                tool_name="write_file",
                args={"path": str(root / "file3.py")},
            )

        self.assertIsNotNone(block)
        self.assertEqual(block["action"], "block")
        self.assertIn("write budget", block["message"])

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
            write_block = self.tools.builder_pre_tool_call(
                tool_name="write_file",
                args={"path": str(root / "extra.py")},
            )
            terminal_block = self.tools.builder_pre_tool_call(
                tool_name="terminal",
                args={"command": "python3 -m compileall -q .", "workdir": str(root)},
            )

            receipt = json.loads(self.tools.builder_receipt({"project_path": str(root)}))
            write_after_receipt = self.tools.builder_pre_tool_call(
                tool_name="write_file",
                args={"path": str(root / "next_stage.py")},
            )

        self.assertTrue(verify["success"])
        self.assertTrue(verify["state_recorded"])
        self.assertTrue(state["guard"]["receipt_required"])
        self.assertIsNotNone(write_block)
        self.assertIn("builder_verify has already passed", write_block["message"])
        self.assertIsNotNone(terminal_block)
        self.assertIn("raw terminal verifier", terminal_block["message"])
        self.assertTrue(receipt["state_recorded"])
        self.assertIsNone(write_after_receipt)

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
            first = self.tools.builder_pre_tool_call(
                tool_name="patch",
                args={"path": str(root / "repair.py")},
            )
            self.tools.builder_post_tool_call(
                tool_name="patch",
                args={"path": str(root / "repair.py")},
                result=json.dumps({"success": True}),
                status="ok",
            )
            second = self.tools.builder_pre_tool_call(
                tool_name="patch",
                args={"path": str(root / "repair.py")},
            )
            self.tools.builder_post_tool_call(
                tool_name="patch",
                args={"path": str(root / "repair.py")},
                result=json.dumps({"success": True}),
                status="ok",
            )
            third = self.tools.builder_pre_tool_call(
                tool_name="patch",
                args={"path": str(root / "repair.py")},
            )

        self.assertFalse(verify["success"])
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


if __name__ == "__main__":
    unittest.main()
