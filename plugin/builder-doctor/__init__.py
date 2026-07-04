"""builder-doctor plugin registration."""

from __future__ import annotations

import logging

from .tools import (
    builder_budget,
    builder_doctor,
    builder_failure_plan,
    builder_map,
    builder_post_tool_call,
    builder_pre_tool_call,
    builder_plan,
    builder_receipt,
    builder_resume,
    builder_verify,
)

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    """Register builder-doctor tools into the builder-doctor toolset."""
    ctx.register_hook("pre_tool_call", builder_pre_tool_call)
    ctx.register_hook("post_tool_call", builder_post_tool_call)
    ctx.register_tool(
        name="builder_map",
        toolset="builder-doctor",
        schema={
            "name": "builder_map",
            "description": "FIRST TOOL for any substantial software build, app creation, repair, or refactor. For a new project, create the root folder first, then call builder_map before writing source/test batches. Map compact facts before editing: scripts, package manager, Node/TypeScript lockfiles, SwiftPM targets/imports, Python pyproject/uv/pytest facts, Rust/Cargo metadata, Go module metadata, entrypoints, config files, tests, source samples, and git status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Absolute path to the project root.",
                    },
                    "max_files": {
                        "type": "integer",
                        "description": "Maximum project files to sample while mapping.",
                        "default": 600,
                    },
                },
                "required": ["project_path"],
            },
        },
        handler=builder_map,
        description="First tool for substantial software builds: map compact build facts before editing.",
        emoji="🗺️",
    )
    ctx.register_tool(
        name="builder_doctor",
        toolset="builder-doctor",
        schema={
            "name": "builder_doctor",
            "description": "Use during software build/repair work to scan Node/TypeScript, SwiftPM/XCTest, Python/uv/pytest, Rust/Cargo, and Go module projects for common build/test/type-system weak spots without mutating files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Absolute path to the project root.",
                    },
                    "focus": {
                        "type": "string",
                        "description": "Category to focus on: all, workspace, node, javascript, typescript, esm, package, testing, scripts, build, swift, swiftpm, python, pyproject, rust, cargo, go, gomod.",
                        "default": "all",
                    },
                },
                "required": ["project_path"],
            },
        },
        handler=builder_doctor,
        description="Scan a project for common build/test/type-system weak spots.",
        emoji="🩺",
    )
    ctx.register_tool(
        name="builder_budget",
        toolset="builder-doctor",
        schema={
            "name": "builder_budget",
            "description": "Use during local-model software builds after each source/test batch and after successful verification. Checks whether the current phase has grown beyond the staged-kernel budget and tells the agent whether to verify, receipt, or defer scope.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Absolute path to the project root.",
                    },
                    "phase": {
                        "type": "string",
                        "description": "Current phase name, such as scaffold, kernel, hardening, integration, or repair.",
                        "default": "kernel",
                    },
                    "after_verify": {
                        "type": "boolean",
                        "description": "Set true immediately after builder_verify succeeds.",
                        "default": False,
                    },
                    "max_source_files": {
                        "type": "integer",
                        "description": "Maximum source files for the current phase before the tool recommends stopping scope.",
                        "default": 8,
                    },
                    "max_test_files": {
                        "type": "integer",
                        "description": "Maximum test files for the current phase before the tool recommends stopping scope.",
                        "default": 4,
                    },
                    "max_source_dirs": {
                        "type": "integer",
                        "description": "Maximum source directories/packages for the current phase before the tool recommends stopping scope.",
                        "default": 4,
                    },
                },
                "required": ["project_path"],
            },
        },
        handler=builder_budget,
        description="Check phase/file budget and decide whether to verify, receipt, or defer scope.",
        emoji="📏",
    )
    ctx.register_tool(
        name="builder_plan",
        toolset="builder-doctor",
        schema={
            "name": "builder_plan",
            "description": "SECOND TOOL for substantial software builds after builder_map. For new projects, call this after creating the root folder and before writing broad source/test batches. Create a bounded phase plan with small file batches, verification gates, checkpointing, and final receipt rules.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Absolute path to the project root.",
                    },
                    "objective": {
                        "type": "string",
                        "description": "Short description of what is being built or changed.",
                    },
                    "max_phases": {
                        "type": "integer",
                        "description": "Maximum number of phases to return.",
                        "default": 7,
                    },
                },
                "required": ["project_path"],
            },
        },
        handler=builder_plan,
        description="Second tool for substantial builds: plan bounded phases and verification gates.",
        emoji="🧭",
    )
    ctx.register_tool(
        name="builder_resume",
        toolset="builder-doctor",
        schema={
            "name": "builder_resume",
            "description": "Use during long software builds to read or write a project-local .hermes-builder/state.json checkpoint so work can resume after compaction, interruption, or stalls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Absolute path to the project root.",
                    },
                    "action": {
                        "type": "string",
                        "description": "read, update, replace, or clear.",
                        "default": "read",
                    },
                    "objective": {"type": "string"},
                    "status": {"type": "string"},
                    "phase": {"type": "string"},
                    "current_phase": {"type": "string"},
                    "completed": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "next_steps": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "decisions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "files_touched": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "verification": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                    "notes": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "max_items": {
                        "type": "integer",
                        "default": 60,
                    },
                },
                "required": ["project_path"],
            },
        },
        handler=builder_resume,
        description="Persist or read a compact checkpoint during long builds.",
        emoji="💾",
    )
    ctx.register_tool(
        name="builder_verify",
        toolset="builder-doctor",
        schema={
            "name": "builder_verify",
            "description": "Preferred verification tool for software builds. Use instead of ad hoc terminal test loops for npm/pnpm/yarn/bun test/build/lint/check, SwiftPM swift build/test, Python uv run pytest/compileall, Cargo cargo test, Go go test ./..., or bounded project commands; returns compact failures.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Absolute path to the project root.",
                    },
                    "commands": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of shell commands to run. If omitted, pick a conservative existing script (test/build/lint).",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Timeout per command in seconds.",
                        "default": 120,
                    },
                },
                "required": ["project_path"],
            },
        },
        handler=builder_verify,
        description="Preferred build/test verification tool; use instead of ad hoc terminal loops.",
        emoji="✅",
    )
    ctx.register_tool(
        name="builder_failure_plan",
        toolset="builder-doctor",
        schema={
            "name": "builder_failure_plan",
            "description": "Use immediately after failed builder_verify before patching. Summarizes the first concrete failure, returns language-specific repair steps, and keeps the repair loop to one focused patch before rerunning the same verifier.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Absolute path to the project root.",
                    },
                    "verification_result": {
                        "type": "object",
                        "description": "Optional full JSON result returned by builder_verify.",
                    },
                    "command": {
                        "type": "string",
                        "description": "Verifier command that failed, if not passing verification_result.",
                    },
                    "output_tail": {
                        "type": "string",
                        "description": "Compact verifier output tail, if not passing verification_result.",
                    },
                    "timed_out": {
                        "type": "boolean",
                        "description": "Whether the verifier timed out.",
                        "default": False,
                    },
                    "zero_tests_detected": {
                        "type": "boolean",
                        "description": "Whether the verifier reported zero executed tests.",
                        "default": False,
                    },
                },
                "required": ["project_path"],
            },
        },
        handler=builder_failure_plan,
        description="Summarize a failed verifier and return a focused language repair plan.",
        emoji="🧯",
    )
    ctx.register_tool(
        name="builder_receipt",
        toolset="builder-doctor",
        schema={
            "name": "builder_receipt",
            "description": "Use before the final answer for software build work. Produce a project receipt from builder state, git status, scripts, files touched, verification records, and warnings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Absolute path to the project root.",
                    },
                    "verification_results": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Optional builder_verify results to include without mutating state.",
                    },
                    "max_files": {
                        "type": "integer",
                        "description": "Maximum changed/touched files to include.",
                        "default": 80,
                    },
                },
                "required": ["project_path"],
            },
        },
        handler=builder_receipt,
        description="Build a final handoff receipt with proof and remaining warnings.",
        emoji="🧾",
    )
