from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.8.0"
TOOLS = {
    "builder_map",
    "builder_doctor",
    "builder_budget",
    "builder_plan",
    "builder_resume",
    "builder_acceptance",
    "builder_verify",
    "builder_failure_plan",
    "builder_receipt",
}
ENTRYPOINT_DOCS = [
    ROOT / "README.md",
    ROOT / "docs" / "QUICKSTART.md",
    ROOT / "docs" / "INSTALL.md",
    ROOT / "docs" / "HERMES_AGENT_SETUP.md",
]


class DocumentationTests(unittest.TestCase):
    def test_public_entrypoints_name_the_complete_toolset(self) -> None:
        for path in ENTRYPOINT_DOCS:
            text = path.read_text(encoding="utf-8")
            missing = sorted(tool for tool in TOOLS if tool not in text)
            self.assertEqual(missing, [], f"{path.relative_to(ROOT)} missing {missing}")

    def test_release_version_is_consistent(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        plugin = (ROOT / "plugin" / "builder-doctor" / "plugin.yaml").read_text(
            encoding="utf-8"
        )
        skill = (ROOT / "skills" / "builder-doctor" / "SKILL.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(f"Current release: **{VERSION}**", readme)
        self.assertIn(f"## {VERSION}", changelog)
        self.assertIn(f"version: {VERSION}", plugin)
        self.assertIn(f"version: {VERSION}", skill)

    def test_examples_include_acceptance_and_completion_signals(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / "examples").glob("*"))
            if path.is_file()
        )
        self.assertIn("builder_acceptance", combined)
        self.assertIn("already_verified", combined)
        self.assertIn("already_complete", combined)

    def test_relative_markdown_links_resolve(self) -> None:
        markdown_files = [ROOT / "README.md", ROOT / "CHANGELOG.md"]
        markdown_files.extend(sorted((ROOT / "docs").glob("*.md")))
        pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
        failures: list[str] = []
        for path in markdown_files:
            for target in pattern.findall(path.read_text(encoding="utf-8")):
                target = target.strip().split("#", 1)[0]
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                resolved = (path.parent / target).resolve()
                if not resolved.exists():
                    failures.append(f"{path.relative_to(ROOT)} -> {target}")
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
