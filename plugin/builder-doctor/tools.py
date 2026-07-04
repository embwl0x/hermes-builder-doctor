"""builder-doctor plugin tool implementations."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


SKIP_DIRS = {
    ".git",
    ".hermes-builder",
    ".build",
    ".next",
    ".nuxt",
    ".swiftpm",
    ".svelte-kit",
    ".turbo",
    ".venv",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "__pycache__",
    "build",
    "coverage",
    "DerivedData",
    "dist",
    "htmlcov",
    "node_modules",
    "out",
    "target",
    "vendor",
}

SOURCE_EXTS = {
    ".cjs",
    ".css",
    ".html",
    ".js",
    ".jsx",
    ".json",
    ".mjs",
    ".mts",
    ".py",
    ".go",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
    ".vue",
}

TEST_MARKERS = (".test.", ".spec.", "__tests__", "/tests/", "/test/")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=True)


def _read_json(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _read_toml(path: Path) -> Optional[dict]:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        return None


def _read_text(path: Path) -> Optional[str]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=True, indent=2)
        f.write("\n")
    tmp.replace(path)


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_int(value: Any, default: int, min_value: int = 1, max_value: int = 10000) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return max(min_value, min(max_value, parsed))


def _clip(text: Any, limit: int = 6000) -> str:
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... clipped {len(value) - limit} chars ..."


def _listify(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _append_unique(existing: List[Any], incoming: List[Any], max_items: int) -> List[Any]:
    result = list(existing or [])
    seen = {json.dumps(item, sort_keys=True, ensure_ascii=True, default=str) for item in result}
    for item in incoming:
        key = json.dumps(item, sort_keys=True, ensure_ascii=True, default=str)
        if key in seen:
            continue
        result.append(item)
        seen.add(key)
        if len(result) >= max_items:
            break
    return result


def _walk_project_files(root: Path, max_files: int = 1000) -> List[Path]:
    files: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".cache")]
        current = Path(dirpath)
        for name in filenames:
            files.append(current / name)
            if len(files) >= max_files:
                return files
    return files


def _detect_package_manager(root: Path) -> str:
    managers: List[str] = []
    has_swiftpm = (root / "Package.swift").exists()
    pyproject = _read_toml(root / "pyproject.toml") or {}
    has_python = bool(pyproject) or (root / "setup.py").exists() or (root / "setup.cfg").exists() or any(root.glob("requirements*.txt"))
    locks = [
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
        ("bun.lock", "bun"),
        ("package-lock.json", "npm"),
    ]
    for filename, manager in locks:
        if (root / filename).exists():
            managers.append(manager)
            break
    if (root / "package.json").exists():
        if not managers:
            managers.append("npm")
    if has_swiftpm:
        managers.append("swiftpm")
    if (root / "Cargo.toml").exists():
        managers.append("cargo")
    if (root / "go.mod").exists():
        managers.append("go")
    if has_python:
        py_manager = "python"
        build_backend = str(((pyproject.get("build-system") or {}).get("build-backend") or ""))
        if (root / "uv.lock").exists():
            py_manager = "uv"
        elif (root / "poetry.lock").exists() or "tool" in pyproject and "poetry" in (pyproject.get("tool") or {}):
            py_manager = "poetry"
        elif (root / "Pipfile").exists() or (root / "Pipfile.lock").exists():
            py_manager = "pipenv"
        elif "hatchling" in build_backend or "tool" in pyproject and "hatch" in (pyproject.get("tool") or {}):
            py_manager = "hatch"
        elif any(root.glob("requirements*.txt")):
            py_manager = "pip"
        managers.append(py_manager)
    return "+".join(managers) if managers else "unknown"


def _package_scripts(pkg: Dict[str, Any]) -> Dict[str, str]:
    scripts = pkg.get("scripts", {}) or {}
    if not isinstance(scripts, dict):
        return {}
    return {str(k): str(v) for k, v in sorted(scripts.items())}


def _package_deps(pkg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for field in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        raw = pkg.get(field, {}) or {}
        if not isinstance(raw, dict):
            groups[field] = {"count": 0, "names": []}
            continue
        names = sorted(str(k) for k in raw.keys())
        groups[field] = {"count": len(names), "names": names[:80]}
    return groups


def _node_package_manager(root: Path) -> str:
    if (root / "pnpm-lock.yaml").exists() or (root / "pnpm-workspace.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        return "bun"
    if (root / "package-lock.json").exists() or (root / "package.json").exists():
        return "npm"
    return ""


def _node_script_command(root: Path, script: str) -> str:
    manager = _node_package_manager(root) or "npm"
    return f"{manager} run {script}"


def _node_project_info(root: Path, pkg: Dict[str, Any], files: Optional[List[Path]] = None) -> Dict[str, Any]:
    sampled = files if files is not None else _walk_project_files(root, max_files=900)
    source_exts = {".cjs", ".js", ".jsx", ".mjs", ".mts", ".ts", ".tsx", ".vue"}
    source_files = [path for path in sampled if path.suffix in source_exts]
    ts_files = [path for path in source_files if path.suffix in {".mts", ".ts", ".tsx", ".vue"}]
    test_files = [
        _rel(path, root)
        for path in source_files
        if any(marker in _rel(path, root).lower() for marker in TEST_MARKERS)
    ]
    config_files = [
        name for name in (
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            "yarn.lock",
            "bun.lock",
            "bun.lockb",
            "tsconfig.json",
            "vite.config.ts",
            "vite.config.js",
            "vitest.config.ts",
            "vitest.config.js",
            "next.config.js",
            "next.config.mjs",
            "tailwind.config.js",
            "tailwind.config.ts",
            "eslint.config.js",
            "eslint.config.mjs",
        )
        if (root / name).exists()
    ]
    lockfiles = [
        name for name in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb")
        if (root / name).exists()
    ]
    deps = _package_deps(pkg)
    dep_names: set[str] = set()
    for group in deps.values():
        dep_names.update(group.get("names", []))

    return {
        "is_node_project": bool(pkg) or bool(source_files) or bool(config_files),
        "package_file": "package.json" if (root / "package.json").exists() else "",
        "package_name": pkg.get("name", ""),
        "package_type": pkg.get("type", ""),
        "package_manager": _node_package_manager(root),
        "package_manager_field": pkg.get("packageManager", ""),
        "lockfiles": lockfiles,
        "scripts": _package_scripts(pkg),
        "dependencies": sorted(dep_names)[:160],
        "config_files": config_files,
        "source_file_count": len(source_files),
        "typescript_file_count": len(ts_files),
        "test_files": sorted(set(test_files))[:80],
    }


def _workspace_patterns(pkg: Dict[str, Any], root: Path) -> List[str]:
    patterns: List[str] = []
    ws_field = pkg.get("workspaces")
    if isinstance(ws_field, list):
        patterns.extend(str(item) for item in ws_field if isinstance(item, str))
    elif isinstance(ws_field, dict):
        for item in ws_field.get("packages", []) or []:
            if isinstance(item, str):
                patterns.append(item)
    pnpm_ws = root / "pnpm-workspace.yaml"
    if pnpm_ws.exists():
        txt = _read_text(pnpm_ws) or ""
        for line in txt.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                patterns.append(stripped[2:].strip().strip("\"'"))
    return sorted(set(patterns))


def _swift_package_text(root: Path) -> str:
    return _read_text(root / "Package.swift") or ""


def _swift_tools_version(package_text: str) -> str:
    match = re.search(r"swift-tools-version:\s*([0-9.]+)", package_text)
    return match.group(1) if match else ""


def _swift_target_names(package_text: str, target_kind: str) -> List[str]:
    pattern = rf"\.{re.escape(target_kind)}\s*\(\s*name:\s*\"([^\"]+)\""
    return sorted(set(re.findall(pattern, package_text)))


def _swift_project_info(root: Path, files: Optional[List[Path]] = None) -> Dict[str, Any]:
    package_text = _swift_package_text(root)
    package_exists = bool(package_text)
    sampled = files if files is not None else _walk_project_files(root, max_files=700)
    swift_files = [path for path in sampled if path.suffix == ".swift"]
    source_dirs = sorted(
        p.name for p in (root / "Sources").iterdir()
        if p.is_dir()
    ) if (root / "Sources").is_dir() else []
    test_dirs = sorted(
        p.name for p in (root / "Tests").iterdir()
        if p.is_dir()
    ) if (root / "Tests").is_dir() else []

    imports: set[str] = set()
    main_files: List[str] = []
    app_files: List[str] = []
    test_files: List[str] = []
    scanned = 0
    for path in swift_files:
        if scanned >= 250:
            break
        txt = _read_text(path)
        if txt is None:
            continue
        scanned += 1
        rel = _rel(path, root)
        lowered = rel.lower()
        if "/tests/" in lowered or "/test/" in lowered or path.name.endswith("Tests.swift"):
            test_files.append(rel)
        if path.name == "main.swift" or "@main" in txt:
            main_files.append(rel)
        if re.search(r"\bstruct\s+\w+\s*:\s*App\b", txt):
            app_files.append(rel)
        for match in re.finditer(r"^\s*import\s+([A-Za-z_][A-Za-z0-9_]*)", txt, re.MULTILINE):
            imports.add(match.group(1))

    target_names = _swift_target_names(package_text, "target")
    executable_targets = _swift_target_names(package_text, "executableTarget")
    test_targets = _swift_target_names(package_text, "testTarget")
    executable_products = _swift_target_names(package_text, "executable")
    library_products = _swift_target_names(package_text, "library")

    return {
        "is_swift_project": package_exists or bool(swift_files),
        "package_file": "Package.swift" if package_exists else "",
        "tools_version": _swift_tools_version(package_text),
        "platforms_declared": "platforms:" in package_text,
        "source_target_dirs": source_dirs[:80],
        "test_target_dirs": test_dirs[:80],
        "targets": {
            "regular": target_names[:80],
            "executable": executable_targets[:80],
            "test": test_targets[:80],
        },
        "products": {
            "executable": executable_products[:80],
            "library": library_products[:80],
        },
        "imports": sorted(imports)[:80],
        "main_files": sorted(set(main_files))[:80],
        "app_files": sorted(set(app_files))[:80],
        "test_files": sorted(set(test_files))[:80],
        "swift_file_count": len(swift_files),
        "scanned_swift_files": scanned,
    }


def _python_dep_name(raw: str) -> str:
    value = str(raw).strip()
    value = value.split(";", 1)[0].strip()
    value = value.split("[", 1)[0].strip()
    return re.split(r"\s*(?:==|~=|!=|<=|>=|<|>|=)\s*", value, maxsplit=1)[0].strip().lower()


def _python_project_info(root: Path, files: Optional[List[Path]] = None) -> Dict[str, Any]:
    pyproject = _read_toml(root / "pyproject.toml") or {}
    project = pyproject.get("project") if isinstance(pyproject.get("project"), dict) else {}
    tool = pyproject.get("tool") if isinstance(pyproject.get("tool"), dict) else {}
    poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
    build_system = pyproject.get("build-system") if isinstance(pyproject.get("build-system"), dict) else {}
    sampled = files if files is not None else _walk_project_files(root, max_files=900)
    python_files = [path for path in sampled if path.suffix == ".py"]

    dependencies: set[str] = set()
    for dep in project.get("dependencies", []) or []:
        dependencies.add(_python_dep_name(dep))
    optional = project.get("optional-dependencies", {}) or {}
    if isinstance(optional, dict):
        for deps in optional.values():
            for dep in deps or []:
                dependencies.add(_python_dep_name(dep))
    for section in ("dependencies", "dev-dependencies"):
        raw = poetry.get(section, {}) if isinstance(poetry, dict) else {}
        if isinstance(raw, dict):
            dependencies.update(str(name).lower() for name in raw.keys() if str(name).lower() != "python")
    for req in root.glob("requirements*.txt"):
        txt = _read_text(req) or ""
        for line in txt.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                continue
            dependencies.add(_python_dep_name(stripped))

    entrypoints: List[str] = []
    scripts = project.get("scripts", {}) or {}
    gui_scripts = project.get("gui-scripts", {}) or {}
    if isinstance(scripts, dict):
        entrypoints.extend(f"{name}={target}" for name, target in sorted(scripts.items()))
    if isinstance(gui_scripts, dict):
        entrypoints.extend(f"{name}={target}" for name, target in sorted(gui_scripts.items()))
    poetry_scripts = poetry.get("scripts", {}) if isinstance(poetry, dict) else {}
    if isinstance(poetry_scripts, dict):
        entrypoints.extend(f"{name}={target}" for name, target in sorted(poetry_scripts.items()))

    source_roots: List[str] = []
    for candidate in ("src", "app", "apps", "services", "lib"):
        if (root / candidate).is_dir():
            source_roots.append(candidate)
    for path in root.iterdir() if root.exists() else []:
        if path.is_dir() and path.name not in SKIP_DIRS and (path / "__init__.py").exists():
            source_roots.append(path.name)

    imports: set[str] = set()
    main_files: List[str] = []
    test_files: List[str] = []
    scanned = 0
    for path in python_files:
        if scanned >= 300:
            break
        txt = _read_text(path)
        if txt is None:
            continue
        scanned += 1
        rel = _rel(path, root)
        lowered = rel.lower()
        if any(marker in lowered for marker in TEST_MARKERS) or path.name.startswith("test_") or path.name.endswith("_test.py"):
            test_files.append(rel)
        if path.name in {"main.py", "cli.py", "__main__.py"} or "__name__" in txt and "__main__" in txt:
            main_files.append(rel)
        for match in re.finditer(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_\.]*)", txt, re.MULTILINE):
            imports.add(match.group(1).split(".", 1)[0])

    tool_names = sorted(set(str(name).lower() for name in tool.keys()))
    config_files = [
        name for name in (
            "pyproject.toml",
            "uv.lock",
            "requirements.txt",
            "requirements-dev.txt",
            "setup.py",
            "setup.cfg",
            "pytest.ini",
            "tox.ini",
            "noxfile.py",
            "ruff.toml",
            ".python-version",
        )
        if (root / name).exists()
    ]

    return {
        "is_python_project": bool(pyproject) or bool(python_files) or bool(config_files),
        "project_file": "pyproject.toml" if (root / "pyproject.toml").exists() else "",
        "project_name": project.get("name") or poetry.get("name") or "",
        "requires_python": project.get("requires-python") or poetry.get("dependencies", {}).get("python", "") if isinstance(poetry.get("dependencies", {}), dict) else project.get("requires-python", ""),
        "build_backend": build_system.get("build-backend", ""),
        "manager": _detect_package_manager(root).split("+")[-1] if _detect_package_manager(root) != "unknown" else "unknown",
        "config_files": config_files,
        "source_roots": sorted(set(source_roots))[:80],
        "entrypoints": sorted(set(entrypoints))[:80],
        "main_files": sorted(set(main_files))[:80],
        "test_files": sorted(set(test_files))[:80],
        "imports": sorted(imports)[:100],
        "dependencies": sorted(d for d in dependencies if d)[:120],
        "tools": tool_names[:80],
        "python_file_count": len(python_files),
        "scanned_python_files": scanned,
    }


def _rust_project_info(root: Path, files: Optional[List[Path]] = None) -> Dict[str, Any]:
    cargo = _read_toml(root / "Cargo.toml") or {}
    package = cargo.get("package") if isinstance(cargo.get("package"), dict) else {}
    sampled = files if files is not None else _walk_project_files(root, max_files=900)
    rust_files = [path for path in sampled if path.suffix == ".rs"]
    dependencies: set[str] = set()
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        raw = cargo.get(section, {}) if isinstance(cargo.get(section, {}), dict) else {}
        dependencies.update(str(name).lower() for name in raw.keys())
    workspace = cargo.get("workspace") if isinstance(cargo.get("workspace"), dict) else {}
    workspace_members = workspace.get("members", []) if isinstance(workspace.get("members", []), list) else []

    main_files: List[str] = []
    test_files: List[str] = []
    modules: set[str] = set()
    has_inline_tests = False
    lib_files: List[str] = []
    scanned = 0
    for path in rust_files:
        if scanned >= 300:
            break
        txt = _read_text(path)
        if txt is None:
            continue
        scanned += 1
        rel = _rel(path, root)
        lowered = rel.lower()
        if "/tests/" in lowered or path.name.endswith("_test.rs"):
            test_files.append(rel)
        if "#[test]" in txt or "#[tokio::test]" in txt or "#[async_std::test]" in txt:
            has_inline_tests = True
        if path.name == "main.rs" or "fn main(" in txt:
            main_files.append(rel)
        if path.name == "lib.rs":
            lib_files.append(rel)
        for match in re.finditer(r"^\s*(?:use|extern\s+crate)\s+([A-Za-z_][A-Za-z0-9_]*)", txt, re.MULTILINE):
            modules.add(match.group(1))

    return {
        "is_rust_project": bool(cargo) or bool(rust_files),
        "project_file": "Cargo.toml" if (root / "Cargo.toml").exists() else "",
        "package_name": package.get("name", ""),
        "edition": package.get("edition", ""),
        "workspace_members": [str(item) for item in workspace_members][:80],
        "dependencies": sorted(dependencies)[:120],
        "features": sorted((cargo.get("features") or {}).keys())[:80] if isinstance(cargo.get("features"), dict) else [],
        "main_files": sorted(set(main_files))[:80],
        "lib_files": sorted(set(lib_files))[:80],
        "test_files": sorted(set(test_files))[:80],
        "has_inline_tests": has_inline_tests,
        "modules": sorted(modules)[:100],
        "rust_file_count": len(rust_files),
        "scanned_rust_files": scanned,
    }


def _go_project_info(root: Path, files: Optional[List[Path]] = None) -> Dict[str, Any]:
    go_mod = _read_text(root / "go.mod") or ""
    module = ""
    go_version = ""
    requires: List[str] = []
    for line in go_mod.splitlines():
        stripped = line.strip()
        if stripped.startswith("module "):
            module = stripped.split(None, 1)[1]
        elif stripped.startswith("go "):
            go_version = stripped.split(None, 1)[1]
        elif stripped.startswith("require "):
            parts = stripped.split()
            if len(parts) >= 2 and parts[1] != "(":
                requires.append(parts[1])
        elif stripped and not stripped.startswith("//") and "/" in stripped and re.match(r"^[A-Za-z0-9_.\-/]+\s+v", stripped):
            requires.append(stripped.split()[0])

    sampled = files if files is not None else _walk_project_files(root, max_files=900)
    go_files = [path for path in sampled if path.suffix == ".go"]
    packages: set[str] = set()
    packages_by_dir: Dict[str, set[str]] = {}
    imports: set[str] = set()
    main_files: List[str] = []
    test_files: List[str] = []
    scanned = 0
    for path in go_files:
        if scanned >= 300:
            break
        txt = _read_text(path)
        if txt is None:
            continue
        scanned += 1
        rel = _rel(path, root)
        if path.name.endswith("_test.go"):
            test_files.append(rel)
        m = re.search(r"^\s*package\s+([A-Za-z_][A-Za-z0-9_]*)", txt, re.MULTILINE)
        if m:
            packages.add(m.group(1))
            packages_by_dir.setdefault(_rel(path.parent, root), set()).add(m.group(1))
            if m.group(1) == "main":
                main_files.append(rel)
        for match in re.finditer(r"^\s*\"([A-Za-z0-9_./-]+)\"", txt, re.MULTILINE):
            imports.add(match.group(1).split("/", 1)[0])
        for match in re.finditer(r"^\s*import\s+\"([A-Za-z0-9_./-]+)\"", txt, re.MULTILINE):
            imports.add(match.group(1).split("/", 1)[0])

    return {
        "is_go_project": bool(go_mod) or bool(go_files),
        "project_file": "go.mod" if (root / "go.mod").exists() else "",
        "module": module,
        "go_version": go_version,
        "requires": sorted(set(requires))[:120],
        "packages": sorted(packages)[:80],
        "package_dirs": {key: sorted(value) for key, value in sorted(packages_by_dir.items())},
        "mixed_package_dirs": {
            key: sorted(value)
            for key, value in sorted(packages_by_dir.items())
            if len(value) > 1
        },
        "imports": sorted(imports)[:100],
        "main_files": sorted(set(main_files))[:80],
        "test_files": sorted(set(test_files))[:80],
        "go_file_count": len(go_files),
        "scanned_go_files": scanned,
    }


def _framework_signals(root: Path, pkg: Dict[str, Any]) -> List[str]:
    deps: set[str] = set()
    for group in _package_deps(pkg).values():
        deps.update(group.get("names", []))
    signals: List[str] = []
    checks = {
        "next": "Next.js",
        "react": "React",
        "vite": "Vite",
        "vitest": "Vitest",
        "typescript": "TypeScript",
        "tailwindcss": "Tailwind CSS",
        "vue": "Vue",
        "svelte": "Svelte",
        "express": "Express",
        "fastify": "Fastify",
        "electron": "Electron",
        "@tauri-apps/api": "Tauri",
        "jest": "Jest",
        "playwright": "Playwright",
    }
    for dep, label in checks.items():
        if dep in deps:
            signals.append(label)
    file_checks = {
        "next.config.js": "Next.js config",
        "next.config.mjs": "Next.js config",
        "vite.config.ts": "Vite config",
        "vite.config.js": "Vite config",
        "vitest.config.ts": "Vitest config",
        "tailwind.config.js": "Tailwind config",
        "tailwind.config.ts": "Tailwind config",
        "tsconfig.json": "TypeScript config",
    }
    for filename, label in file_checks.items():
        if (root / filename).exists() and label not in signals:
            signals.append(label)
    swift_info = _swift_project_info(root)
    if swift_info.get("package_file"):
        signals.append("SwiftPM")
    if any(root.glob("*.xcodeproj")):
        signals.append("Xcode project")
    swift_import_labels = {
        "SwiftUI": "SwiftUI",
        "AppKit": "AppKit",
        "UIKit": "UIKit",
        "SpriteKit": "SpriteKit",
        "SceneKit": "SceneKit",
        "XCTest": "XCTest",
        "Testing": "Swift Testing",
    }
    for imported, label in swift_import_labels.items():
        if imported in swift_info.get("imports", []) and label not in signals:
            signals.append(label)
    python_info = _python_project_info(root)
    if python_info.get("is_python_project"):
        signals.append("Python")
    python_labels = {
        "pytest": "pytest",
        "ruff": "Ruff",
        "mypy": "mypy",
        "pyright": "Pyright",
        "fastapi": "FastAPI",
        "django": "Django",
        "flask": "Flask",
        "typer": "Typer",
        "click": "Click",
        "pydantic": "Pydantic",
        "sqlalchemy": "SQLAlchemy",
    }
    py_deps = set(python_info.get("dependencies", []))
    py_tools = set(python_info.get("tools", []))
    py_imports = set(str(item).lower() for item in python_info.get("imports", []))
    for key, label in python_labels.items():
        if key in py_deps or key in py_tools or key in py_imports:
            if label not in signals:
                signals.append(label)
    rust_info = _rust_project_info(root)
    if rust_info.get("is_rust_project"):
        signals.append("Rust")
        if rust_info.get("project_file"):
            signals.append("Cargo")
    rust_labels = {
        "tokio": "Tokio",
        "axum": "Axum",
        "actix-web": "Actix Web",
        "clap": "Clap",
        "serde": "Serde",
        "sqlx": "SQLx",
        "tauri": "Tauri",
    }
    rust_deps = set(rust_info.get("dependencies", []))
    for key, label in rust_labels.items():
        if key in rust_deps and label not in signals:
            signals.append(label)
    go_info = _go_project_info(root)
    if go_info.get("is_go_project"):
        signals.append("Go")
        if go_info.get("project_file"):
            signals.append("Go modules")
    go_requires = " ".join(go_info.get("requires", [])).lower()
    go_labels = {
        "github.com/gin-gonic/gin": "Gin",
        "github.com/labstack/echo": "Echo",
        "github.com/spf13/cobra": "Cobra",
        "google.golang.org/grpc": "gRPC",
        "github.com/gofiber/fiber": "Fiber",
    }
    for key, label in go_labels.items():
        if key in go_requires and label not in signals:
            signals.append(label)
    return signals


def _entrypoints(root: Path, pkg: Dict[str, Any]) -> List[str]:
    candidates: List[str] = []
    for field in ("main", "module", "types", "typings", "bin", "exports"):
        for ref in _package_path_values(pkg.get(field)):
            if isinstance(ref, str) and ref.startswith((".", "/")):
                candidates.append(ref)
    for relpath in (
        "src/index.ts",
        "src/main.ts",
        "src/app.ts",
        "src/App.tsx",
        "app/page.tsx",
        "pages/index.tsx",
        "index.html",
        "main.py",
        "Package.swift",
    ):
        if (root / relpath).exists():
            candidates.append(relpath)
    for pattern in ("Sources/*/main.swift", "Sources/**/*App.swift"):
        for path in root.glob(pattern):
            if path.is_file():
                candidates.append(_rel(path, root))
    python_info = _python_project_info(root)
    candidates.extend(python_info.get("entrypoints", []))
    candidates.extend(python_info.get("main_files", []))
    rust_info = _rust_project_info(root)
    candidates.extend(rust_info.get("main_files", []))
    go_info = _go_project_info(root)
    candidates.extend(go_info.get("main_files", []))
    return sorted(set(candidates))[:40]


def _git_status(root: Path, max_lines: int = 80) -> Dict[str, Any]:
    if not (root / ".git").exists():
        return {"is_git_repo": False, "changed_files": []}
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        return {
            "is_git_repo": True,
            "exit_code": proc.returncode,
            "changed_files": lines[:max_lines],
            "truncated": len(lines) > max_lines,
        }
    except Exception as exc:
        return {"is_git_repo": True, "error": str(exc), "changed_files": []}


def _state_path(root: Path) -> Path:
    return root / ".hermes-builder" / "state.json"


def _default_guard() -> Dict[str, Any]:
    return {
        "root_anchor": "",
        "root_anchor_set_at": "",
        "builder_verify_used": False,
        "last_verify_success": None,
        "last_verify_at": "",
        "last_verify_commands": [],
        "writes_since_budget": 0,
        "writes_since_verify": 0,
        "repair_patches_remaining": None,
        "failure_plan_required": False,
        "last_failure_plan_at": "",
        "last_failure_plan_command": "",
        "verify_required": False,
        "receipt_required": False,
        "last_budget_at": "",
        "last_budget_after_verify": False,
        "last_receipt_at": "",
        "last_receipt_blocked_reason": "",
        "language_profile": "unknown",
    }


def _guard_from_state(state: Dict[str, Any]) -> Dict[str, Any]:
    guard = state.get("guard")
    merged = _default_guard()
    if isinstance(guard, dict):
        merged.update(guard)
    return merged


def _anchor_guard(root: Path, guard: Dict[str, Any]) -> Dict[str, Any]:
    guard["root_anchor"] = str(root.resolve())
    if not guard.get("root_anchor_set_at"):
        guard["root_anchor_set_at"] = _now_iso()
    return guard


def _save_state(root: Path, state: Dict[str, Any]) -> None:
    state["project_path"] = str(root)
    state["updated_at"] = _now_iso()
    _write_json(_state_path(root), state)


def _default_state(root: Path) -> Dict[str, Any]:
    now = _now_iso()
    return {
        "version": 1,
        "project_path": str(root),
        "objective": "",
        "status": "active",
        "current_phase": "",
        "completed": [],
        "next_steps": [],
        "decisions": [],
        "files_touched": [],
        "verification": [],
        "notes": [],
        "guard": _default_guard(),
        "created_at": now,
        "updated_at": now,
    }


def _load_state(root: Path) -> Dict[str, Any]:
    state = _read_json(_state_path(root))
    if not isinstance(state, dict):
        return _default_state(root)
    base = _default_state(root)
    base.update(state)
    base["guard"] = _guard_from_state(base)
    return base


def _detect_language_profile(root: Path) -> str:
    if (root / "Package.swift").exists():
        return "swift"
    if (root / "Cargo.toml").exists():
        return "rust"
    if (root / "go.mod").exists():
        return "go"
    if _python_project_info(root).get("is_python_project"):
        return "python"
    if (root / "package.json").exists():
        return "node"
    return "unknown"


def _language_budget_defaults(root: Path) -> Dict[str, int]:
    profile = _detect_language_profile(root)
    defaults = {
        "node": {"max_source_files": 3, "max_test_files": 2, "max_source_dirs": 3},
        "swift": {"max_source_files": 6, "max_test_files": 3, "max_source_dirs": 3},
        "python": {"max_source_files": 5, "max_test_files": 3, "max_source_dirs": 3},
        "rust": {"max_source_files": 4, "max_test_files": 2, "max_source_dirs": 3},
        "go": {"max_source_files": 5, "max_test_files": 2, "max_source_dirs": 2},
    }
    return defaults.get(profile, {"max_source_files": 8, "max_test_files": 4, "max_source_dirs": 4})


def _expand_tool_path(raw: Any, base: Optional[Path] = None) -> Optional[Path]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            root = base or Path(os.getenv("TERMINAL_CWD") or os.getcwd()).expanduser()
            path = root / path
        return path.resolve()
    except Exception:
        return None


def _patch_file_candidates(patch_text: Any) -> List[str]:
    if not isinstance(patch_text, str):
        return []
    candidates: List[str] = []
    patterns = (
        r"^\*\*\*\s+(?:Add|Update|Delete) File:\s+(.+?)\s*$",
        r"^\*\*\*\s+Move to:\s+(.+?)\s*$",
        r"^(?:---|\+\+\+)\s+(?:[ab]/)?(.+?)\s*$",
    )
    for line in patch_text.splitlines():
        for pattern in patterns:
            match = re.match(pattern, line)
            if not match:
                continue
            value = match.group(1).strip()
            if value and value != "/dev/null":
                candidates.append(value)
            break
    return candidates


def _terminal_command_path_candidates(command: Any) -> List[str]:
    if not isinstance(command, str):
        return []
    candidates: List[str] = []
    patterns = (
        r"(?:^|\s)>\s*([^\s;&|]+)",
        r"(?:^|\s)>>\s*([^\s;&|]+)",
        r"\btee\s+(?:-a\s+)?([^\s;&|]+)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, command):
            value = match.group(1).strip().strip("\"'")
            if value and value != "/dev/null":
                candidates.append(value)
    try:
        for part in re.split(r"[;&|]+", command):
            tokens = shlex.split(part)
            if not tokens:
                continue
            executable = Path(tokens[0]).name
            if executable not in {"rm", "cp", "mv", "touch"}:
                continue
            for token in tokens[1:]:
                if token.startswith("-"):
                    continue
                candidates.append(token)
    except Exception:
        pass
    return candidates


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
    except Exception:
        return False


def _find_builder_root(path: Path) -> Optional[Path]:
    start = path if path.is_dir() else path.parent
    for candidate in (start, *start.parents):
        if _state_path(candidate).exists():
            return candidate
    return None


def _root_from_tool_args(tool_name: str, args: Any) -> Optional[Path]:
    if not isinstance(args, dict):
        return None

    explicit = _expand_tool_path(args.get("project_path"))
    if explicit and explicit.is_dir():
        return explicit

    base = _expand_tool_path(args.get("workdir")) if tool_name == "terminal" else None
    candidates: List[Path] = []
    for key in ("path", "file_path", "target"):
        value = args.get(key)
        if isinstance(value, str):
            expanded = _expand_tool_path(value, base=base)
            if expanded:
                candidates.append(expanded)

    if tool_name == "patch":
        for rel in _patch_file_candidates(args.get("patch")):
            expanded = _expand_tool_path(rel, base=base)
            if expanded:
                candidates.append(expanded)
    elif tool_name == "terminal":
        workdir = _expand_tool_path(args.get("workdir"))
        if workdir:
            candidates.append(workdir)
        else:
            cwd = _expand_tool_path(os.getenv("TERMINAL_CWD") or os.getcwd())
            if cwd:
                candidates.append(cwd)
        for rel in _terminal_command_path_candidates(args.get("command")):
            expanded = _expand_tool_path(rel, base=base)
            if expanded:
                candidates.append(expanded)

    for candidate in candidates:
        root = _find_builder_root(candidate)
        if root:
            return root
    return None


def _boundary_candidate_paths(tool_name: str, args: Any, root: Path) -> List[Path]:
    if not isinstance(args, dict):
        return []

    base = _expand_tool_path(args.get("workdir")) or root
    candidates: List[Path] = []
    for key in ("path", "file_path", "target", "destination"):
        expanded = _expand_tool_path(args.get(key), base=base)
        if expanded:
            candidates.append(expanded)

    if tool_name == "patch":
        for rel in _patch_file_candidates(args.get("patch")):
            expanded = _expand_tool_path(rel, base=base)
            if expanded:
                candidates.append(expanded)
    elif tool_name == "terminal":
        workdir = _expand_tool_path(args.get("workdir"))
        if workdir:
            candidates.append(workdir)
        for rel in _terminal_command_path_candidates(args.get("command")):
            expanded = _expand_tool_path(rel, base=base)
            if expanded:
                candidates.append(expanded)

    return candidates


def _is_raw_verifier_command(command: str) -> bool:
    patterns = [
        r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|build|lint|typecheck|check)\b",
        r"\bswift\s+(?:build|test)\b",
        r"\bcargo\s+(?:test|check|clippy|build)\b",
        r"\bgo\s+test\b",
        r"\buv\s+run\s+(?:pytest|python\s+-m\s+pytest|python\s+-m\s+compileall)\b",
        r"\bpython(?:3)?\s+-m\s+(?:pytest|compileall)\b",
        r"(^|[;&|]\s*)pytest(?:\s|$)",
    ]
    return any(re.search(pattern, command) for pattern in patterns)


def _is_terminal_file_mutation_command(command: str) -> bool:
    patterns = [
        r"(?s)\bcat\s+<<.+>",
        r"(?s)\btee\s+(?:-a\s+)?[^\s]+",
        r"(?s)\bprintf\b.+>",
        r"(?s)\b(?:python3?|node|ruby|perl)\s+-\s*<<",
        r"(?:^|[;&|]\s*)(?:rm|cp|mv|touch)\b",
    ]
    return any(re.search(pattern, command) for pattern in patterns)


def _result_has_error(result: Any, status: str = "") -> bool:
    if status and status not in {"ok", "success"}:
        return True
    parsed = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except Exception:
            return False
    if isinstance(parsed, dict):
        if parsed.get("error"):
            return True
        if parsed.get("success") is False:
            return True
    return False


def _verification_status(items: List[Any]) -> Optional[bool]:
    statuses: List[bool] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("zero_tests_detected"):
            statuses.append(False)
            continue
        if "success" in item and isinstance(item.get("success"), bool):
            statuses.append(bool(item.get("success")))
            continue
        if "exit_code" in item:
            statuses.append(item.get("exit_code") == 0 and not bool(item.get("timed_out")))
            continue
        if "failures" in item and isinstance(item.get("failures"), list):
            statuses.append(len(item.get("failures", [])) == 0)
    if not statuses:
        return None
    return all(statuses)


def _write_guard_block(root: Path, state: Dict[str, Any], guard: Dict[str, Any], reason: str, message: str) -> Dict[str, str]:
    guard["last_block_reason"] = reason
    guard["last_block_at"] = _now_iso()
    state["guard"] = guard
    try:
        _save_state(root, state)
    except Exception:
        pass
    return {"action": "block", "message": message}


def builder_pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[Dict[str, str]]:
    """Hermes pre_tool_call hook that enforces Builder Doctor staging."""
    if tool_name not in {"write_file", "patch", "terminal"}:
        return None
    root = _root_from_tool_args(tool_name, args)
    if root is None:
        return None
    state = _load_state(root)
    guard = _guard_from_state(state)
    guard = _anchor_guard(root, guard)
    state["guard"] = guard

    anchored_root = _expand_tool_path(guard.get("root_anchor")) or root
    for candidate in _boundary_candidate_paths(tool_name, args, anchored_root):
        if not _is_within_root(candidate, anchored_root):
            return _write_guard_block(
                root,
                state,
                guard,
                "root-boundary-blocked",
                (
                    "Builder Doctor blocked this tool call because it targets a path outside the mapped "
                    f"project root ({anchored_root}). Stay inside the project root or create/map a separate "
                    "project before editing there."
                ),
            )

    if tool_name == "terminal":
        command = str(args.get("command", "")) if isinstance(args, dict) else ""
        if guard.get("builder_verify_used") and _is_raw_verifier_command(command):
            return _write_guard_block(
                root,
                state,
                guard,
                "raw-verifier-blocked",
                (
                    "Builder Doctor blocked this raw terminal verifier because this project is already using "
                    "builder_verify. Call builder_verify with the same bounded command instead, then call "
                    "builder_resume, builder_budget(after_verify=true), and builder_receipt on success."
                ),
            )
        if _is_terminal_file_mutation_command(command):
            return _write_guard_block(
                root,
                state,
                guard,
                "terminal-file-mutation-blocked",
                (
                    "Builder Doctor blocked this terminal file mutation. Use write_file or patch "
                    "for source/test/config edits so the project-root, write-budget, verification, and "
                    "repair-plan guards can track the change."
                ),
            )
        return None

    if guard.get("failure_plan_required"):
        return _write_guard_block(
            root,
            state,
            guard,
            "failure-plan-required",
            (
                "Builder Doctor blocked this repair edit because the last builder_verify failed. "
                "Call builder_failure_plan with the failed verifier output first, then patch only "
                "the first diagnostic it identifies."
            ),
        )

    if guard.get("receipt_required"):
        return _write_guard_block(
            root,
            state,
            guard,
            "receipt-required",
            (
                "Builder Doctor blocked another file edit because builder_verify has already passed for this "
                "stage. Call builder_resume, builder_budget with after_verify=true, and builder_receipt. Put "
                "extra scope in next_steps instead of widening this stage."
            ),
        )

    repair_remaining = guard.get("repair_patches_remaining")
    if guard.get("last_verify_success") is False and isinstance(repair_remaining, int) and repair_remaining <= 0:
        guard["verify_required"] = True

    if guard.get("verify_required") or _safe_int(guard.get("writes_since_budget", 0), 0, min_value=0) >= 3:
        guard["verify_required"] = True
        return _write_guard_block(
            root,
            state,
            guard,
            "verify-required",
            (
                "Builder Doctor blocked this edit because the current stage has reached its write budget. "
                "Call builder_budget, then builder_verify with the smallest relevant command before writing more."
            ),
        )

    guard["writes_since_budget"] = _safe_int(guard.get("writes_since_budget", 0), 0, min_value=0) + 1
    guard["writes_since_verify"] = _safe_int(guard.get("writes_since_verify", 0), 0, min_value=0) + 1
    guard["language_profile"] = _detect_language_profile(root)

    if guard.get("last_verify_success") is False and isinstance(repair_remaining, int):
        repair_remaining = max(0, repair_remaining - 1)
        guard["repair_patches_remaining"] = repair_remaining
        if repair_remaining <= 0:
            guard["verify_required"] = True
    elif guard["writes_since_budget"] >= 3:
        guard["verify_required"] = True

    state["guard"] = guard
    try:
        _save_state(root, state)
    except Exception:
        pass
    return None


def builder_post_tool_call(
    tool_name: str = "",
    args: Any = None,
    result: Any = None,
    status: str = "",
    **_: Any,
) -> None:
    """Hermes post_tool_call hook retained for compatibility.

    Write reservations are made in the pre hook so same-turn multi-tool bursts
    cannot overrun the stage budget before the post hook sees the completed
    writes.
    """
    return


def _build_project_map(root: Path, max_files: int = 600) -> Dict[str, Any]:
    pkg = _read_json(root / "package.json") or {}
    scripts = _package_scripts(pkg)
    files = _walk_project_files(root, max_files=max_files)
    node_info = _node_project_info(root, pkg, files)
    swift_info = _swift_project_info(root, files)
    python_info = _python_project_info(root, files)
    rust_info = _rust_project_info(root, files)
    go_info = _go_project_info(root, files)
    ext_counts: Dict[str, int] = {}
    source_files: List[str] = []
    test_files: List[str] = []
    config_files: List[str] = []
    for path in files:
        rel = _rel(path, root)
        ext = path.suffix or "<none>"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
        lowered = rel.lower()
        if path.suffix in SOURCE_EXTS:
            source_files.append(rel)
        if any(marker in lowered for marker in TEST_MARKERS):
            test_files.append(rel)
        if path.name in {
            "package.json",
            "tsconfig.json",
            "vite.config.ts",
            "vite.config.js",
            "vitest.config.ts",
            "next.config.js",
            "next.config.mjs",
            "tailwind.config.js",
            "tailwind.config.ts",
            "eslint.config.js",
            "eslint.config.mjs",
            "pyproject.toml",
            "uv.lock",
            "requirements.txt",
            "requirements-dev.txt",
            "setup.py",
            "setup.cfg",
            "pytest.ini",
            "tox.ini",
            "noxfile.py",
            "ruff.toml",
            ".python-version",
            "Package.swift",
            "Package.resolved",
            "Cargo.toml",
            "Cargo.lock",
            "rust-toolchain",
            "rust-toolchain.toml",
            "go.mod",
            "go.sum",
        }:
            config_files.append(rel)
        if path.suffix in {".xcodeproj", ".xcworkspace"}:
            config_files.append(rel)

    top_dirs = sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and p.name not in SKIP_DIRS
    )[:80]
    top_files = sorted(
        p.name for p in root.iterdir()
        if p.is_file()
    )[:80]

    return {
        "name": pkg.get("name") or python_info.get("project_name") or rust_info.get("package_name") or go_info.get("module") or root.name,
        "project_path": str(root),
        "package_manager": _detect_package_manager(root),
        "scripts": scripts,
        "dependency_groups": _package_deps(pkg),
        "workspace_patterns": _workspace_patterns(pkg, root),
        "frameworks": _framework_signals(root, pkg),
        "entrypoints": _entrypoints(root, pkg),
        "node": node_info if node_info.get("is_node_project") else {},
        "swift": swift_info if swift_info.get("is_swift_project") else {},
        "python": python_info if python_info.get("is_python_project") else {},
        "rust": rust_info if rust_info.get("is_rust_project") else {},
        "go": go_info if go_info.get("is_go_project") else {},
        "top_level_dirs": top_dirs,
        "top_level_files": top_files,
        "config_files": sorted(set(config_files))[:80],
        "file_counts": {
            "sampled": len(files),
            "by_extension": dict(sorted(ext_counts.items())),
            "source_files": len(source_files),
            "test_files": len(test_files),
        },
        "source_sample": source_files[:120],
        "test_sample": test_files[:80],
        "git": _git_status(root),
    }


def _package_path_values(value: Any) -> List[str]:
    """Return relative file references from package.json string/list/dict fields."""
    refs: List[str] = []
    if isinstance(value, str):
        refs.append(value)
    elif isinstance(value, list):
        for item in value:
            refs.extend(_package_path_values(item))
    elif isinstance(value, dict):
        for item in value.values():
            refs.extend(_package_path_values(item))
    return refs


def _is_blocked_verify_command(command: str) -> bool:
    blocked = [
        r"\b(?:npm|pnpm|yarn|bun)\s+(?:install|add|i|ci)\b",
        r"\b(?:npm|pnpm|yarn|bun)\s+run\s+(?:dev|serve|start)\b",
        r"\b(?:pnpm|yarn|bun)\s+(?:dev|serve|start)\b",
        r"\bswift\s+run(?:\s|$)",
        r"\bcargo\s+(?:add|install|update|run)\b",
        r"\bgo\s+(?:get|install|run)\b",
        r"\bgo\s+mod\s+(?:download|tidy|vendor)\b",
        r"\buv\s+(?:add|remove|sync|lock|pip)\b",
        r"\bpython(?:3)?\s+-m\s+pip\s+install\b",
        r"\bpip(?:3)?\s+install\b",
        r"\bpoetry\s+(?:add|install|update|lock)\b",
        r"\bpipenv\s+install\b",
    ]
    return any(re.search(pattern, command) for pattern in blocked)


def _is_test_verify_command(command: str) -> bool:
    patterns = [
        r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test\b",
        r"\b(?:vitest|jest|node\s+--test)\b",
        r"\bswift\s+test\b",
        r"\bcargo\s+test\b",
        r"\bgo\s+test\b",
        r"\buv\s+run\s+(?:pytest|python\s+-m\s+pytest)\b",
        r"\bpython(?:3)?\s+-m\s+pytest\b",
        r"(^|[;&|]\s*)pytest(?:\s|$)",
    ]
    return any(re.search(pattern, command) for pattern in patterns)


def _zero_tests_detected(command: str, output: str) -> bool:
    if not _is_test_verify_command(command):
        return False

    if re.search(r"(?m)^#\s*tests\s+0\s*$", output) or re.search(r"(?m)^1\.\.0\s*$", output):
        return True
    if re.search(r"\bNo tests found\b", output, flags=re.IGNORECASE):
        return True
    if re.search(r"\bcollected\s+0\s+items\b", output) or re.search(r"\bno tests ran\b", output, flags=re.IGNORECASE):
        return True
    if re.search(r"\bExecuted\s+0\s+tests\b", output) or re.search(r"\b0\s+tests?\s+passed\b", output):
        return True

    if "cargo" in command and "test" in command:
        counts = [int(match.group(1)) for match in re.finditer(r"(?m)^running\s+(\d+)\s+tests?\s*$", output)]
        if counts and all(count == 0 for count in counts):
            return True

    if re.search(r"\bgo\s+test\b", command):
        meaningful = any(re.match(r"^(ok|PASS)\b", line.strip()) for line in output.splitlines())
        no_test_packages = any("[no test files]" in line for line in output.splitlines())
        if no_test_packages and not meaningful:
            return True

    return False


def _zero_test_failure(command: str, output_tail: str) -> Dict[str, Any]:
    return {
        "command": command,
        "exit_code": 0,
        "timed_out": False,
        "output_tail": output_tail,
        "zero_tests_detected": True,
        "diagnostics": [
            {
                "kind": "zero-tests",
                "message": "The verifier exited successfully but reported zero executed tests.",
            }
        ],
        "suggested_next": [
            "Add one focused test for the current kernel, then rerun builder_verify.",
            "Keep the repair to the smallest behavior under test; do not widen the feature set before the test exists.",
        ],
    }


def _default_verify_commands(root: Path, pkg: Dict[str, Any], scripts: Dict[str, Any]) -> List[str]:
    if (root / "Package.swift").exists():
        return ["swift build", "swift test"]
    if (root / "Cargo.toml").exists():
        return ["cargo test"]
    if (root / "go.mod").exists():
        return ["go test ./..."]
    python_info = _python_project_info(root)
    if python_info.get("is_python_project"):
        has_pytest = (
            "pytest" in python_info.get("dependencies", [])
            or "pytest" in python_info.get("tools", [])
            or bool(python_info.get("test_files"))
            or (root / "pytest.ini").exists()
        )
        runner = "uv run" if (root / "uv.lock").exists() or (root / "pyproject.toml").exists() else "python3 -m"
        if has_pytest:
            return ["uv run pytest"] if runner == "uv run" else ["python3 -m pytest"]
        return ["uv run python -m compileall -q ."] if runner == "uv run" else ["python3 -m compileall -q ."]
    for candidate in ("test", "build", "lint", "typecheck", "check"):
        if candidate in scripts:
            return [_node_script_command(root, candidate)]
    return []


def _ensure_required_verify_commands(root: Path, commands: List[str]) -> List[str]:
    profile = _detect_language_profile(root)
    normalized = list(commands)

    if profile == "rust":
        has_cargo_compile_check = any(
            re.search(r"\bcargo\s+(?:check|build|clippy)\b", command)
            for command in normalized
        )
        has_cargo_test = any(re.search(r"\bcargo\s+test\b", command) for command in normalized)
        if has_cargo_compile_check and not has_cargo_test:
            normalized.append("cargo test")

    return normalized


def _failure_guidance(command: str, output: str, timed_out: bool = False) -> Dict[str, Any]:
    diagnostics: List[Dict[str, Any]] = []
    suggested_next: List[str] = []
    seen: set[str] = set()

    for line in output.splitlines():
        m = re.match(r"^(.+?\.swift):(\d+):(\d+):\s*(error|warning):\s*(.+)$", line.strip())
        if not m:
            continue
        key = "|".join(m.groups())
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append({
            "kind": "swift-compiler",
            "file": m.group(1),
            "line": int(m.group(2)),
            "column": int(m.group(3)),
            "severity": m.group(4),
            "message": m.group(5),
        })
        if len(diagnostics) >= 12:
            break

    for line in output.splitlines():
        m = re.search(r"Test Case '([^']+)' failed \(([^)]+)\)", line)
        if not m:
            continue
        key = f"xctest|{m.group(1)}|{m.group(2)}"
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append({
            "kind": "xctest-failure",
            "test": m.group(1),
            "location": m.group(2),
            "message": "XCTest case failed.",
        })
        if len(diagnostics) >= 16:
            break

    for line in output.splitlines():
        m = re.search(r"Executed\s+(\d+)\s+tests?,\s+with\s+(\d+)\s+failures?", line)
        if not m:
            continue
        diagnostics.append({
            "kind": "test-summary",
            "tests": int(m.group(1)),
            "failures": int(m.group(2)),
            "message": line.strip(),
        })
        break

    pytest_failed = 0
    for line in output.splitlines():
        stripped = line.strip()
        m = re.match(r"^FAILED\s+(.+?)(?:\s+-\s+(.+))?$", stripped)
        if not m:
            continue
        diagnostics.append({
            "kind": "pytest-failure",
            "test": m.group(1),
            "message": (m.group(2) or "pytest test failed.")[:500],
        })
        pytest_failed += 1
        if pytest_failed >= 12:
            break

    traceback_file = None
    traceback_line = None
    traceback_func = None
    for line in output.splitlines():
        m = re.match(r'^\s*File "([^"]+\.py)", line (\d+), in (.+)$', line)
        if m:
            traceback_file = m.group(1)
            traceback_line = int(m.group(2))
            traceback_func = m.group(3)
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)):\s*(.+)$", line.strip())
        if m and traceback_file:
            diagnostics.append({
                "kind": "python-traceback",
                "file": traceback_file,
                "line": traceback_line,
                "function": traceback_func,
                "exception": m.group(1),
                "message": m.group(2),
            })
            break

    for line in output.splitlines():
        m = re.match(r"^\s*(.+?\.py):(\d+):(?:\d+:)?\s*(?:E\s+)?(.+)$", line)
        if not m:
            continue
        message = m.group(3).strip()
        if not message or message.startswith("in "):
            continue
        diagnostics.append({
            "kind": "python-location",
            "file": m.group(1),
            "line": int(m.group(2)),
            "message": message[:500],
        })
        if len(diagnostics) >= 20:
            break

    for line in output.splitlines():
        stripped = line.strip()
        m = re.match(r"^(.+?\.(?:ts|tsx|js|jsx|mjs|cjs|mts))\((\d+),(\d+)\):\s*(error|warning)\s+(TS\d+):\s*(.+)$", stripped)
        if m:
            key = f"ts|{'|'.join(m.groups())}"
            if key not in seen:
                seen.add(key)
                diagnostics.append({
                    "kind": "typescript-compiler",
                    "file": m.group(1),
                    "line": int(m.group(2)),
                    "column": int(m.group(3)),
                    "severity": m.group(4),
                    "code": m.group(5),
                    "message": m.group(6)[:500],
                })
            if len(diagnostics) >= 20:
                break
            continue
        m = re.match(r"^(.+?\.(?:ts|tsx|js|jsx|mjs|cjs|mts)):(\d+):(\d+):\s*(.+)$", stripped)
        if not m:
            continue
        key = f"node|{'|'.join(m.groups())}"
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append({
            "kind": "node-location",
            "file": m.group(1),
            "line": int(m.group(2)),
            "column": int(m.group(3)),
            "message": m.group(4)[:500],
        })
        if len(diagnostics) >= 20:
            break

    rust_context: Dict[str, Any] = {}
    for line in output.splitlines():
        stripped = line.strip()
        m = re.match(r"^(error(?:\[(E\d+)\])?|warning):\s*(.+)$", stripped)
        if m:
            rust_context = {
                "severity": "error" if m.group(1).startswith("error") else "warning",
                "code": m.group(2) or "",
                "message": m.group(3)[:500],
            }
            continue
        m = re.match(r"^-->\s*(.+?\.rs):(\d+):(\d+)$", stripped)
        if not m:
            continue
        key = f"rust|{'|'.join(m.groups())}|{rust_context.get('message', '')}"
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append({
            "kind": "rust-compiler",
            "file": m.group(1),
            "line": int(m.group(2)),
            "column": int(m.group(3)),
            "severity": rust_context.get("severity", "error"),
            "code": rust_context.get("code", ""),
            "message": rust_context.get("message", "Rust compiler diagnostic."),
        })
        if len(diagnostics) >= 20:
            break

    for line in output.splitlines():
        stripped = line.strip()
        m = re.match(r"^test\s+(.+?)\s+\.\.\.\s+FAILED$", stripped)
        if not m:
            continue
        key = f"rust-test|{m.group(1)}"
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append({
            "kind": "rust-test-failure",
            "test": m.group(1),
            "message": "Rust test failed.",
        })
        if len(diagnostics) >= 20:
            break

    for line in output.splitlines():
        stripped = line.strip()
        m = re.search(r"found packages\s+([A-Za-z_][A-Za-z0-9_]*)\s+\(([^)]+)\)\s+and\s+([A-Za-z_][A-Za-z0-9_]*)\s+\(([^)]+)\)", stripped)
        if not m:
            continue
        key = f"go-mixed-package|{'|'.join(m.groups())}"
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append({
            "kind": "go-mixed-packages",
            "packages": [m.group(1), m.group(3)],
            "files": [m.group(2), m.group(4)],
            "message": "Go directory contains mixed package names; use one package name per directory.",
        })
        if len(diagnostics) >= 20:
            break

    for line in output.splitlines():
        stripped = line.strip()
        m = re.match(r"^(.+?\.go):(\d+):(?:(\d+):)?\s*(.+)$", stripped)
        if not m:
            continue
        key = f"go|{'|'.join(str(part) for part in m.groups())}"
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append({
            "kind": "go-location",
            "file": m.group(1),
            "line": int(m.group(2)),
            "column": int(m.group(3) or 1),
            "message": m.group(4)[:500],
        })
        if len(diagnostics) >= 20:
            break

    for line in output.splitlines():
        stripped = line.strip()
        m = re.search(r"found packages\s+([A-Za-z_][A-Za-z0-9_]*)\s+\(([^)]+)\)\s+and\s+([A-Za-z_][A-Za-z0-9_]*)\s+\(([^)]+)\)", stripped)
        if m:
            key = f"go-mixed-package|{'|'.join(m.groups())}"
            if key not in seen:
                seen.add(key)
                diagnostics.append({
                    "kind": "go-mixed-packages",
                    "packages": [m.group(1), m.group(3)],
                    "files": [m.group(2), m.group(4)],
                    "message": "Go directory contains mixed package names; use one package name per directory.",
                })
            if len(diagnostics) >= 20:
                break
            continue
        m = re.match(r"^--- FAIL:\s+([A-Za-z0-9_./-]+)", stripped)
        if m:
            key = f"go-test|{m.group(1)}"
            if key not in seen:
                seen.add(key)
                diagnostics.append({
                    "kind": "go-test-failure",
                    "test": m.group(1),
                    "message": "Go test failed.",
                })
            if len(diagnostics) >= 20:
                break
            continue
        m = re.match(r"^FAIL\s+(.+)$", stripped)
        if not m:
            continue
        key = f"go-summary|{m.group(1)}"
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append({
            "kind": "go-test-summary",
            "package": m.group(1).split()[0],
            "message": stripped,
        })
        if len(diagnostics) >= 20:
            break

    if timed_out:
        suggested_next.append("Shrink the verification scope before increasing timeout; avoid launching apps or watchers.")
    if any(item.get("kind") in {"typescript-compiler", "node-location"} for item in diagnostics):
        suggested_next.append("Read the first reported JS/TS file, patch that diagnostic only, then rerun the same builder_verify command.")
    if any(item.get("kind") == "swift-compiler" and item.get("severity") == "error" for item in diagnostics):
        suggested_next.append("Read the first failing Swift file at the reported line, patch that compile error only, then rerun builder_verify.")
    if any(item.get("kind") == "xctest-failure" for item in diagnostics):
        suggested_next.append("Open the failing XCTest and the implementation under test; patch behavior, not the assertion, then rerun swift test.")
    if "swift build" in command and not diagnostics:
        suggested_next.append("Use builder_doctor focus=swift, then rerun swift build after one focused source pass.")
    if "swift test" in command and not any(item.get("kind") == "xctest-failure" for item in diagnostics):
        suggested_next.append("If the test log is noisy, rerun the smallest failing test command through builder_verify.")
    if any(item.get("kind") in {"pytest-failure", "python-traceback", "python-location"} for item in diagnostics):
        suggested_next.append("Read the first failing Python test/source file, patch the behavior or import error, then rerun builder_verify.")
    if ("pytest" in command or "python -m compileall" in command or "uv run python -m compileall" in command) and not any(str(item.get("kind", "")).startswith("python") or item.get("kind") == "pytest-failure" for item in diagnostics):
        suggested_next.append("Use builder_doctor focus=python, then rerun the smallest Python check through builder_verify.")
    if any(item.get("kind") in {"rust-compiler", "rust-test-failure"} for item in diagnostics):
        suggested_next.append("Read the first failing Rust source/test file, patch the behavior or compiler error, then rerun cargo test through builder_verify.")
    if "cargo" in command and not any(str(item.get("kind", "")).startswith("rust") for item in diagnostics):
        suggested_next.append("Use builder_doctor focus=rust, then rerun the smallest Cargo check through builder_verify.")
    if any(item.get("kind") == "go-mixed-packages" for item in diagnostics):
        suggested_next.append("Fix Go package declarations or move files so each directory has one package name, then rerun go test ./... through builder_verify.")
    if any(item.get("kind") in {"go-location", "go-test-failure", "go-test-summary"} for item in diagnostics):
        suggested_next.append("Read the first failing Go source/test file, patch the behavior or compiler error, then rerun go test ./... through builder_verify.")
    if "go test" in command and not any(str(item.get("kind", "")).startswith("go") for item in diagnostics):
        suggested_next.append("Use builder_doctor focus=go, then rerun the smallest Go package test through builder_verify.")
    if not suggested_next:
        suggested_next.append("Patch the first concrete failure in output_tail, then rerun only this command through builder_verify.")

    return {
        "diagnostics": diagnostics[:20],
        "suggested_next": suggested_next[:4],
    }


def _diagnostic_file(diagnostic: Dict[str, Any]) -> str:
    value = diagnostic.get("file") or diagnostic.get("location") or ""
    if isinstance(value, str) and ":" in value and not value.endswith((".swift", ".py", ".rs", ".go")):
        return value.split(":", 1)[0]
    return str(value or "")


def _diagnostic_summary(diagnostic: Dict[str, Any]) -> str:
    if not diagnostic:
        return "No structured diagnostic was parsed; use the output tail and rerun the smallest verifier."
    kind = str(diagnostic.get("kind", "diagnostic"))
    message = str(diagnostic.get("message") or diagnostic.get("test") or diagnostic.get("code") or "")
    location = _diagnostic_file(diagnostic)
    if diagnostic.get("line"):
        location = f"{location}:{diagnostic.get('line')}"
    if location:
        return f"{kind} at {location}: {message}".strip()
    return f"{kind}: {message}".strip()


def _language_from_command(root: Path, command: str) -> str:
    profile = _detect_language_profile(root)
    if profile != "unknown":
        return profile
    if re.search(r"\bcargo\b", command):
        return "rust"
    if re.search(r"\bswift\b", command):
        return "swift"
    if re.search(r"\bgo\s+test\b", command):
        return "go"
    if "pytest" in command or "python" in command:
        return "python"
    if re.search(r"\b(?:npm|pnpm|yarn|bun|node|vitest|jest)\b", command):
        return "node"
    return "unknown"


def _repair_recipe(language: str, diagnostic: Dict[str, Any], command: str, zero_tests: bool, timed_out: bool) -> Dict[str, Any]:
    kind = str(diagnostic.get("kind", ""))
    if timed_out:
        return {
            "mode": "timeout",
            "patch_policy": "Do not patch broadly from a timeout alone.",
            "steps": [
                "Shrink the verifier to the smallest package/test target that exercises the changed code.",
                "Only increase timeout after a narrower command still times out for a known reason.",
            ],
        }
    if zero_tests:
        test_steps = {
            "node": [
                "Create an actual discovered Node test file such as tests/<feature>.test.js or tests/test.js using node:test.",
                "Assert one core behavior from the current kernel; do not add new features.",
                f"Rerun builder_verify with `{command}`.",
            ],
            "swift": [
                "Create or update a real XCTest file under Tests/<Target>Tests with at least one test method.",
                "Assert one core behavior from the current kernel; do not weaken package settings.",
                f"Rerun builder_verify with `{command}`.",
            ],
            "python": [
                "Create an actual discovered Python test file such as tests/test_<feature>.py, not only tests/__init__.py.",
                "Use unittest.TestCase or test_ functions so the current command discovers at least one test.",
                f"Rerun builder_verify with `{command}`.",
            ],
            "rust": [
                "Create or update a real Rust test: an inline #[test] or a tests/<feature>.rs integration test.",
                "Assert one core behavior from the current kernel; do not only compile the crate.",
                f"Rerun builder_verify with `{command}`.",
            ],
            "go": [
                "Create or update a real Go *_test.go file with at least one TestXxx function in the same package.",
                "Assert one core behavior from the current kernel; keep one package name per directory.",
                f"Rerun builder_verify with `{command}`.",
            ],
        }
        return {
            "mode": f"{language}-add-focused-test",
            "patch_policy": "Add one real discovered test for the current kernel before adding features.",
            "steps": test_steps.get(language, [
                "Create or update the smallest real test file discovered by the current verifier.",
                f"Rerun builder_verify with `{command}`.",
            ]),
        }

    recipes: Dict[str, Dict[str, Any]] = {
        "node": {
            "mode": "node-focused-repair",
            "patch_policy": "Patch one JS/TS diagnostic or one failing assertion; do not change package manager or install dependencies.",
            "steps": [
                "Read the reported file and the nearest test or call site.",
                "Fix the first syntax/module/assertion problem only.",
                f"Rerun builder_verify with `{command}`.",
            ],
        },
        "swift": {
            "mode": "swift-focused-repair",
            "patch_policy": "Patch the first compiler/XCTest diagnostic; do not weaken XCTest assertions.",
            "steps": [
                "Read the failing Swift file at the reported line plus the XCTest if the failure is behavioral.",
                "Fix target names/imports/types before behavior changes.",
                f"Rerun builder_verify with `{command}`.",
            ],
        },
        "python": {
            "mode": "python-focused-repair",
            "patch_policy": "Patch the first traceback, import error, or pytest failure; do not add dependencies.",
            "steps": [
                "Read the reported test/source file and the function in the traceback.",
                "Fix behavior or import structure, not the test expectation unless the test is clearly wrong.",
                f"Rerun builder_verify with `{command}`.",
            ],
        },
        "rust": {
            "mode": "rust-focused-repair",
            "patch_policy": "Patch one compiler diagnostic or one failing test; do not create parallel replacement modules.",
            "steps": [
                "Read the reported .rs file and the failing test or caller.",
                "For borrow/type errors, prefer the smallest signature/data-ownership fix.",
                "For test failures, patch behavior rather than loosening assertions.",
                f"Rerun builder_verify with `{command}`.",
            ],
        },
        "go": {
            "mode": "go-focused-repair",
            "patch_policy": "Patch one package/compiler/test failure; keep one package name per directory.",
            "steps": [
                "If packages are mixed, fix declarations or move files before behavior work.",
                "Read the first failing Go source/test file and patch that cause only.",
                f"Rerun builder_verify with `{command}`.",
            ],
        },
        "unknown": {
            "mode": "generic-focused-repair",
            "patch_policy": "Patch one concrete failure only; avoid scope expansion.",
            "steps": [
                "Read the first file named in the output tail.",
                "Patch that diagnostic only.",
                f"Rerun builder_verify with `{command}`.",
            ],
        },
    }
    recipe = recipes.get(language, recipes["unknown"]).copy()
    if kind in {"go-mixed-packages"}:
        recipe["mode"] = "go-package-layout-repair"
        recipe["steps"] = [
            "Fix package declarations or move files so each directory has exactly one package name.",
            "Do not rewrite behavior until package setup passes.",
            f"Rerun builder_verify with `{command}`.",
        ]
    elif kind in {"rust-test-failure", "xctest-failure", "pytest-failure", "go-test-failure"}:
        recipe["mode"] = f"{language}-test-repair"
    return recipe


def _extract_failure_input(args: Dict[str, Any]) -> Dict[str, Any]:
    verification_result = args.get("verification_result")
    if isinstance(verification_result, str):
        try:
            verification_result = json.loads(verification_result)
        except Exception:
            verification_result = None

    if isinstance(verification_result, dict):
        failures = verification_result.get("failures")
        if isinstance(failures, list) and failures:
            first = failures[0] if isinstance(failures[0], dict) else {}
            return {
                "command": str(first.get("command") or args.get("command") or ""),
                "output_tail": str(first.get("output_tail") or args.get("output_tail") or ""),
                "timed_out": bool(first.get("timed_out")),
                "zero_tests": bool(first.get("zero_tests_detected")),
            }
        commands = verification_result.get("commands")
        if isinstance(commands, list):
            for item in commands:
                if not isinstance(item, dict):
                    continue
                if item.get("exit_code") != 0 or item.get("timed_out") or item.get("zero_tests_detected"):
                    return {
                        "command": str(item.get("command") or args.get("command") or ""),
                        "output_tail": str(item.get("output_tail") or args.get("output_tail") or ""),
                        "timed_out": bool(item.get("timed_out")),
                        "zero_tests": bool(item.get("zero_tests_detected")),
                    }

    return {
        "command": str(args.get("command") or ""),
        "output_tail": str(args.get("output_tail") or ""),
        "timed_out": bool(args.get("timed_out", False)),
        "zero_tests": bool(args.get("zero_tests_detected", False)),
    }


# ---------------------------------------------------------------------------
# builder_failure_plan
# ---------------------------------------------------------------------------

def builder_failure_plan(args: Dict[str, Any], **_: Any) -> str:
    project_path = args.get("project_path", "")
    root = Path(project_path).resolve()
    if not root.is_dir():
        return _json({
            "success": False,
            "project_path": project_path,
            "summary": "Project path does not exist or is not a directory.",
            "repair_plan": {},
        })

    failure = _extract_failure_input(args)
    command = failure["command"] or "builder_verify"
    output_tail = failure["output_tail"]
    timed_out = bool(failure["timed_out"])
    zero_tests = bool(failure["zero_tests"]) or _zero_tests_detected(command, output_tail)
    guidance = _zero_test_failure(command, output_tail) if zero_tests else _failure_guidance(command, output_tail, timed_out=timed_out)
    diagnostics = guidance.get("diagnostics", [])
    first = diagnostics[0] if diagnostics else {}
    language = _language_from_command(root, command)
    target_file = _diagnostic_file(first)
    target_path = str((root / target_file).resolve()) if target_file and not Path(target_file).is_absolute() else target_file
    recipe = _repair_recipe(language, first, command, zero_tests, timed_out)

    read_files = [target_path] if target_path else []
    if language == "rust" and first.get("kind") == "rust-test-failure":
        read_files.append(str(root / "src"))
    if language == "swift" and first.get("kind") == "xctest-failure":
        read_files.append(str(root / "Tests"))

    state_recorded = False
    state_warning = ""
    try:
        state = _load_state(root)
        guard = _anchor_guard(root, _guard_from_state(state))
        guard["failure_plan_required"] = False
        guard["last_failure_plan_at"] = _now_iso()
        guard["last_failure_plan_command"] = command
        guard["language_profile"] = language
        state["guard"] = guard
        _save_state(root, state)
        state_recorded = True
    except Exception as exc:
        state_warning = f"Could not update failure-plan state: {exc}"

    return _json({
        "success": True,
        "project_path": str(root),
        "summary": _diagnostic_summary(first),
        "language_profile": language,
        "command": command,
        "first_diagnostic": first,
        "diagnostics": diagnostics[:8],
        "repair_plan": {
            "read_files": read_files[:4],
            "patch_budget": "one focused patch, maximum two before rerunning builder_verify",
            "patch_target": target_path,
            "patch_policy": recipe["patch_policy"],
            "steps": recipe["steps"],
            "next_verify_command": command,
            "stop_conditions": [
                "Stop if the next builder_verify reports a different first failure; create a new builder_failure_plan.",
                "Stop after two patches without a passing builder_verify and receipt the remaining failure.",
            ],
        },
        "recipe": recipe,
        "suggested_next": guidance.get("suggested_next", [])[:4],
        "state_recorded": state_recorded,
        "state_warning": state_warning,
    })


# ---------------------------------------------------------------------------
# builder_doctor
# ---------------------------------------------------------------------------

def builder_doctor(args: Dict[str, Any], **_: Any) -> str:
    project_path = args.get("project_path", "")
    focus = args.get("focus", "all")

    root = Path(project_path).resolve()
    if not root.is_dir():
        return _json({
            "success": False,
            "project_path": project_path,
            "summary": "Project path does not exist or is not a directory.",
            "findings": [],
        })

    findings: List[Dict[str, Any]] = []

    pkg = _read_json(root / "package.json") or {}
    scripts = _package_scripts(pkg)
    node_info = _node_project_info(root, pkg)
    swift_info = _swift_project_info(root)
    python_info = _python_project_info(root)
    rust_info = _rust_project_info(root)
    go_info = _go_project_info(root)

    # --- 0) SwiftPM/XCTest structure risks ---
    if focus in ("all", "swift", "swiftpm", "testing", "build"):
        if swift_info.get("is_swift_project"):
            imports = set(swift_info.get("imports", []))
            ui_imports = sorted(imports.intersection({"SwiftUI", "AppKit", "UIKit", "SpriteKit", "SceneKit"}))
            if swift_info.get("package_file") and not any(swift_info.get("targets", {}).values()):
                findings.append({
                    "severity": "warning",
                    "code": "swiftpm-no-targets-detected",
                    "file": "Package.swift",
                    "message": "Package.swift exists, but no regular, executable, or test targets were detected.",
                    "evidence": "No .target/.executableTarget/.testTarget name entries found.",
                    "suggested_fix": "Check Package.swift syntax and declare explicit targets for Sources and Tests.",
                })
            if swift_info.get("package_file") and not swift_info.get("test_target_dirs") and not swift_info.get("targets", {}).get("test"):
                findings.append({
                    "severity": "info",
                    "code": "swiftpm-no-tests",
                    "file": "Package.swift",
                    "message": "SwiftPM package has no detected test target or Tests directory.",
                    "evidence": "No Tests/* directory and no .testTarget entry found.",
                    "suggested_fix": "Add a Tests/<TargetName>Tests target before relying on behavior claims.",
                })
            if swift_info.get("products", {}).get("executable") or swift_info.get("targets", {}).get("executable"):
                if not swift_info.get("main_files"):
                    findings.append({
                        "severity": "warning",
                        "code": "swiftpm-executable-missing-main",
                        "file": "Sources",
                        "message": "Executable SwiftPM target detected without a sampled @main or main.swift entrypoint.",
                        "evidence": f"executable targets={swift_info.get('targets', {}).get('executable', [])}, main_files={swift_info.get('main_files', [])}",
                        "suggested_fix": "Add a main.swift file or an @main type in the executable target.",
                    })
            if ui_imports and swift_info.get("package_file") and not swift_info.get("platforms_declared"):
                findings.append({
                    "severity": "warning",
                    "code": "swiftpm-ui-platforms-missing",
                    "file": "Package.swift",
                    "message": "Swift UI/game imports are present but Package.swift has no explicit platforms declaration.",
                    "evidence": f"imports={ui_imports}",
                    "suggested_fix": "Declare supported platforms in Package.swift, such as platforms: [.macOS(.v14)].",
                })
            for target in swift_info.get("targets", {}).get("regular", []) + swift_info.get("targets", {}).get("executable", []):
                package_text = _swift_package_text(root)
                if target not in swift_info.get("source_target_dirs", []) and "path:" not in package_text:
                    findings.append({
                        "severity": "warning",
                        "code": "swiftpm-target-dir-missing",
                        "file": "Package.swift",
                        "message": "SwiftPM target name does not match a conventional Sources/<Target> directory.",
                        "evidence": f"target={target}, source_target_dirs={swift_info.get('source_target_dirs', [])}",
                        "suggested_fix": "Create Sources/<Target>/ or add an explicit path: argument for the target.",
                    })
            for target in swift_info.get("targets", {}).get("test", []):
                package_text = _swift_package_text(root)
                if target not in swift_info.get("test_target_dirs", []) and "path:" not in package_text:
                    findings.append({
                        "severity": "warning",
                        "code": "swiftpm-test-target-dir-missing",
                        "file": "Package.swift",
                        "message": "SwiftPM test target name does not match a conventional Tests/<Target> directory.",
                        "evidence": f"target={target}, test_target_dirs={swift_info.get('test_target_dirs', [])}",
                        "suggested_fix": "Create Tests/<Target>/ or add an explicit path: argument for the test target.",
                    })

    # --- 0b) Python/pytest/uv structure risks ---
    if focus in ("all", "python", "pyproject", "testing", "build", "package"):
        if python_info.get("is_python_project"):
            pyproject = _read_toml(root / "pyproject.toml") or {}
            project = pyproject.get("project") if isinstance(pyproject.get("project"), dict) else {}
            tool = pyproject.get("tool") if isinstance(pyproject.get("tool"), dict) else {}
            if python_info.get("project_file") and not python_info.get("project_name"):
                findings.append({
                    "severity": "warning",
                    "code": "python-project-name-missing",
                    "file": "pyproject.toml",
                    "message": "Python project metadata has no project name.",
                    "evidence": "No [project].name or [tool.poetry].name detected.",
                    "suggested_fix": "Add [project] name = \"...\" for a package-style project.",
                })
            if python_info.get("project_file") and not python_info.get("requires_python"):
                findings.append({
                    "severity": "info",
                    "code": "python-requires-python-missing",
                    "file": "pyproject.toml",
                    "message": "Python version requirement is not declared.",
                    "evidence": "No [project].requires-python detected.",
                    "suggested_fix": "Add requires-python, for example >=3.11, to make verification reproducible.",
                })
            if python_info.get("python_file_count", 0) > 2 and not python_info.get("test_files"):
                findings.append({
                    "severity": "warning",
                    "code": "python-tests-missing",
                    "file": "tests",
                    "message": "Python project has source files but no sampled tests.",
                    "evidence": f"python_file_count={python_info.get('python_file_count')}, test_files=[]",
                    "suggested_fix": "Add pytest or unittest coverage before relying on behavior claims.",
                })
            if python_info.get("test_files") and "pytest" not in python_info.get("dependencies", []) and "pytest" not in python_info.get("tools", []) and not (root / "pytest.ini").exists():
                findings.append({
                    "severity": "info",
                    "code": "python-test-runner-implicit",
                    "file": "pyproject.toml",
                    "message": "Tests exist, but pytest is not declared in project metadata or config.",
                    "evidence": f"sample_tests={python_info.get('test_files', [])[:5]}",
                    "suggested_fix": "Declare pytest as a dev dependency or add a clear unittest/pytest command.",
                })
            manager_signals = [
                name for name in ("uv.lock", "poetry.lock", "Pipfile", "Pipfile.lock", "requirements.txt")
                if (root / name).exists()
            ]
            if len({name.split(".", 1)[0] for name in manager_signals}) > 1:
                findings.append({
                    "severity": "warning",
                    "code": "python-mixed-dependency-managers",
                    "file": ".",
                    "message": "Multiple Python dependency-manager signals were detected.",
                    "evidence": f"signals={manager_signals}",
                    "suggested_fix": "Pick one project authority, preferably uv/pyproject for this Hermes lane.",
                })
            pytest_cfg = tool.get("pytest") if isinstance(tool.get("pytest"), dict) else {}
            pytest_opts = pytest_cfg.get("ini_options") if isinstance(pytest_cfg.get("ini_options"), dict) else {}
            testpaths = pytest_opts.get("testpaths", []) if isinstance(pytest_opts, dict) else []
            configured_testpaths = testpaths if isinstance(testpaths, list) else []
            for testpath in configured_testpaths:
                if isinstance(testpath, str) and not (root / testpath).exists():
                    findings.append({
                        "severity": "warning",
                        "code": "python-pytest-testpath-missing",
                        "file": "pyproject.toml",
                        "message": "pytest testpaths references a missing path.",
                        "evidence": testpath,
                        "suggested_fix": "Create the test path or update tool.pytest.ini_options.testpaths.",
                    })
            app_like = any(name in python_info.get("dependencies", []) for name in ("fastapi", "flask", "typer", "click"))
            if app_like and not python_info.get("entrypoints") and not python_info.get("main_files"):
                findings.append({
                    "severity": "info",
                    "code": "python-app-entrypoint-missing",
                    "file": "pyproject.toml",
                    "message": "App-like Python dependencies were detected, but no script or main file was sampled.",
                    "evidence": f"dependencies={python_info.get('dependencies', [])[:20]}",
                    "suggested_fix": "Add a [project.scripts] entrypoint or a clear main.py/cli.py.",
                })

    # --- 0c) Node/TypeScript package-manager, script, and test risks ---
    if focus in ("all", "node", "javascript", "typescript", "testing", "scripts", "build", "package"):
        if node_info.get("is_node_project"):
            lock_manager_by_file = {
                "package-lock.json": "npm",
                "pnpm-lock.yaml": "pnpm",
                "yarn.lock": "yarn",
                "bun.lock": "bun",
                "bun.lockb": "bun",
            }
            lock_managers = {
                lock_manager_by_file[name]
                for name in node_info.get("lockfiles", [])
                if name in lock_manager_by_file
            }
            if node_info.get("source_file_count", 0) > 0 and not node_info.get("package_file"):
                findings.append({
                    "severity": "warning",
                    "code": "node-source-without-package-json",
                    "file": ".",
                    "message": "JavaScript/TypeScript source files exist, but package.json was not found.",
                    "evidence": f"source_file_count={node_info.get('source_file_count')}",
                    "suggested_fix": "Add package.json with explicit scripts before relying on Node verification.",
                })
            if len(lock_managers) > 1:
                findings.append({
                    "severity": "warning",
                    "code": "node-mixed-lockfiles",
                    "file": ".",
                    "message": "Multiple Node package-manager lockfiles were detected.",
                    "evidence": f"lockfiles={node_info.get('lockfiles', [])}",
                    "suggested_fix": "Keep one lockfile/package manager for this project lane.",
                })
            package_manager_field = str(node_info.get("package_manager_field") or "")
            field_match = re.match(r"^([A-Za-z0-9_-]+)@", package_manager_field)
            field_manager = field_match.group(1) if field_match else ""
            detected_manager = node_info.get("package_manager")
            if field_manager and detected_manager and field_manager != detected_manager:
                findings.append({
                    "severity": "warning",
                    "code": "node-package-manager-mismatch",
                    "file": "package.json",
                    "message": "packageManager field does not match the detected lockfile manager.",
                    "evidence": f"packageManager={package_manager_field!r}, detected={detected_manager!r}, lockfiles={node_info.get('lockfiles', [])}",
                    "suggested_fix": "Align packageManager and the lockfile before installing or running scripts.",
                })
            tsconfig_exists = any(root.glob("tsconfig*.json"))
            if node_info.get("typescript_file_count", 0) > 0 and not tsconfig_exists:
                findings.append({
                    "severity": "warning",
                    "code": "typescript-config-missing",
                    "file": "tsconfig.json",
                    "message": "TypeScript-like files exist, but no tsconfig was found.",
                    "evidence": f"typescript_file_count={node_info.get('typescript_file_count')}",
                    "suggested_fix": "Add a tsconfig.json or use a framework config that clearly owns TypeScript compilation.",
                })
            deps = set(node_info.get("dependencies", []))
            if "typescript" in deps and not tsconfig_exists:
                findings.append({
                    "severity": "info",
                    "code": "typescript-dependency-without-config",
                    "file": "package.json",
                    "message": "typescript is declared but no tsconfig file was sampled.",
                    "evidence": "dependency=typescript",
                    "suggested_fix": "Add tsconfig.json or document the framework-generated TypeScript config.",
                })
            if node_info.get("test_files") and "test" not in scripts:
                findings.append({
                    "severity": "warning",
                    "code": "node-tests-without-test-script",
                    "file": "package.json",
                    "message": "Test files exist, but package.json has no test script.",
                    "evidence": f"sample_tests={node_info.get('test_files', [])[:5]}",
                    "suggested_fix": "Add a bounded test script so builder_verify can validate behavior.",
                })
            if node_info.get("typescript_file_count", 0) > 0 and not any(name in scripts for name in ("typecheck", "build", "test", "check")):
                findings.append({
                    "severity": "info",
                    "code": "typescript-no-verification-script",
                    "file": "package.json",
                    "message": "TypeScript files exist, but no typecheck/build/test/check script was found.",
                    "evidence": f"scripts={list(scripts.keys())}",
                    "suggested_fix": "Add a typecheck or build script and use builder_verify to run it.",
                })
            has_deps = any(group.get("count", 0) > 0 for group in _package_deps(pkg).values())
            if node_info.get("package_file") and has_deps and not node_info.get("lockfiles"):
                findings.append({
                    "severity": "info",
                    "code": "node-lockfile-missing",
                    "file": "package.json",
                    "message": "package.json declares dependencies, but no lockfile was found.",
                    "evidence": "No package-lock.json, pnpm-lock.yaml, yarn.lock, bun.lock, or bun.lockb sampled.",
                    "suggested_fix": "Use one package manager consistently and commit the generated lockfile for reproducible builds.",
                })

    # --- 0d) Rust/Cargo structure risks ---
    if focus in ("all", "rust", "cargo", "testing", "build", "package"):
        if rust_info.get("is_rust_project"):
            cargo = _read_toml(root / "Cargo.toml") or {}
            if not rust_info.get("project_file"):
                findings.append({
                    "severity": "warning",
                    "code": "rust-source-without-cargo-toml",
                    "file": ".",
                    "message": "Rust source files exist, but Cargo.toml was not found.",
                    "evidence": f"rust_file_count={rust_info.get('rust_file_count')}",
                    "suggested_fix": "Add Cargo.toml with package or workspace metadata before relying on Cargo verification.",
                })
            if rust_info.get("project_file") and not rust_info.get("package_name") and not rust_info.get("workspace_members"):
                findings.append({
                    "severity": "warning",
                    "code": "cargo-package-name-missing",
                    "file": "Cargo.toml",
                    "message": "Cargo.toml has no [package].name and no workspace members.",
                    "evidence": "No package.name or workspace.members detected.",
                    "suggested_fix": "Add [package] name/version/edition or define a workspace with members.",
                })
            if rust_info.get("project_file") and rust_info.get("package_name") and not rust_info.get("edition"):
                findings.append({
                    "severity": "info",
                    "code": "cargo-edition-missing",
                    "file": "Cargo.toml",
                    "message": "Cargo package edition is not declared.",
                    "evidence": f"package={rust_info.get('package_name')}",
                    "suggested_fix": "Set edition = \"2021\" or \"2024\" intentionally.",
                })
            if rust_info.get("rust_file_count", 0) > 2 and not rust_info.get("test_files") and not rust_info.get("has_inline_tests"):
                findings.append({
                    "severity": "warning",
                    "code": "rust-tests-missing",
                    "file": "tests",
                    "message": "Rust project has source files but no sampled tests.",
                    "evidence": f"rust_file_count={rust_info.get('rust_file_count')}, inline_tests={rust_info.get('has_inline_tests')}",
                    "suggested_fix": "Add unit tests with #[test] or integration tests under tests/ before relying on behavior claims.",
                })
            if rust_info.get("project_file") and rust_info.get("package_name") and not rust_info.get("main_files") and not rust_info.get("lib_files"):
                findings.append({
                    "severity": "warning",
                    "code": "cargo-entrypoint-missing",
                    "file": "src",
                    "message": "Cargo package metadata exists, but no sampled src/main.rs or src/lib.rs entrypoint was found.",
                    "evidence": f"main_files={rust_info.get('main_files')}, lib_files={rust_info.get('lib_files')}",
                    "suggested_fix": "Add src/main.rs for a binary or src/lib.rs for a library package.",
                })
            missing_members: List[str] = []
            for member in rust_info.get("workspace_members", []):
                if not list(root.glob(member)):
                    missing_members.append(member)
            if missing_members:
                findings.append({
                    "severity": "warning",
                    "code": "cargo-workspace-member-missing",
                    "file": "Cargo.toml",
                    "message": "Cargo workspace member patterns did not match any paths.",
                    "evidence": f"missing_members={missing_members[:10]}",
                    "suggested_fix": "Create the member package paths or update workspace.members.",
                })
            if isinstance(cargo.get("dependencies"), dict) and "tokio" in cargo.get("dependencies", {}) and "async-trait" in cargo.get("dependencies", {}) and not rust_info.get("test_files") and not rust_info.get("has_inline_tests"):
                findings.append({
                    "severity": "info",
                    "code": "rust-async-without-tests",
                    "file": "Cargo.toml",
                    "message": "Async Rust dependencies were detected without sampled async/unit tests.",
                    "evidence": "dependencies include tokio and async-trait.",
                    "suggested_fix": "Add focused async tests with #[tokio::test] for core behavior.",
                })

    # --- 0e) Go module structure risks ---
    if focus in ("all", "go", "gomod", "testing", "build", "package"):
        if go_info.get("is_go_project"):
            if not go_info.get("project_file"):
                findings.append({
                    "severity": "warning",
                    "code": "go-source-without-module",
                    "file": ".",
                    "message": "Go source files exist, but go.mod was not found.",
                    "evidence": f"go_file_count={go_info.get('go_file_count')}",
                    "suggested_fix": "Add go.mod with module and go version before relying on go test ./....",
                })
            if go_info.get("project_file") and not go_info.get("module"):
                findings.append({
                    "severity": "warning",
                    "code": "go-module-name-missing",
                    "file": "go.mod",
                    "message": "go.mod has no module line.",
                    "evidence": "module line missing.",
                    "suggested_fix": "Add a module path to go.mod.",
                })
            if go_info.get("project_file") and not go_info.get("go_version"):
                findings.append({
                    "severity": "info",
                    "code": "go-version-missing",
                    "file": "go.mod",
                    "message": "go.mod has no go version line.",
                    "evidence": "go directive missing.",
                    "suggested_fix": "Add a go version directive such as go 1.23.",
                })
            if go_info.get("go_file_count", 0) > 2 and not go_info.get("test_files"):
                findings.append({
                    "severity": "warning",
                    "code": "go-tests-missing",
                    "file": ".",
                    "message": "Go module has source files but no sampled *_test.go files.",
                    "evidence": f"go_file_count={go_info.get('go_file_count')}, test_files=[]",
                    "suggested_fix": "Add focused *_test.go coverage before relying on behavior claims.",
                })
            packages_by_dir: Dict[str, set[str]] = {}
            for src in root.rglob("*.go"):
                if any(part in SKIP_DIRS for part in src.parts):
                    continue
                txt = _read_text(src) or ""
                m = re.search(r"^\s*package\s+([A-Za-z_][A-Za-z0-9_]*)", txt, re.MULTILINE)
                if not m:
                    continue
                rel_dir = _rel(src.parent, root)
                packages_by_dir.setdefault(rel_dir, set()).add(m.group(1))
            for rel_dir, packages in sorted(packages_by_dir.items()):
                if len(packages) <= 1:
                    continue
                findings.append({
                    "severity": "error",
                    "code": "go-mixed-packages-in-directory",
                    "file": rel_dir,
                    "message": "A Go directory contains multiple package names.",
                    "evidence": f"packages={sorted(packages)}",
                    "suggested_fix": "Use one package name per directory, or move tests/helpers into a separate directory.",
                })

    # --- 1) Workspace package dependency gaps ---
    if focus in ("all", "workspace"):
        workspace_roots: List[Path] = []
        # pnpm-workspace.yaml
        pnpm_ws = root / "pnpm-workspace.yaml"
        if pnpm_ws.exists():
            txt = _read_text(pnpm_ws) or ""
            for line in txt.splitlines():
                line = line.strip()
                if line.startswith("- "):
                    pattern = line[2:].strip()
                    for p in root.glob(pattern):
                        if p.is_dir() and (p / "package.json").exists():
                            workspace_roots.append(p)
        # package.json workspaces
        ws_field = pkg.get("workspaces")
        if isinstance(ws_field, list):
            for entry in ws_field:
                if isinstance(entry, str):
                    for p in root.glob(entry):
                        if p.is_dir() and (p / "package.json").exists():
                            workspace_roots.append(p)
                elif isinstance(entry, dict) and "packages" in entry:
                    for pat in entry["packages"]:
                        for p in root.glob(pat):
                            if p.is_dir() and (p / "package.json").exists():
                                workspace_roots.append(p)
        # Deduplicate
        seen = set()
        for pkg_dir in workspace_roots:
            if pkg_dir in seen:
                continue
            seen.add(pkg_dir)
            child_pkg = _read_json(pkg_dir / "package.json") or {}
            child_name = child_pkg.get("name")
            if not child_name:
                findings.append({
                    "severity": "warning",
                    "code": "workspace-missing-name",
                    "file": _rel(pkg_dir / "package.json", root),
                    "message": "Workspace package is missing a name field.",
                    "evidence": "package.json has no 'name'.",
                    "suggested_fix": "Add a unique 'name' to the workspace package.json.",
                })

    # --- 2) TypeScript module/moduleResolution mismatch ---
    if focus in ("all", "typescript"):
        tsconfigs = list(root.glob("tsconfig*.json"))
        for cfg in tsconfigs:
            data = _read_json(cfg) or {}
            compiler = data.get("compilerOptions", {}) or {}
            module = compiler.get("module")
            module_res = compiler.get("moduleResolution")
            if module in ("NodeNext", "Node16") and module_res not in ("NodeNext", "Node16", "Node"):
                findings.append({
                    "severity": "error",
                    "code": "tsconfig-module-resolution-mismatch",
                    "file": _rel(cfg, root),
                    "message": "module is NodeNext/Node16 but moduleResolution is not compatible.",
                    "evidence": f"module={module!r}, moduleResolution={module_res!r}",
                    "suggested_fix": "Set moduleResolution to 'NodeNext' or 'Node16' to match module.",
                })
            if module in ("ES2020", "ES2021", "ES2022") and module_res == "Node":
                findings.append({
                    "severity": "warning",
                    "code": "tsconfig-module-resolution-mismatch",
                    "file": _rel(cfg, root),
                    "message": "ES module target with Node moduleResolution may cause ESM/CJS interop issues.",
                    "evidence": f"module={module!r}, moduleResolution={module_res!r}",
                    "suggested_fix": "Consider setting moduleResolution to 'Bundler' or 'NodeNext'.",
                })

    # --- 3) NodeNext ESM import extension problems ---
    if focus in ("all", "esm"):
        tsconfigs = list(root.glob("tsconfig*.json"))
        is_node_next = False
        for cfg in tsconfigs:
            data = _read_json(cfg) or {}
            compiler = data.get("compilerOptions", {}) or {}
            if compiler.get("module") in ("NodeNext", "Node16"):
                is_node_next = True
                break
        if is_node_next or (pkg.get("type") == "module"):
            # Scan source files for bare specifiers without extensions where ESM requires them
            source_exts = {".ts", ".tsx", ".js", ".jsx", ".mts", ".mjs"}
            scanned = 0
            for src in root.rglob("*"):
                if scanned > 300:
                    break
                if src.suffix not in source_exts:
                    continue
                if "node_modules" in src.parts or ".git" in src.parts:
                    continue
                txt = _read_text(src)
                if not txt:
                    continue
                scanned += 1
                for m in re.finditer(r"import\s+.*?from\s+['\"]([^'\"]+?)['\"]", txt):
                    spec = m.group(1)
                    if spec.startswith(".") and not spec.startswith("./") and not spec.startswith("../"):
                        # relative without ./ or ../
                        findings.append({
                            "severity": "error",
                            "code": "esm-relative-import-malformed",
                            "file": _rel(src, root),
                            "message": "Relative import missing './' or '../' prefix.",
                            "evidence": spec,
                            "suggested_fix": "Prefix relative imports with './' or '../'.",
                        })
                    if spec.startswith(".") and "." not in Path(spec).name:
                        findings.append({
                            "severity": "warning",
                            "code": "esm-missing-extension",
                            "file": _rel(src, root),
                            "message": "Relative import may be missing file extension required under NodeNext ESM.",
                            "evidence": spec,
                            "suggested_fix": "Add the file extension (e.g. '.js', '.ts') to the import specifier.",
                        })
                    if spec.startswith("~") or spec.startswith("@"):
                        findings.append({
                            "severity": "warning",
                            "code": "esm-alias-without-extension",
                            "file": _rel(src, root),
                            "message": "Aliased import may need a file extension under NodeNext ESM.",
                            "evidence": spec,
                            "suggested_fix": "Ensure the alias resolver preserves extensions or use a bundler.",
                        })

    # --- 4) package main/types/exports mismatches ---
    if focus in ("all", "package"):
        for field in ("main", "module", "types", "typings", "exports"):
            val = pkg.get(field)
            if not val:
                continue
            refs = _package_path_values(val)
            if not refs and not isinstance(val, str):
                findings.append({
                    "severity": "warning",
                    "code": "package-field-unhandled-shape",
                    "file": "package.json",
                    "message": f"package.json '{field}' has a shape the doctor did not turn into file references.",
                    "evidence": f"{field}: {type(val).__name__}",
                    "suggested_fix": "Review the field manually and ensure referenced output files are generated.",
                })
                continue
            for ref_value in refs:
                if not ref_value.startswith((".", "/")):
                    continue
                ref = (root / ref_value).resolve() if not ref_value.startswith("/") else Path(ref_value)
                if ref.exists():
                    continue
                findings.append({
                    "severity": "error",
                    "code": "package-field-missing-file",
                    "file": "package.json",
                    "message": f"package.json '{field}' points to a missing file.",
                    "evidence": f"{field}: {ref_value}",
                    "suggested_fix": f"Create the referenced file or update '{field}' to the correct path.",
                })

    # --- 5) vitest jsdom/setup issues ---
    if focus in ("all", "testing"):
        # vite.config.* / vitest.config.*
        config_candidates = []
        for pat in ("vite.config.*", "vitest.config.*", "vite.config.ts", "vitest.config.ts", "vite.config.js", "vitest.config.js", "vite.config.mts", "vitest.config.mts"):
            config_candidates.extend(root.glob(pat))
        for cfg in config_candidates:
            txt = _read_text(cfg) or ""
            if "jsdom" in txt and "test" in txt.lower():
                # crude check for setupFiles
                if "setupFiles" not in txt and "setup" not in txt:
                    findings.append({
                        "severity": "warning",
                        "code": "vitest-setup-missing",
                        "file": _rel(cfg, root),
                        "message": "Vitest jsdom environment detected but setup files may be missing.",
                        "evidence": "jsdom environment referenced without setupFiles.",
                        "suggested_fix": "Add setupFiles to the vitest test configuration if DOM APIs need polyfills.",
                    })

    # --- 6) Missing scripts ---
    if focus in ("all", "scripts"):
        if (root / "package.json").exists():
            wanted = ["test", "build", "lint"]
            for s in wanted:
                if s not in scripts:
                    findings.append({
                        "severity": "info",
                        "code": "script-missing",
                        "file": "package.json",
                        "message": f"Common script '{s}' is not defined in package.json.",
                        "evidence": f"scripts: {list(scripts.keys())}",
                        "suggested_fix": f"Add a '{s}' script if the project uses it.",
                    })

    # --- 7) Stale generated output assumptions ---
    if focus in ("all", "build"):
        for d in ("dist", "build", "out", ".next", "coverage"):
            p = root / d
            if p.exists() and not p.is_dir():
                continue
            if p.exists() and p.is_dir() and not any(p.iterdir()):
                findings.append({
                    "severity": "info",
                    "code": "stale-generated-output",
                    "file": d,
                    "message": "Generated output directory exists but is empty.",
                    "evidence": f"{d}/ is empty.",
                    "suggested_fix": "Run the build script to populate generated output, or remove the stale directory.",
                })

    summary = f"Scanned {root}. Found {len(findings)} issue(s)."
    return _json({
        "success": True,
        "project_path": project_path,
        "summary": summary,
        "findings": findings,
    })


# ---------------------------------------------------------------------------
# builder_verify
# ---------------------------------------------------------------------------

def builder_verify(args: Dict[str, Any], **_: Any) -> str:
    project_path = args.get("project_path", "")
    commands = args.get("commands")
    timeout_seconds = _safe_int(args.get("timeout_seconds", 120), default=120, min_value=5, max_value=1800)

    root = Path(project_path).resolve()
    if not root.is_dir():
        return _json({
            "success": False,
            "project_path": project_path,
            "commands": [],
            "failures": [],
            "summary": "Project path does not exist or is not a directory.",
        })

    pkg = _read_json(root / "package.json") or {}
    scripts = pkg.get("scripts", {}) or {}

    if isinstance(commands, str):
        commands = [commands]
    elif commands:
        commands = [str(cmd) for cmd in commands if str(cmd).strip()]

    if not commands:
        commands = _default_verify_commands(root, pkg, scripts)
        if not commands:
            return _json({
                "success": False,
                "project_path": project_path,
                "commands": [],
                "failures": [],
                "summary": "No commands provided and no known default verifier found (SwiftPM, Cargo, Go module, Python project, or Node test/build/lint/typecheck/check script).",
            })
    else:
        commands = _ensure_required_verify_commands(root, commands)

    env = os.environ.copy()
    env["CI"] = "1"
    env["NO_COLOR"] = "1"

    results = []
    failures = []
    any_failure = False

    for cmd in commands:
        if _is_blocked_verify_command(cmd):
            any_failure = True
            failure = {
                "command": cmd,
                "exit_code": None,
                "timed_out": False,
                "duration_seconds": 0,
                "output_tail": "Blocked by builder_verify safety policy: installs and long-lived dev/start/serve commands are not allowed.",
                "diagnostics": [],
                "suggested_next": ["Use a bounded build/test/check command instead of installs, dev servers, or app runs."],
            }
            failures.append(failure)
            results.append(failure)
            continue
        start = __import__("time").time()
        timed_out = False
        exit_code = None
        output_tail = ""
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            exit_code = proc.returncode
            combined = proc.stdout + "\n" + proc.stderr
            lines = combined.splitlines()
            tail_lines = lines[-50:] if len(lines) > 50 else lines
            output_tail = "\n".join(tail_lines)
            zero_tests_detected = exit_code == 0 and _zero_tests_detected(cmd, output_tail)
            if exit_code != 0:
                any_failure = True
                failure = {
                    "command": cmd,
                    "exit_code": exit_code,
                    "timed_out": False,
                    "output_tail": output_tail,
                }
                failure.update(_failure_guidance(cmd, output_tail))
                failures.append(failure)
            elif zero_tests_detected:
                any_failure = True
                failure = _zero_test_failure(cmd, output_tail)
                failures.append(failure)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            any_failure = True
            combined = (exc.stdout or "") + "\n" + (exc.stderr or "")
            lines = combined.splitlines()
            tail_lines = lines[-50:] if len(lines) > 50 else lines
            output_tail = "\n".join(tail_lines)
            failure = {
                "command": cmd,
                "exit_code": None,
                "timed_out": True,
                "output_tail": output_tail,
            }
            failure.update(_failure_guidance(cmd, output_tail, timed_out=True))
            failures.append(failure)
        except Exception as exc:
            any_failure = True
            output_tail = str(exc)
            failure = {
                "command": cmd,
                "exit_code": None,
                "timed_out": False,
                "output_tail": output_tail,
            }
            failure.update(_failure_guidance(cmd, output_tail))
            failures.append(failure)
        duration = round(__import__("time").time() - start, 3)
        record = {
            "command": cmd,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_seconds": duration,
            "output_tail": output_tail,
            "zero_tests_detected": exit_code == 0 and not timed_out and _zero_tests_detected(cmd, output_tail),
        }
        if record["zero_tests_detected"]:
            record.update(_zero_test_failure(cmd, output_tail))
            record["duration_seconds"] = duration
        elif exit_code != 0 or timed_out:
            record.update(_failure_guidance(cmd, output_tail, timed_out=timed_out))
        results.append(record)

    summary = f"Ran {len(commands)} command(s); {len(failures)} failure(s)."
    next_required: List[str] = []
    if any_failure:
        next_required.append("Patch one concrete failure, then rerun builder_verify; do not add new features.")
        next_required.append("Make at most two focused patches before rerunning builder_verify; do not stack broad patch bursts.")
    else:
        next_required.extend([
            "builder_verify recorded this verification in .hermes-builder/state.json.",
            "Call builder_budget with after_verify=true before writing more files.",
            "If this is the intended stage, call builder_receipt now instead of rerunning the same command through terminal.",
        ])
    state_recorded = False
    state_warning = ""
    verification_records = [
        {
            "command": item.get("command", ""),
            "exit_code": item.get("exit_code"),
            "timed_out": bool(item.get("timed_out")),
            "duration_seconds": item.get("duration_seconds", 0),
            "zero_tests_detected": bool(item.get("zero_tests_detected")),
            "success": item.get("exit_code") == 0 and not item.get("timed_out") and not item.get("zero_tests_detected"),
            "recorded_at": _now_iso(),
        }
        for item in results
        if isinstance(item, dict)
    ]
    try:
        state = _load_state(root)
        state["verification"] = _append_unique(
            list(state.get("verification", [])),
            verification_records,
            max_items=120,
        )
        guard = _anchor_guard(root, _guard_from_state(state))
        guard["builder_verify_used"] = True
        guard["last_verify_success"] = not any_failure
        guard["last_verify_at"] = _now_iso()
        guard["last_verify_commands"] = [str(command) for command in commands]
        guard["writes_since_budget"] = 0
        guard["writes_since_verify"] = 0
        guard["verify_required"] = False
        guard["language_profile"] = _detect_language_profile(root)
        if any_failure:
            guard["receipt_required"] = False
            guard["repair_patches_remaining"] = 2
            guard["failure_plan_required"] = True
        else:
            guard["receipt_required"] = True
            guard["repair_patches_remaining"] = None
            guard["failure_plan_required"] = False
        state["guard"] = guard
        _save_state(root, state)
        state_recorded = True
    except Exception as exc:
        state_warning = f"Could not update .hermes-builder/state.json: {exc}"
    return _json({
        "success": not any_failure,
        "project_path": project_path,
        "commands": results,
        "failures": failures,
        "summary": summary,
        "next_required": next_required,
        "state_recorded": state_recorded,
        "state_warning": state_warning,
    })


# ---------------------------------------------------------------------------
# builder_budget
# ---------------------------------------------------------------------------

def builder_budget(args: Dict[str, Any], **_: Any) -> str:
    project_path = args.get("project_path", "")
    phase = str(args.get("phase", "kernel")).strip().lower() or "kernel"
    after_verify = bool(args.get("after_verify", False))
    max_source_files = _safe_int(args.get("max_source_files", 8), default=8, min_value=1, max_value=500)
    max_test_files = _safe_int(args.get("max_test_files", 4), default=4, min_value=0, max_value=500)
    max_source_dirs = _safe_int(args.get("max_source_dirs", 4), default=4, min_value=1, max_value=200)

    root = Path(project_path).resolve()
    if not root.is_dir():
        return _json({
            "success": False,
            "project_path": project_path,
            "summary": "Project path does not exist or is not a directory.",
            "over_budget": False,
            "actions": ["Create the root folder, then call builder_map and builder_plan."],
        })

    language_defaults = _language_budget_defaults(root)
    if "max_source_files" not in args:
        max_source_files = language_defaults["max_source_files"]
    if "max_test_files" not in args:
        max_test_files = language_defaults["max_test_files"]
    if "max_source_dirs" not in args:
        max_source_dirs = language_defaults["max_source_dirs"]

    code_exts = {
        ".c", ".cc", ".cpp", ".go", ".h", ".hpp", ".js", ".jsx",
        ".mjs", ".py", ".rs", ".swift", ".ts", ".tsx", ".vue",
    }
    sampled = _walk_project_files(root, max_files=3000)
    source_files = [
        path for path in sampled
        if path.suffix in code_exts and not any(part in SKIP_DIRS for part in path.parts)
    ]
    test_files = [
        path for path in source_files
        if path.name.endswith(("_test.go", "Tests.swift"))
        or any(marker in path.parts for marker in ("tests", "test", "__tests__"))
        or ".test." in path.name
        or ".spec." in path.name
        or path.name.startswith("test_")
    ]
    source_dirs = sorted({_rel(path.parent, root) for path in source_files})
    go_info = _go_project_info(root, sampled)
    mixed_package_dirs = go_info.get("mixed_package_dirs", {}) if go_info.get("is_go_project") else {}

    issues: List[Dict[str, Any]] = []
    if len(source_files) > max_source_files:
        issues.append({
            "code": "source-file-budget-exceeded",
            "message": "The current phase has more source files than the staged-kernel budget.",
            "evidence": f"source_files={len(source_files)} > max_source_files={max_source_files}",
        })
    if len(test_files) > max_test_files:
        issues.append({
            "code": "test-file-budget-exceeded",
            "message": "The current phase has more test files than the staged-kernel budget.",
            "evidence": f"test_files={len(test_files)} > max_test_files={max_test_files}",
        })
    if len(source_dirs) > max_source_dirs:
        issues.append({
            "code": "source-dir-budget-exceeded",
            "message": "The current phase spans too many source directories/packages.",
            "evidence": f"source_dirs={len(source_dirs)} > max_source_dirs={max_source_dirs}",
        })
    if mixed_package_dirs:
        issues.append({
            "code": "go-mixed-package-dirs",
            "message": "One or more Go directories contain multiple package names.",
            "evidence": json.dumps(mixed_package_dirs, ensure_ascii=True, sort_keys=True),
        })

    actions: List[str] = []
    if issues:
        actions.extend([
            "Stop adding files for this phase now.",
            "If verification has not passed for the current file set, run builder_verify before any more write_file or patch calls.",
            "If verification already passed, call builder_resume and builder_receipt, then defer the extra scope to a later phase.",
        ])
    elif after_verify:
        actions.extend([
            "The current phase is within budget after verification.",
            "builder_verify has already recorded the verification; call builder_resume if you need to add checkpoint notes.",
            "Call builder_receipt if this stage is complete.",
        ])
    else:
        actions.extend([
            "The current phase is within budget.",
            "The next source/test batch is capped at two files or three write_file/patch calls.",
            "After that capped batch, run builder_budget and builder_verify before expanding scope.",
        ])

    guard: Dict[str, Any] = {}
    state_recorded = False
    state_warning = ""
    try:
        state = _load_state(root)
        guard = _anchor_guard(root, _guard_from_state(state))
        guard["last_budget_at"] = _now_iso()
        guard["last_budget_after_verify"] = after_verify
        guard["language_profile"] = _detect_language_profile(root)
        if after_verify:
            guard["writes_since_budget"] = 0
            guard["verify_required"] = False
            if guard.get("last_verify_success") is True:
                guard["receipt_required"] = True
        elif not guard.get("verify_required"):
            guard["writes_since_budget"] = 0
        state["guard"] = guard
        _save_state(root, state)
        state_recorded = True
    except Exception as exc:
        state_warning = f"Could not update .hermes-builder/state.json: {exc}"

    return _json({
        "success": True,
        "project_path": str(root),
        "phase": phase,
        "summary": f"Budget check: {len(source_files)} source file(s), {len(test_files)} test file(s), {len(source_dirs)} source dir(s), {len(issues)} issue(s).",
        "language_profile": _detect_language_profile(root),
        "counts": {
            "source_files": len(source_files),
            "test_files": len(test_files),
            "source_dirs": len(source_dirs),
        },
        "limits": {
            "max_source_files": max_source_files,
            "max_test_files": max_test_files,
            "max_source_dirs": max_source_dirs,
        },
        "over_budget": bool(issues),
        "hard_stop": bool(issues),
        "allowed_next_tools": (
            ["builder_verify", "builder_resume", "builder_receipt"]
            if issues
            else ["builder_resume", "builder_receipt"]
            if after_verify
            else ["write_file", "patch", "builder_budget", "builder_verify"]
        ),
        "issues": issues,
        "actions": actions,
        "enforcement": {
            "state_recorded": state_recorded,
            "state_warning": state_warning,
            "writes_since_budget": guard.get("writes_since_budget", 0),
            "writes_since_verify": guard.get("writes_since_verify", 0),
            "verify_required": bool(guard.get("verify_required", False)),
            "receipt_required": bool(guard.get("receipt_required", False)),
            "repair_patches_remaining": guard.get("repair_patches_remaining"),
        },
        "source_sample": [_rel(path, root) for path in source_files[:40]],
        "test_sample": [_rel(path, root) for path in test_files[:40]],
    })


# ---------------------------------------------------------------------------
# builder_map
# ---------------------------------------------------------------------------

def builder_map(args: Dict[str, Any], **_: Any) -> str:
    project_path = args.get("project_path", "")
    max_files = _safe_int(args.get("max_files", 600), default=600, min_value=50, max_value=3000)

    root = Path(project_path).resolve()
    if not root.is_dir():
        return _json({
            "success": False,
            "project_path": project_path,
            "summary": "Project path does not exist or is not a directory.",
            "map": {},
        })

    project_map = _build_project_map(root, max_files=max_files)
    recommendations = [
        "Call builder_doctor before broad edits.",
        "Call builder_plan for a phase plan before making more than a small file batch.",
        "Call builder_resume after each completed phase.",
        "Call builder_verify with the smallest relevant script after each phase.",
        "Call builder_receipt before the final answer.",
    ]
    if project_map.get("node"):
        manager = project_map.get("node", {}).get("package_manager") or "npm"
        recommendations.insert(0, f"Node project detected; builder_verify uses {manager} run <script> for package scripts when commands are omitted.")
    if project_map.get("swift"):
        recommendations.insert(0, "SwiftPM detected; builder_verify defaults to swift build and swift test when commands are omitted.")
    if project_map.get("python"):
        recommendations.insert(0, "Python project detected; builder_verify defaults to uv run pytest/python compileall or python3 -m pytest/compileall when commands are omitted.")
    if project_map.get("rust"):
        recommendations.insert(0, "Cargo project detected; builder_verify defaults to cargo test when commands are omitted.")
    if project_map.get("go"):
        recommendations.insert(0, "Go module detected; builder_verify defaults to go test ./... when commands are omitted.")
    if not project_map["scripts"] and not project_map.get("swift") and not project_map.get("python") and not project_map.get("rust") and not project_map.get("go"):
        recommendations.insert(0, "No package scripts found; create explicit build/test scripts before relying on verification.")
    elif not project_map.get("swift") and not project_map.get("python") and not project_map.get("rust") and not project_map.get("go") and "test" not in project_map["scripts"]:
        recommendations.insert(0, "No test script found; add one if this build has logic that needs proof.")

    state_recorded = False
    state_warning = ""
    try:
        state = _load_state(root)
        guard = _anchor_guard(root, _guard_from_state(state))
        guard["language_profile"] = _detect_language_profile(root)
        state["guard"] = guard
        _save_state(root, state)
        state_recorded = True
    except Exception as exc:
        state_warning = f"Could not create .hermes-builder/state.json marker: {exc}"

    return _json({
        "success": True,
        "project_path": str(root),
        "summary": (
            f"Mapped {project_map['name']} with {len(project_map['scripts'])} script(s), "
            f"{len(project_map['frameworks'])} framework signal(s), "
            f"{project_map['file_counts']['source_files']} source file(s) sampled."
        ),
        "map": project_map,
        "recommended_next": recommendations,
        "state_recorded": state_recorded,
        "state_warning": state_warning,
    })


# ---------------------------------------------------------------------------
# builder_plan
# ---------------------------------------------------------------------------

def builder_plan(args: Dict[str, Any], **_: Any) -> str:
    project_path = args.get("project_path", "")
    objective = _clip(args.get("objective", ""), 1200)
    max_phases = _safe_int(args.get("max_phases", 7), default=7, min_value=3, max_value=10)

    root = Path(project_path).resolve()
    if not root.is_dir():
        return _json({
            "success": False,
            "project_path": project_path,
            "summary": "Project path does not exist or is not a directory.",
            "phases": [],
        })

    project_map = _build_project_map(root, max_files=500)
    scripts = project_map.get("scripts", {})
    frameworks = set(project_map.get("frameworks", []))
    node_info = project_map.get("node", {})
    swift_info = project_map.get("swift", {})
    python_info = project_map.get("python", {})
    rust_info = project_map.get("rust", {})
    go_info = project_map.get("go", {})
    has_package = (root / "package.json").exists()
    has_swiftpm = bool(swift_info)
    has_python = bool(python_info)
    has_rust = bool(rust_info)
    has_go = bool(go_info)
    is_newish = not (has_package or has_swiftpm or has_python or has_rust or has_go) or project_map["file_counts"]["source_files"] < 8
    verify_command = None
    if has_swiftpm:
        verify_command = "builder_verify commands: swift build, swift test"
    elif has_rust:
        verify_command = "builder_verify command: cargo test"
    elif has_go:
        verify_command = "builder_verify command: go test ./..."
    elif has_python:
        if (root / "uv.lock").exists() or (root / "pyproject.toml").exists():
            verify_command = "builder_verify command: uv run pytest or uv run python -m compileall -q ."
        else:
            verify_command = "builder_verify command: python3 -m pytest or python3 -m compileall -q ."
    else:
        for script in ("test", "build", "lint", "typecheck", "check"):
            if script in scripts:
                verify_command = _node_script_command(root, script)
                break
    if verify_command is None and has_package:
        manager = node_info.get("package_manager") or _node_package_manager(root) or "npm"
        verify_command = f"{manager} run build or {manager} run test after adding scripts"

    phases: List[Dict[str, Any]] = [
        {
            "id": "map-and-risk",
            "title": "Map the project and risks",
            "goal": "Use builder_map and builder_doctor before broad edits so the build starts from facts.",
            "max_file_batch": 0,
            "tools": ["builder_map", "builder_doctor"],
            "done_when": "Known scripts, entrypoints, framework signals, and high-severity findings are understood.",
        },
        {
            "id": "state-seed",
            "title": "Seed resumable state",
            "goal": "Record the objective, first phase, important decisions, and immediate next steps.",
            "max_file_batch": 0,
            "tools": ["builder_resume"],
            "done_when": "A project-local .hermes-builder/state.json exists with objective and next steps.",
        },
    ]

    if is_newish:
        phases.append({
            "id": "scaffold",
            "title": "Scaffold the thin working slice",
            "goal": "Create the minimal runnable shell, scripts, and directory layout before adding breadth.",
            "max_file_batch": 4,
            "tools": ["write_file", "builder_budget", "builder_resume", "builder_verify"],
            "done_when": "The project has explicit build/test scripts and a runnable minimal path.",
            "verification": verify_command or "Add a small self-check command, then run builder_verify.",
        })
    else:
        phases.append({
            "id": "localize-change",
            "title": "Localize the change surface",
            "goal": "Identify the smallest set of modules needed for the objective and avoid unrelated rewrites.",
            "max_file_batch": 4,
            "tools": ["read_file", "builder_resume"],
            "done_when": "Files to touch are listed and the plan has a narrow module boundary.",
        })

    domain_title = "Core behavior"
    if "React" in frameworks or "Vite" in frameworks or "Next.js" in frameworks:
        domain_title = "Core UI and state behavior"
    elif "Express" in frameworks or "Fastify" in frameworks:
        domain_title = "Core API and service behavior"
    elif {"SwiftUI", "AppKit", "SpriteKit", "SceneKit"}.intersection(frameworks):
        domain_title = "Core Swift app/game behavior"
    elif "SwiftPM" in frameworks:
        domain_title = "Core Swift package behavior"
    elif {"FastAPI", "Django", "Flask"}.intersection(frameworks):
        domain_title = "Core Python service behavior"
    elif {"Typer", "Click"}.intersection(frameworks):
        domain_title = "Core Python CLI behavior"
    elif "Python" in frameworks:
        domain_title = "Core Python package behavior"
    elif {"Axum", "Actix Web", "Tokio"}.intersection(frameworks):
        domain_title = "Core Rust service behavior"
    elif "Clap" in frameworks:
        domain_title = "Core Rust CLI behavior"
    elif "Rust" in frameworks:
        domain_title = "Core Rust package behavior"
    elif {"Gin", "Echo", "Fiber", "gRPC"}.intersection(frameworks):
        domain_title = "Core Go service behavior"
    elif "Cobra" in frameworks:
        domain_title = "Core Go CLI behavior"
    elif "Go" in frameworks:
        domain_title = "Core Go module behavior"

    phases.extend([
        {
            "id": "core",
            "title": domain_title,
            "goal": "Implement the main user-visible behavior as a small vertical slice.",
            "max_file_batch": 4,
            "tools": ["write_file", "builder_resume", "builder_verify"],
            "done_when": "The central workflow works with representative data and no placeholders in the critical path.",
            "verification": verify_command or "Run the smallest available check through builder_verify.",
        },
        {
            "id": "hardening",
            "title": "Hardening and edge cases",
            "goal": "Add validation, empty/error states, persistence boundaries, and focused tests.",
            "max_file_batch": 4,
            "tools": ["builder_doctor", "builder_budget", "builder_resume", "builder_verify"],
            "done_when": "High-risk branches have tests or an explicit manual proof path.",
            "verification": verify_command or "Run targeted tests through builder_verify.",
        },
        {
            "id": "integration",
            "title": "Integration pass",
            "goal": "Run the strongest bounded verification available and fix only failures tied to the objective.",
            "max_file_batch": 3,
            "tools": ["builder_doctor", "builder_budget", "builder_verify", "builder_resume"],
            "done_when": "Build/test/lint status is recorded with command names and outcomes.",
            "verification": "builder_verify with build/test/lint commands that exist.",
        },
        {
            "id": "receipt",
            "title": "Final receipt",
            "goal": "Summarize files, decisions, verification, and remaining limitations before the final answer.",
            "max_file_batch": 0,
            "tools": ["builder_receipt"],
            "done_when": "A compact receipt exists and includes proof commands or explains why they were unavailable.",
        },
    ])

    phases = phases[:max_phases]
    return _json({
        "success": True,
        "project_path": str(root),
        "objective": objective,
        "summary": f"Created a {len(phases)}-phase build plan for {project_map['name']}.",
        "project_signals": {
            "name": project_map["name"],
            "package_manager": project_map["package_manager"],
            "frameworks": project_map["frameworks"],
            "scripts": scripts,
            "node": node_info,
            "swift": swift_info,
            "python": python_info,
            "rust": rust_info,
            "go": go_info,
            "is_newish": is_newish,
        },
        "phases": phases,
        "rules": [
            "Touch no more than the phase max_file_batch before verifying or recording state.",
            "Hard stop after 4 file writes/patches in one phase: run builder_verify before expanding scope.",
            "Call builder_budget after each source/test batch and after successful verification; if it reports over_budget, stop adding scope and receipt/defer.",
            "After builder_budget reports within budget, the next source/test batch is still capped at two files or three write_file/patch calls before builder_verify.",
            "For super-complex objectives, build a verified kernel first and record deferred layers instead of attempting the full system in one turn.",
            "Before writing source, choose stable language identity and keep it consistent: Node module style, Swift target names, Python import root, Rust crate/module names, and one Go package name per directory.",
            "For Go, if builder_map shows mixed_package_dirs or builder_verify reports found packages X and Y, fix package declarations or move files before behavior work.",
            "After the first builder_verify, fix only verification failures; do not add new features.",
            "After any failed builder_verify, make at most two focused patches before rerunning builder_verify.",
            "After builder_verify succeeds, do not rerun the same command via terminal; call builder_resume, builder_budget, then builder_receipt.",
            "If verification still fails after one focused fix pass, call builder_receipt and report the remaining failure.",
            "Before the session reaches roughly 45k context tokens, force a checkpoint/receipt instead of starting another feature pass.",
            "Use builder_resume after every phase boundary or meaningful change in direction.",
            "If builder_verify times out, shrink the command scope before increasing timeout.",
        ],
    })


# ---------------------------------------------------------------------------
# builder_resume
# ---------------------------------------------------------------------------

def builder_resume(args: Dict[str, Any], **_: Any) -> str:
    project_path = args.get("project_path", "")
    action = str(args.get("action", "read")).strip().lower() or "read"
    max_items = _safe_int(args.get("max_items", 60), default=60, min_value=10, max_value=200)

    root = Path(project_path).resolve()
    if not root.is_dir():
        return _json({
            "success": False,
            "project_path": project_path,
            "summary": "Project path does not exist or is not a directory.",
            "state": {},
        })

    path = _state_path(root)
    if action not in {"read", "update", "replace", "clear"}:
        return _json({
            "success": False,
            "project_path": str(root),
            "state_path": str(path),
            "summary": "Unsupported action. Use read, update, replace, or clear.",
            "state": _load_state(root),
        })

    if action == "clear":
        try:
            if path.exists():
                path.unlink()
            return _json({
                "success": True,
                "project_path": str(root),
                "state_path": str(path),
                "summary": "Cleared builder resume state.",
                "state": _default_state(root),
            })
        except Exception as exc:
            return _json({
                "success": False,
                "project_path": str(root),
                "state_path": str(path),
                "summary": f"Failed to clear builder resume state: {exc}",
                "state": _load_state(root),
            })

    existed = path.exists()
    state = _default_state(root) if action == "replace" else _load_state(root)
    verification_incoming: List[Any] = []

    if action in {"update", "replace"}:
        scalar_fields = {
            "objective": "objective",
            "status": "status",
            "phase": "current_phase",
            "current_phase": "current_phase",
        }
        for arg_key, state_key in scalar_fields.items():
            if arg_key in args and args.get(arg_key) is not None:
                state[state_key] = _clip(args.get(arg_key), 2000)

        list_fields = {
            "completed": "completed",
            "next_steps": "next_steps",
            "decisions": "decisions",
            "files": "files_touched",
            "files_touched": "files_touched",
            "verification": "verification",
            "notes": "notes",
        }
        for arg_key, state_key in list_fields.items():
            if arg_key not in args:
                continue
            incoming = _listify(args.get(arg_key))
            if state_key == "verification":
                incoming = [
                    item if isinstance(item, dict) else {"note": _clip(item, 2000), "recorded_at": _now_iso()}
                    for item in incoming
                ]
                verification_incoming = incoming
            else:
                incoming = [_clip(item, 1200) for item in incoming]
            state[state_key] = _append_unique(list(state.get(state_key, [])), incoming, max_items=max_items)

        guard = _anchor_guard(root, _guard_from_state(state))
        guard["language_profile"] = _detect_language_profile(root)
        if verification_incoming:
            status = _verification_status(verification_incoming)
            guard["builder_verify_used"] = True
            guard["last_verify_success"] = status
            guard["last_verify_at"] = _now_iso()
            guard["writes_since_budget"] = 0
            guard["writes_since_verify"] = 0
            guard["verify_required"] = False
            if status is True:
                guard["receipt_required"] = True
                guard["repair_patches_remaining"] = None
                guard["failure_plan_required"] = False
            elif status is False:
                guard["receipt_required"] = False
                guard["repair_patches_remaining"] = 2
                guard["failure_plan_required"] = True
        state["guard"] = guard

        state["project_path"] = str(root)
        state["updated_at"] = _now_iso()
        try:
            _write_json(path, state)
        except Exception as exc:
            return _json({
                "success": False,
                "project_path": str(root),
                "state_path": str(path),
                "summary": f"Failed to write builder resume state: {exc}",
                "state": state,
            })

    summary_action = {
        "read": "Read",
        "update": "Updated",
        "replace": "Replaced",
    }[action]
    next_required: List[str] = []
    guard = _anchor_guard(root, _guard_from_state(state))
    if action in {"update", "replace"} and "verification" in args:
        next_required.extend([
            "If the recorded verification passed, do not write or patch more files in this turn.",
            "Call builder_budget with after_verify=true.",
            "Then call builder_receipt and record deferred layers instead of expanding the build.",
            "If the recorded verification failed, patch at most two concrete failures before rerunning builder_verify.",
        ])
    elif guard.get("receipt_required"):
        next_required.extend([
            "A passing verification is already recorded for this stage.",
            "Call builder_budget with after_verify=true, then builder_receipt before adding more scope.",
        ])
    elif guard.get("verify_required"):
        next_required.extend([
            "The current stage has reached its write budget.",
            "Call builder_budget, then builder_verify before writing more files.",
        ])
    return _json({
        "success": True,
        "project_path": str(root),
        "state_path": str(path),
        "state_exists": existed or action in {"update", "replace"},
        "summary": f"{summary_action} builder resume state.",
        "state": state,
        "next_required": next_required,
    })


# ---------------------------------------------------------------------------
# builder_receipt
# ---------------------------------------------------------------------------

def builder_receipt(args: Dict[str, Any], **_: Any) -> str:
    project_path = args.get("project_path", "")
    max_files = _safe_int(args.get("max_files", 80), default=80, min_value=20, max_value=300)
    verification_results = _listify(args.get("verification_results"))

    root = Path(project_path).resolve()
    if not root.is_dir():
        return _json({
            "success": False,
            "project_path": project_path,
            "summary": "Project path does not exist or is not a directory.",
            "receipt": {},
        })

    project_map = _build_project_map(root, max_files=700)
    state_path = _state_path(root)
    state = _load_state(root)
    state_exists = state_path.exists()
    git = _git_status(root, max_lines=max_files)

    touched = list(state.get("files_touched", []))[:max_files]
    if not touched and git.get("changed_files"):
        touched = [line[3:] if len(line) > 3 else line for line in git.get("changed_files", [])][:max_files]

    verification = list(state.get("verification", []))
    for item in verification_results:
        verification.append(item if isinstance(item, dict) else {"note": _clip(item, 2000)})

    warnings: List[str] = []
    blocking_warnings: List[str] = []
    guard = _anchor_guard(root, _guard_from_state(state))
    latest_verification = next((item for item in reversed(verification) if isinstance(item, dict)), None)
    latest_verification_status = _verification_status([latest_verification]) if latest_verification else None
    zero_test_records = [
        item for item in verification
        if isinstance(item, dict) and item.get("zero_tests_detected")
    ]

    if not state_exists:
        warnings.append("No .hermes-builder/state.json exists; call builder_resume during long builds.")
    if not verification:
        blocking_warnings.append("No verification records supplied or saved; run builder_verify before final handoff.")
    elif guard.get("last_verify_success") is not True:
        if guard.get("last_verify_success") is False:
            blocking_warnings.append("Last recorded verification failed; run builder_failure_plan, patch one cause, and rerun builder_verify before final handoff.")
        else:
            blocking_warnings.append("No passing builder_verify checkpoint is recorded for this stage.")
    elif latest_verification_status is False:
        blocking_warnings.append("Latest verification record is not passing; rerun builder_verify after a focused repair before final handoff.")
    if zero_test_records:
        warnings.append("At least one verification record reported zero executed tests; a completed stage needs focused tests that actually run.")
        if latest_verification and latest_verification.get("zero_tests_detected"):
            blocking_warnings.append("Latest verification reported zero executed tests; add a focused test and rerun builder_verify.")
    if guard.get("last_verify_success") is True and not guard.get("last_budget_after_verify"):
        warnings.append("Passing verification is recorded, but builder_budget(after_verify=true) has not been recorded before receipt.")
    if project_map["scripts"] and not any(name in project_map["scripts"] for name in ("test", "build", "lint", "typecheck", "check")):
        warnings.append("package.json exists but has no common verification scripts.")
    if project_map.get("rust") and not project_map.get("rust", {}).get("test_files") and not project_map.get("rust", {}).get("has_inline_tests"):
        warnings.append("Rust project has no sampled tests; cargo test may only prove compilation.")
    if project_map.get("go") and not project_map.get("go", {}).get("test_files"):
        warnings.append("Go module has no sampled *_test.go files; go test may only prove compilation.")
    ready_to_report = not blocking_warnings and not warnings

    receipt = {
        "project": project_map["name"],
        "project_path": str(root),
        "package_manager": project_map["package_manager"],
        "frameworks": project_map["frameworks"],
        "node": project_map.get("node", {}),
        "swift": project_map.get("swift", {}),
        "python": project_map.get("python", {}),
        "rust": project_map.get("rust", {}),
        "go": project_map.get("go", {}),
        "objective": state.get("objective", ""),
        "status": state.get("status", ""),
        "current_phase": state.get("current_phase", ""),
        "completed": state.get("completed", [])[:max_files],
        "next_steps": state.get("next_steps", [])[:max_files],
        "decisions": state.get("decisions", [])[:max_files],
        "files_touched": touched,
        "verification": verification[:max_files],
        "available_scripts": project_map["scripts"],
        "git": git,
        "warnings": blocking_warnings + warnings,
        "blocking_warnings": blocking_warnings,
    }

    state_recorded = False
    state_warning = ""
    try:
        guard = _anchor_guard(root, _guard_from_state(state))
        if ready_to_report:
            guard["receipt_required"] = False
            guard["verify_required"] = False
            guard["writes_since_budget"] = 0
            guard["failure_plan_required"] = False
            guard["last_receipt_blocked_reason"] = ""
        else:
            guard["last_receipt_blocked_reason"] = "; ".join(blocking_warnings or warnings)[:1000]
        guard["last_receipt_at"] = _now_iso()
        guard["language_profile"] = _detect_language_profile(root)
        state["guard"] = guard
        _save_state(root, state)
        state_recorded = True
    except Exception as exc:
        state_warning = f"Could not update receipt state: {exc}"

    return _json({
        "success": True,
        "project_path": str(root),
        "state_path": str(state_path),
        "ready_to_report": ready_to_report,
        "blocking_warnings": blocking_warnings,
        "summary": (
            f"Receipt for {project_map['name']}: "
            f"{len(receipt['files_touched'])} file(s), "
            f"{len(verification)} verification record(s), "
            f"{len(blocking_warnings) + len(warnings)} warning(s)."
        ),
        "receipt": receipt,
        "state_recorded": state_recorded,
        "state_warning": state_warning,
    })
