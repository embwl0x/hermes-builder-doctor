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
