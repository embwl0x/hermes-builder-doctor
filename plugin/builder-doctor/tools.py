"""builder-doctor plugin tool implementations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
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

OBJECTIVE_STOPWORDS = {
    "about",
    "above",
    "after",
    "again",
    "available",
    "before",
    "bounded",
    "build",
    "builder",
    "call",
    "check",
    "code",
    "command",
    "complete",
    "configured",
    "create",
    "current",
    "dependency",
    "directory",
    "external",
    "file",
    "files",
    "first",
    "focus",
    "focused",
    "foundation",
    "full",
    "handoff",
    "include",
    "includes",
    "integration",
    "kernel",
    "larger",
    "library",
    "local",
    "macos",
    "main",
    "model",
    "module",
    "must",
    "naturally",
    "objective",
    "package",
    "path",
    "phase",
    "project",
    "prompt",
    "record",
    "repair",
    "root",
    "run",
    "script",
    "source",
    "stage",
    "staged",
    "structure",
    "swift",
    "swiftpm",
    "target",
    "test",
    "tests",
    "tool",
    "tools",
    "use",
    "using",
    "verified",
    "verify",
    "workflow",
    "write",
    "with",
    "without",
    "xctest",
}


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


def _identifier_text(text: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return text.replace("_", " ").replace("-", " ")


def _objective_contract_text(text: str) -> str:
    """Keep the part of an objective that describes this stage, not deferrals."""
    text = str(text or "")
    pieces = re.split(r"\b(?:defer|deferred|future|later)\b", text, maxsplit=1, flags=re.IGNORECASE)
    return pieces[0]


def _canonical_objective_term(token: str) -> str:
    token = token.lower().strip()
    if token.endswith("ies") and len(token) > 5:
        return token[:-3] + "y"
    if token.endswith("sses"):
        return token[:-2]
    if token.endswith("s") and len(token) > 4 and not token.endswith(("ss", "us")):
        return token[:-1]
    return token


def _objective_term_variants(term: str) -> set[str]:
    variants = {term}
    if term.endswith("ies") and len(term) > 5:
        variants.add(term[:-3] + "y")
    if term.endswith("s") and len(term) > 4 and not term.endswith(("ss", "us")):
        variants.add(term[:-1])
    if term.endswith("ical") and len(term) > 6:
        variants.add(term[:-2])
    if term.endswith("tion") and len(term) > 7:
        variants.add(term[:-3])
    return {value for value in variants if len(value) >= 4}


def _extract_objective_terms(text: str, max_terms: int = 40) -> List[str]:
    """Extract concrete scope anchors from a user objective.

    These are intentionally simple lexical anchors. They help local models avoid
    declaring a tiny verified slice complete when the objective named several
    core domain concepts.
    """
    contract = _identifier_text(_objective_contract_text(text))
    raw_terms = re.findall(r"[a-zA-Z][a-zA-Z0-9]{3,}", contract.lower())
    terms: List[str] = []
    seen: set[str] = set()
    for raw in raw_terms:
        term = _canonical_objective_term(raw)
        if term in OBJECTIVE_STOPWORDS or term.isdigit():
            continue
        if term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= max_terms:
            break
    return terms


def _project_scope_corpus(root: Path, max_files: int = 160, max_chars: int = 240000) -> str:
    parts: List[str] = []
    total = 0
    include_exts = SOURCE_EXTS | {".toml", ".yaml", ".yml", ".md"}
    for path in _walk_project_files(root, max_files=max_files):
        if path.suffix not in include_exts:
            continue
        rel = _rel(path, root)
        text = _read_text(path) or ""
        chunk = _identifier_text(f"{rel}\n{text[:12000]}").lower()
        parts.append(chunk)
        total += len(chunk)
        if total >= max_chars:
            break
    return "\n".join(parts)


def _scope_contract_status(root: Path, state: Dict[str, Any], project_map: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    objective = str(state.get("objective") or "")
    terms = _extract_objective_terms(objective)
    if len(terms) < 6:
        return {
            "required": False,
            "ready": True,
            "reason": "No concrete saved objective with enough scope anchors.",
            "objective_terms": terms,
            "matched_terms": [],
            "missing_terms": [],
            "min_matches": 0,
        }

    corpus = _project_scope_corpus(root)
    matched: List[str] = []
    missing: List[str] = []
    for term in terms:
        variants = _objective_term_variants(term)
        if any(re.search(rf"\b{re.escape(variant)}\b", corpus) for variant in variants):
            matched.append(term)
        else:
            missing.append(term)

    min_matches = min(8, max(4, int(round(len(terms) * 0.35))))
    ready = len(matched) >= min_matches
    return {
        "required": True,
        "ready": ready,
        "reason": (
            f"Matched {len(matched)}/{len(terms)} objective anchor(s); "
            f"minimum for this staged receipt is {min_matches}."
        ),
        "objective_terms": terms,
        "matched_terms": matched[:40],
        "missing_terms": missing[:40],
        "min_matches": min_matches,
    }


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


def _swift_target_path_overrides(package_text: str, target_kind: str) -> Dict[str, str]:
    """Best-effort map of SwiftPM target names to explicit path overrides."""
    pattern = rf"\.{re.escape(target_kind)}\s*\(\s*name:\s*\"([^\"]+)\"(?P<body>.*?)(?=\n\s*\.(?:target|executableTarget|testTarget)\s*\(|\n\s*\]\s*\)|\Z)"
    paths: Dict[str, str] = {}
    for match in re.finditer(pattern, package_text, re.DOTALL):
        name = match.group(1)
        body = match.group("body") or ""
        path_match = re.search(r"\bpath:\s*\"([^\"]+)\"", body)
        if path_match:
            paths[name] = path_match.group(1)
    return paths


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
    target_paths = {
        "regular": _swift_target_path_overrides(package_text, "target"),
        "executable": _swift_target_path_overrides(package_text, "executableTarget"),
        "test": _swift_target_path_overrides(package_text, "testTarget"),
    }

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
        "target_paths": target_paths,
        "missing_test_target_dirs": [
            target for target in test_targets
            if target not in test_dirs and target not in target_paths.get("test", {})
        ][:80],
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


def _output_text(value: Any) -> str:
    """Normalize subprocess output, including bytes returned on timeouts."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _descendant_pids(root_pid: int) -> List[int]:
    """Snapshot descendants, including children that created a new process group."""
    try:
        snapshot = subprocess.run(
            ["ps", "-axo", "pid=,ppid="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return []

    children: Dict[int, List[int]] = {}
    for line in snapshot.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            pid, ppid = (int(field) for field in fields)
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)

    descendants: List[int] = []
    pending = list(children.get(root_pid, []))
    while pending:
        pid = pending.pop()
        descendants.append(pid)
        pending.extend(children.get(pid, []))
    return descendants


def _signal_pids(pids: List[int], sig: signal.Signals) -> None:
    for pid in reversed(pids):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def _stop_process_tree(proc: subprocess.Popen[str]) -> tuple[str, str]:
    """Stop a verifier and all descendants, even if a child detached its group."""
    descendants = _descendant_pids(proc.pid)
    _signal_pids(descendants, signal.SIGTERM)
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    try:
        stdout, stderr = proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        descendants.extend(_descendant_pids(proc.pid))
        _signal_pids(sorted(set(descendants)), signal.SIGKILL)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = proc.communicate()

    return _output_text(stdout), _output_text(stderr)


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
        "last_receipt_ready": False,
        "language_profile": "unknown",
        "objective_required": False,
        "test_phase_required": False,
        "last_missing_tests_reason": "",
        "scope_phase_required": False,
        "last_scope_contract_reason": "",
        "acceptance_required": False,
        "acceptance_ready": True,
        "last_acceptance_at": "",
        "last_acceptance_reason": "",
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
        "acceptance_contract": {"criteria": []},
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


def _missing_required_tests(root: Path, project_map: Optional[Dict[str, Any]] = None) -> List[str]:
    """Return reasons this project still needs real tests before receipt.

    This intentionally separates "a compile/check command passed" from
    "the staged build is ready to hand off". Local models tend to verify a
    compile/vet slice too early, then get trapped by receipt_required before
    writing tests.
    """
    project_map = project_map or _build_project_map(root, max_files=700)
    profile = _detect_language_profile(root)
    reasons: List[str] = []

    if profile == "go":
        go_info = project_map.get("go", {}) or {}
        if go_info.get("go_file_count", 0) > 0 and not go_info.get("test_files"):
            reasons.append("Go source exists but no sampled *_test.go file exists; add focused Go tests before receipt.")
    elif profile == "swift":
        swift_info = project_map.get("swift", {}) or {}
        targets = swift_info.get("targets", {}) if isinstance(swift_info, dict) else {}
        test_targets = targets.get("test", []) or []
        swift_test_files = [
            path for path in root.glob("Tests/**/*.swift") if path.is_file()
        ]
        if test_targets and not swift_info.get("test_files"):
            reasons.append("SwiftPM declares a test target but no sampled XCTest files exist under Tests/<Target>.")
        elif swift_info.get("swift_file_count", 0) > 0 and not swift_info.get("test_files"):
            reasons.append("Swift source exists but no sampled XCTest files exist; add Tests/<Target>Tests coverage before receipt.")
        elif swift_test_files:
            test_source = "\n".join(
                path.read_text(encoding="utf-8", errors="ignore")
                for path in swift_test_files
            )
            declaration_count = (
                len(re.findall(r"(?m)^\s*@Test\b", test_source))
                + len(re.findall(r"\bfunc\s+test[A-Za-z0-9_]*\s*\(", test_source))
            )
            placeholder_assertion = bool(
                re.search(r"\bXCTAssertTrue\s*\(\s*true\s*\)", test_source)
                or re.search(r"#expect\s*\(\s*true\s*\)", test_source)
            )
            placeholder_name = bool(re.search(r"(?i)\bplaceholder\b", test_source))
            if declaration_count <= 1 and (placeholder_assertion or placeholder_name):
                reasons.append(
                    "Swift tests contain only a trivial placeholder; add focused behavioral assertions before receipt."
                )
    elif profile == "python":
        python_info = project_map.get("python", {}) or {}
        if python_info.get("python_file_count", 0) > 1 and not python_info.get("test_files"):
            reasons.append("Python source exists but no sampled tests exist; add unittest/pytest coverage before receipt.")
    elif profile == "rust":
        rust_info = project_map.get("rust", {}) or {}
        if rust_info.get("rust_file_count", 0) > 0 and not rust_info.get("test_files") and not rust_info.get("has_inline_tests"):
            reasons.append("Rust source exists but no sampled #[test] or tests/ files exist; add focused Rust tests before receipt.")
    elif profile == "node":
        node_info = project_map.get("node", {}) or {}
        if node_info.get("source_file_count", 0) > 0 and not node_info.get("test_files"):
            reasons.append("Node/TypeScript source exists but no sampled test files exist; add a focused discovered test before receipt.")

    return reasons


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


def _write_call_limit(root: Path) -> int:
    """Return a coherent per-checkpoint edit batch for the detected language.

    Swift features frequently require a model, implementation, integration, and
    test file to compile together. A three-call global cap fragmented those
    changes and forced verification of deliberately incomplete states.
    """
    return 6 if _detect_language_profile(root) == "swift" else 3


def _language_stage_policy(root: Path) -> Dict[str, Any]:
    profile = _detect_language_profile(root)
    policies: Dict[str, Dict[str, Any]] = {
        "node": {
            "preset": "node-esm-kernel",
            "first_slice": [
                "package.json with type=module and a bounded test script",
                "one implementation module",
                "one node:test file",
            ],
            "verify": "npm test or the detected package-manager test script through builder_verify",
            "forbidden": ["npm install/add/ci", "pnpm add/install", "yarn add/install", "bun add/install", "node_modules"],
            "repair": "Patch one JS/TS diagnostic or failing assertion; do not change package manager or install packages.",
        },
        "python": {
            "preset": "python-stdlib-kernel",
            "first_slice": [
                "pyproject.toml metadata",
                "one importable module/package",
                "one tests/test_*.py unittest file",
            ],
            "verify": "python3 -m unittest discover -s tests unless pytest is explicitly declared",
            "forbidden": ["pip install", "python -m pip install", "uv pip", "uv sync", "uv run for stdlib kernels", ".venv"],
            "repair": "Patch the first traceback/import/test failure; do not create an environment to fix import layout.",
        },
        "go": {
            "preset": "go-package-kernel",
            "first_slice": ["go.mod", "one package implementation file", "one *_test.go file"],
            "verify": "go test ./... through builder_verify",
            "forbidden": ["go get", "go install", "go run", "go mod tidy/download/vendor during the first slice"],
            "repair": "Keep one package name per directory; fix package setup before behavior changes.",
        },
        "swift": {
            "preset": "swiftpm-library-kernel",
            "first_slice": [
                "Package.swift with one library target and one XCTest target",
                "one Sources/<Target>/<Target>.swift implementation file",
                "one Tests/<Target>Tests/<Target>Tests.swift XCTest file",
            ],
            "verify": "swift build and swift test through builder_verify",
            "forbidden": ["extra targets before first passing swift test", "weakening XCTest assertions", "swift run as verification"],
            "repair": "Patch the first compiler/XCTest diagnostic only, then rerun swift test.",
        },
        "rust": {
            "preset": "rust-lib-kernel",
            "first_slice": ["Cargo.toml", "src/lib.rs", "inline #[cfg(test)] tests"],
            "verify": "cargo test through builder_verify",
            "forbidden": ["cargo add/install/update/run", "targeted test as final proof", "parallel replacement modules"],
            "repair": "Patch one compiler diagnostic or failing test; full cargo test is required before receipt.",
        },
    }
    policy = dict(policies.get(profile, {
        "preset": "generic-staged-kernel",
        "first_slice": ["manifest/config", "one implementation file", "one focused test file"],
        "verify": "smallest bounded test/build command through builder_verify",
        "forbidden": ["dependency installs", "dev servers", "watchers", "broad scaffolds before first verification"],
        "repair": "Patch one concrete diagnostic, then rerun builder_verify.",
    }))
    policy["language_profile"] = profile
    policy["budget_defaults"] = _language_budget_defaults(root)
    return policy


def _environment_artifact_dirs(root: Path) -> List[str]:
    names = {
        ".venv",
        "__pypackages__",
        ".tox",
        ".nox",
        "node_modules",
        ".pnpm-store",
        ".yarn",
        ".pytest_cache",
    }
    found: List[str] = []
    if not root.exists():
        return found
    for path in root.iterdir():
        if path.is_dir() and path.name in names:
            found.append(path.name)
    return sorted(found)


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


def _terminal_command_path_candidates(
    command: Any, base: Optional[Path] = None
) -> List[str]:
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
    current_dir = base or _expand_tool_path(os.getenv("TERMINAL_CWD") or os.getcwd())
    try:
        for part in re.split(r"[;&|]+", command):
            tokens = shlex.split(part)
            if not tokens:
                continue

            while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
                tokens.pop(0)
            if tokens and Path(tokens[0]).name in {"builtin", "command"}:
                tokens.pop(0)
            if not tokens:
                continue

            executable_token = tokens[0]
            executable = Path(executable_token).name
            if executable == "cd":
                operands = [token for token in tokens[1:] if token != "--"]
                destination = next(
                    (token for token in operands if not token.startswith("-")), None
                )
                if destination:
                    expanded = _expand_tool_path(destination, base=current_dir)
                    if expanded:
                        candidates.append(str(expanded))
                        current_dir = expanded
                continue

            if "/" in executable_token or executable_token.startswith("~"):
                expanded = _expand_tool_path(executable_token, base=current_dir)
                system_roots = (
                    Path("/bin"),
                    Path("/sbin"),
                    Path("/usr"),
                    Path("/opt/homebrew"),
                    Path("/Applications/Xcode.app/Contents/Developer"),
                    Path("/Library/Developer/CommandLineTools"),
                )
                if expanded and not any(
                    _is_within_root(expanded, root) for root in system_roots
                ):
                    candidates.append(str(expanded))

            if executable not in {"rm", "cp", "mv", "touch"}:
                continue
            for token in tokens[1:]:
                if token.startswith("-"):
                    continue
                expanded = _expand_tool_path(token, base=current_dir)
                candidates.append(str(expanded) if expanded else token)
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
        for rel in _terminal_command_path_candidates(args.get("command"), base=base):
            expanded = _expand_tool_path(rel, base=base)
            if expanded:
                candidates.append(expanded)
        if not workdir:
            cwd = _expand_tool_path(os.getenv("TERMINAL_CWD") or os.getcwd())
            if cwd:
                candidates.append(cwd)

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
        for rel in _terminal_command_path_candidates(args.get("command"), base=base):
            expanded = _expand_tool_path(rel, base=base)
            if expanded:
                candidates.append(expanded)

    return candidates


def _is_raw_verifier_command(command: str) -> bool:
    patterns = [
        r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|build|lint|typecheck|check)\b",
        r"\b(?:vitest|jest|node\s+--test)\b",
        r"\bswift\s+(?:build|test)\b",
        r"\bcargo\s+(?:test|check|clippy|build)\b",
        r"\bgo\s+(?:test|build|vet)\b",
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
        r"(?:^|[;&|]\s*)go\s+mod\s+(?:tidy|download|vendor)\b",
        r"(?:^|[;&|]\s*)(?:rm|cp|mv|touch)\b",
    ]
    return any(re.search(pattern, command) for pattern in patterns)


def _is_copy_only_terminal_command(command: str) -> bool:
    try:
        parts = [part.strip() for part in re.split(r"&&|;", command) if part.strip()]
        if not parts or Path(shlex.split(parts[0])[0]).name != "cp":
            return False
        return all(Path(shlex.split(part)[0]).name == "echo" for part in parts[1:])
    except (IndexError, ValueError):
        return False


def _is_verified_artifact_export(command: str, root: Path, guard: Dict[str, Any]) -> bool:
    """Allow a verified build artifact to be copied into a macOS app install dir."""
    if guard.get("last_verify_success") is not True:
        return False
    try:
        parts = [part.strip() for part in re.split(r"&&|;", command) if part.strip()]
        if not parts:
            return False
        tokens = shlex.split(parts[0])
        if not tokens or Path(tokens[0]).name != "cp":
            return False
        positional = [token for token in tokens[1:] if not token.startswith("-")]
        if len(positional) != 2:
            return False
        source = _expand_tool_path(positional[0], base=root)
        destination = _expand_tool_path(positional[1], base=root)
        if source is None or destination is None or not source.exists():
            return False
        if not _is_within_root(source, root) or _is_within_root(destination, root):
            return False
        relative = source.relative_to(root.resolve())
        if not relative.parts or relative.parts[0] not in {".build", "build", "dist", "out"}:
            return False
        install_roots = [Path("/Applications"), Path.home() / "Applications"]
        if not any(_is_within_root(destination, install_root) for install_root in install_roots):
            return False
        return all(Path(shlex.split(part)[0]).name == "echo" for part in parts[1:])
    except (ValueError, OSError):
        return False


def _is_dependency_or_env_mutation_command(command: str) -> bool:
    patterns = [
        r"\b(?:npm|pnpm|yarn|bun)\s+(?:install|add|i|ci)\b",
        r"\b(?:npm|pnpm|yarn|bun)\s+dlx\b",
        r"\buv\s+(?:add|remove|sync|lock|pip|venv)\b",
        r"\bpython(?:3)?\s+-m\s+(?:pip\s+install|venv)\b",
        r"\bpip(?:3)?\s+install\b",
        r"\bvirtualenv\b",
        r"\bpoetry\s+(?:add|install|update|lock)\b",
        r"\bpipenv\s+install\b",
        r"\bcargo\s+(?:add|install|update|run)\b",
        r"\bgo\s+(?:get|install|run)\b",
        r"\bgo\s+mod\s+(?:download|tidy|vendor)\b",
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
    if tool_name == "terminal":
        command = str(args.get("command", "")) if isinstance(args, dict) else ""
        if _is_raw_verifier_command(command):
            return {
                "action": "block",
                "message": (
                    "Builder Doctor blocked this raw terminal verifier. Use builder_verify with the "
                    "project_path and bounded command instead, then call builder_resume, "
                    "builder_budget(after_verify=true), and builder_receipt on success."
                ),
            }
        if _is_terminal_file_mutation_command(command) and not _is_copy_only_terminal_command(command):
            return {
                "action": "block",
                "message": (
                    "Builder Doctor blocked this terminal file mutation. Use write_file or patch for "
                    "source/test/config edits, and avoid dependency/install/tidy mutations inside "
                    "the verifier loop."
                ),
            }
    root = _root_from_tool_args(tool_name, args)
    if root is None:
        return None
    state = _load_state(root)
    guard = _guard_from_state(state)
    guard = _anchor_guard(root, guard)
    state["guard"] = guard

    anchored_root = _expand_tool_path(guard.get("root_anchor")) or root
    command = str(args.get("command", "")) if tool_name == "terminal" and isinstance(args, dict) else ""
    verified_artifact_export = _is_verified_artifact_export(command, anchored_root, guard)
    for candidate in _boundary_candidate_paths(tool_name, args, anchored_root):
        if not _is_within_root(candidate, anchored_root) and not verified_artifact_export:
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
            if verified_artifact_export:
                return None
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
        if _is_dependency_or_env_mutation_command(command):
            return _write_guard_block(
                root,
                state,
                guard,
                "dependency-env-mutation-blocked",
                (
                    "Builder Doctor blocked this dependency/environment mutation inside the mapped project. "
                    "Use the language preset from builder_map/builder_budget: keep the first slice small, "
                    "avoid local env/install artifacts, and verify with builder_verify."
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
                f"Call builder_failure_plan now with only "
                f"{{\"project_path\": \"{root}\"}}; it automatically loads the latest failed "
                "verifier output. Do not call another tool first. Then patch only the first "
                "diagnostic it identifies."
            ),
        )

    if tool_name in {"write_file", "patch"} and not str(state.get("objective") or "").strip():
        guard["objective_required"] = True
        return _write_guard_block(
            root,
            state,
            guard,
            "objective-required",
            (
                "Builder Doctor blocked this source edit because the project has no saved objective. "
                "Call builder_resume with action=update and objective set to the concrete user request, "
                "then continue the staged build."
            ),
        )

    if guard.get("receipt_required") and (
        guard.get("test_phase_required") or guard.get("scope_phase_required")
    ):
        guard["receipt_required"] = False
        guard["verify_required"] = False
        state["guard"] = guard
        try:
            _save_state(root, state)
        except Exception:
            pass

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

    write_call_limit = _write_call_limit(root)
    if guard.get("verify_required") or _safe_int(guard.get("writes_since_budget", 0), 0, min_value=0) >= write_call_limit:
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
    guard["last_receipt_ready"] = False
    guard["language_profile"] = _detect_language_profile(root)

    if guard.get("last_verify_success") is False and isinstance(repair_remaining, int):
        repair_remaining = max(0, repair_remaining - 1)
        guard["repair_patches_remaining"] = repair_remaining
        if repair_remaining <= 0:
            guard["verify_required"] = True
    elif guard["writes_since_budget"] >= write_call_limit:
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
        r"\buv\s+(?:add|remove|sync|lock|pip|venv)\b",
        r"\bpython(?:3)?\s+-m\s+pip\s+install\b",
        r"\bpython(?:3)?\s+-m\s+venv\b",
        r"\bpip(?:3)?\s+install\b",
        r"\bvirtualenv\b",
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
            or (root / "pytest.ini").exists()
        )
        uses_uv = (root / "uv.lock").exists()
        if has_pytest:
            return ["uv run pytest"] if uses_uv else ["python3 -m pytest"]
        if python_info.get("test_files"):
            tests_dir = root / "tests"
            return ["python3 -m unittest discover -s tests"] if tests_dir.is_dir() else ["python3 -m unittest discover"]
        return ["python3 -m compileall -q ."]
    for candidate in ("test", "build", "lint", "typecheck", "check"):
        if candidate in scripts:
            return [_node_script_command(root, candidate)]
    return []


def _cargo_test_args(command: str) -> Optional[List[str]]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for index in range(len(tokens) - 1):
        if tokens[index] == "cargo" and tokens[index + 1] == "test":
            return tokens[index + 2:]
    return None


def _is_full_cargo_test_command(command: str) -> bool:
    args = _cargo_test_args(command)
    if args is None:
        return False

    target_selectors = {
        "--bench",
        "--bin",
        "--example",
        "--lib",
        "--package",
        "--test",
        "-p",
    }
    value_flags = {
        "--color",
        "--config",
        "--features",
        "--jobs",
        "--manifest-path",
        "--message-format",
        "--profile",
        "--target",
        "--target-dir",
        "-j",
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            return True
        if arg in target_selectors or any(arg.startswith(f"{selector}=") for selector in target_selectors):
            return False
        if arg in value_flags:
            index += 2
            continue
        if any(arg.startswith(f"{flag}=") for flag in value_flags):
            index += 1
            continue
        if arg.startswith("-"):
            index += 1
            continue
        return False
    return True


def _ensure_required_verify_commands(root: Path, commands: List[str]) -> List[str]:
    profile = _detect_language_profile(root)
    normalized = list(commands)

    if profile == "swift":
        has_swift_compile_check = any(
            re.search(r"\bswift\s+build\b", command)
            for command in normalized
        )
        has_swift_test = any(
            re.search(r"\bswift\s+test\b", command)
            for command in normalized
        )
        if has_swift_compile_check and not has_swift_test:
            normalized.append("swift test")

    if profile == "go":
        has_go_compile_check = any(
            re.search(r"\bgo\s+(?:build|vet)\b", command)
            for command in normalized
        )
        has_go_test = any(
            re.search(r"\bgo\s+test\b", command)
            for command in normalized
        )
        if has_go_compile_check and not has_go_test:
            normalized.append("go test ./...")

    if profile == "rust":
        has_cargo_compile_check = any(
            re.search(r"\bcargo\s+(?:check|build|clippy)\b", command)
            for command in normalized
        )
        has_cargo_test = any(_cargo_test_args(command) is not None for command in normalized)
        has_full_cargo_test = any(_is_full_cargo_test_command(command) for command in normalized)
        if (has_cargo_compile_check or has_cargo_test) and not has_full_cargo_test:
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
        stripped = line.strip()
        m = re.search(r"target '([^']+)' has overlapping sources:\s*(.+)$", stripped)
        if not m:
            continue
        sources = [part.strip() for part in m.group(2).split(",") if part.strip()]
        key = f"swiftpm-overlap|{m.group(1)}|{'|'.join(sources)}"
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append({
            "kind": "swiftpm-overlapping-sources",
            "file": "Package.swift",
            "target": m.group(1),
            "sources": sources[:12],
            "message": "SwiftPM target has overlapping sources, commonly because a declared test target has no Tests/<Target> directory or target paths do not match conventional layout.",
        })
        if len(diagnostics) >= 16:
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
    if any(item.get("kind") == "swiftpm-overlapping-sources" for item in diagnostics):
        suggested_next.append("Run builder_doctor focus=swift; fix target layout first, especially missing Tests/<TestTarget>/ directories, before editing Swift behavior.")
        suggested_next.append("Do not keep adding path/exclude guesses; align Package.swift target names with Sources/<Target> and Tests/<TestTarget>.")
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
    elif kind in {"swiftpm-overlapping-sources"}:
        recipe["mode"] = "swiftpm-target-layout-repair"
        recipe["patch_policy"] = "Fix SwiftPM target layout only; do not change simulation/app behavior during this repair."
        recipe["steps"] = [
            "Run builder_doctor with focus=swift or inspect its Swift findings.",
            "Ensure each declared library/executable target maps to exactly one Sources/<Target>/ directory or one explicit path.",
            "Ensure every .testTarget has a real Tests/<TestTarget>/ directory with at least one XCTest file, or a valid test-target-specific path under Tests.",
            "Remove stale source directories that no target should own instead of adding broad exclude guesses.",
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
    if not failure["command"] and not failure["output_tail"]:
        try:
            saved_state = _load_state(root)
            saved_guard = _guard_from_state(saved_state)
            saved_failure = saved_guard.get("last_failure")
            if isinstance(saved_failure, dict):
                failure = {
                    "command": str(saved_failure.get("command") or ""),
                    "output_tail": str(saved_failure.get("output_tail") or ""),
                    "timed_out": bool(saved_failure.get("timed_out")),
                    "zero_tests": bool(saved_failure.get("zero_tests")),
                }
        except Exception:
            pass
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
    language_policy = _language_stage_policy(root)

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
        "policy": language_policy,
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
    env_artifacts = _environment_artifact_dirs(root)

    if env_artifacts and focus in ("all", "python", "node", "javascript", "typescript", "testing", "build", "package"):
        findings.append({
            "severity": "warning",
            "code": "staged-build-env-artifacts",
            "file": ".",
            "message": "Local dependency/environment artifact directories were detected inside the staged build.",
            "evidence": f"directories={env_artifacts}",
            "suggested_fix": "Do not create .venv/node_modules/install artifacts during the first verified slice; remove them from generated test projects and verify with bounded commands.",
        })

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
            if swift_info.get("missing_test_target_dirs"):
                findings.append({
                    "severity": "error",
                    "code": "swiftpm-test-target-dir-missing",
                    "file": "Package.swift",
                    "message": "SwiftPM declares test targets whose conventional Tests/<Target> directories are missing.",
                    "evidence": f"missing_test_target_dirs={swift_info.get('missing_test_target_dirs')}, test_target_dirs={swift_info.get('test_target_dirs', [])}",
                    "suggested_fix": "Create Tests/<TestTargetName>/<TestTargetName>.swift with XCTest cases, or give that specific .testTarget a valid path under Tests. Do not try to fix this with exclude entries on source targets.",
                })
            if swift_info.get("targets", {}).get("test") and not swift_info.get("test_files"):
                findings.append({
                    "severity": "error",
                    "code": "swiftpm-test-files-missing",
                    "file": "Tests",
                    "message": "SwiftPM test target exists, but no sampled XCTest files were found.",
                    "evidence": f"test_targets={swift_info.get('targets', {}).get('test', [])}, test_files=[]",
                    "suggested_fix": "Add at least one XCTest file under Tests/<TestTargetName>/ before claiming behavior is verified.",
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
                target_paths = swift_info.get("target_paths", {})
                path_overrides = {}
                if isinstance(target_paths, dict):
                    path_overrides.update(target_paths.get("regular", {}) or {})
                    path_overrides.update(target_paths.get("executable", {}) or {})
                if target not in swift_info.get("source_target_dirs", []) and target not in path_overrides:
                    findings.append({
                        "severity": "warning",
                        "code": "swiftpm-target-dir-missing",
                        "file": "Package.swift",
                        "message": "SwiftPM target name does not match a conventional Sources/<Target> directory.",
                        "evidence": f"target={target}, source_target_dirs={swift_info.get('source_target_dirs', [])}",
                        "suggested_fix": "Create Sources/<Target>/ or add an explicit path: argument for the target.",
                    })
            for target in swift_info.get("targets", {}).get("test", []):
                if target in set(swift_info.get("missing_test_target_dirs", []) or []):
                    continue
                test_paths = (swift_info.get("target_paths", {}) or {}).get("test", {}) if isinstance(swift_info.get("target_paths"), dict) else {}
                if target not in swift_info.get("test_target_dirs", []) and target not in test_paths:
                    findings.append({
                        "severity": "error",
                        "code": "swiftpm-test-target-dir-missing",
                        "file": "Package.swift",
                        "message": "SwiftPM test target name does not match a conventional Tests/<Target> directory.",
                        "evidence": f"target={target}, test_target_dirs={swift_info.get('test_target_dirs', [])}",
                        "suggested_fix": "Create Tests/<Target>/ with an XCTest file, or add an explicit path on that .testTarget only.",
                    })

    # --- 0b) Python/pytest/uv structure risks ---
    if focus in ("all", "python", "pyproject", "testing", "build", "package"):
        if python_info.get("is_python_project"):
            pyproject = _read_toml(root / "pyproject.toml") or {}
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

def _cached_verification_records(
    root: Path,
    state: Dict[str, Any],
    commands: List[str],
) -> Optional[List[Dict[str, Any]]]:
    """Return current passing records when rerunning would prove nothing new."""
    guard = _guard_from_state(state)
    if _safe_int(guard.get("writes_since_verify", 0), 0, min_value=0) != 0:
        return None
    verification = state.get("verification")
    if not isinstance(verification, list) or not verification:
        return None
    contract = state.get("acceptance_contract")
    baseline = 0
    if isinstance(contract, dict):
        try:
            baseline = int(contract.get("verification_baseline", 0) or 0)
        except (TypeError, ValueError):
            baseline = 0
    baseline = max(0, min(len(verification), baseline))
    latest: Dict[str, Dict[str, Any]] = {}
    for item in verification[baseline:]:
        if not isinstance(item, dict):
            continue
        # Only builder_verify may establish reusable proof. Checkpoint notes can
        # contain command/result-shaped dictionaries, but they are not verifier
        # records and must never shadow the latest trusted result.
        if item.get("source") != "builder_verify" or "exit_code" not in item:
            continue
        command = str(item.get("command") or "").strip()
        if command:
            latest[command] = item

    current_evidence = _acceptance_evidence_snapshot(root, state)
    cached: List[Dict[str, Any]] = []
    for command in commands:
        record = latest.get(command)
        if not record:
            return None
        if (
            record.get("source") != "builder_verify"
            or record.get("exit_code") != 0
            or record.get("timed_out")
            or record.get("zero_tests_detected")
        ):
            return None
        recorded_evidence = record.get("acceptance_evidence")
        if current_evidence and recorded_evidence != current_evidence:
            return None
        cached.append({
            "command": command,
            "exit_code": 0,
            "timed_out": False,
            "duration_seconds": 0,
            "output_tail": "Skipped unchanged duplicate; latest builder_verify result is still current.",
            "zero_tests_detected": False,
            "cached": True,
        })
    return cached

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

    state_before_verify = _load_state(root)
    cached_records = _cached_verification_records(root, state_before_verify, commands)
    if cached_records is not None:
        already_complete = bool(_guard_from_state(state_before_verify).get("last_receipt_ready"))
        return _json({
            "success": True,
            "project_path": project_path,
            "commands": cached_records,
            "failures": [],
            "missing_required_tests": [],
            "summary": "Skipped unchanged duplicate verification; the latest passing result is still current.",
            "already_verified": True,
            "already_complete": already_complete,
            "next_required": (
                ["The stage is already receipted. Stop calling builder tools and send the final answer now."]
                if already_complete
                else ["Call builder_budget with after_verify=true, then builder_receipt once."]
            ),
            "state_recorded": False,
            "state_warning": "",
        })

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
            proc = subprocess.Popen(
                cmd,
                shell=True,
                cwd=str(root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                any_failure = True
                stdout, stderr = _stop_process_tree(proc)
                if not stdout:
                    stdout = _output_text(exc.stdout)
                if not stderr:
                    stderr = _output_text(exc.stderr)
                combined = stdout + "\n" + stderr
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
            else:
                exit_code = proc.returncode
                combined = _output_text(stdout) + "\n" + _output_text(stderr)
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

    project_map_for_verify = _build_project_map(root, max_files=700)
    missing_required_tests = [] if any_failure else _missing_required_tests(root, project_map_for_verify)
    summary = f"Ran {len(commands)} command(s); {len(failures)} failure(s)."
    next_required: List[str] = []
    if any_failure:
        next_required.append("Call builder_failure_plan with this failed verifier result before patching.")
        next_required.append("Patch one concrete failure, then rerun builder_verify; do not add new features.")
        next_required.append("Make at most two focused patches before rerunning builder_verify; do not stack broad patch bursts.")
    elif missing_required_tests:
        next_required.extend([
            "This verification passed, but the staged build still has no focused tests for the current kernel.",
            "Open a test/hardening phase now: add one real discovered test file, then rerun builder_verify with the language test command.",
            "Do not call builder_receipt as final handoff until the test command passes with tests discovered.",
        ])
    else:
        next_required.extend([
            "builder_verify recorded this verification in .hermes-builder/state.json.",
            "Call builder_budget with after_verify=true before writing more files.",
            "If this is the intended stage, call builder_receipt now. Do not send the final answer before builder_receipt.",
        ])
    state_recorded = False
    state_warning = ""
    verification_records = [
        {
            "source": "builder_verify",
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
        evidence_snapshot = _acceptance_evidence_snapshot(root, state)
        for record in verification_records:
            record["acceptance_evidence"] = evidence_snapshot
        existing_verification = state.get("verification") or []
        if not isinstance(existing_verification, list):
            existing_verification = []
        # Verification retries are ordered evidence, not set members. Preserve
        # repeated results so the latest outcome for an exact command wins.
        state["verification"] = (existing_verification + verification_records)[-120:]
        guard = _anchor_guard(root, _guard_from_state(state))
        guard["builder_verify_used"] = True
        guard["last_receipt_ready"] = False
        guard["last_verify_success"] = not any_failure
        guard["last_verify_at"] = _now_iso()
        guard["last_verify_commands"] = [str(command) for command in commands]
        guard["writes_since_budget"] = 0
        guard["writes_since_verify"] = 0
        guard["verify_required"] = False
        guard["language_profile"] = _detect_language_profile(root)
        if any_failure:
            guard["receipt_required"] = False
            guard["test_phase_required"] = False
            guard["last_missing_tests_reason"] = ""
            guard["repair_patches_remaining"] = 2
            guard["failure_plan_required"] = True
            first_failure = failures[0] if failures and isinstance(failures[0], dict) else {}
            guard["last_failure"] = {
                "command": str(first_failure.get("command") or ""),
                "output_tail": str(first_failure.get("output_tail") or "")[-8000:],
                "timed_out": bool(first_failure.get("timed_out")),
                "zero_tests": bool(first_failure.get("zero_tests_detected")),
                "recorded_at": _now_iso(),
            }
        elif missing_required_tests:
            guard["receipt_required"] = False
            guard["test_phase_required"] = True
            guard["last_missing_tests_reason"] = "; ".join(missing_required_tests)[:1000]
            guard["repair_patches_remaining"] = None
            guard["failure_plan_required"] = False
            guard.pop("last_failure", None)
        else:
            guard["receipt_required"] = True
            guard["test_phase_required"] = False
            guard["last_missing_tests_reason"] = ""
            guard["repair_patches_remaining"] = None
            guard["failure_plan_required"] = False
            guard.pop("last_failure", None)
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
        "missing_required_tests": missing_required_tests,
        "summary": summary,
        "next_required": next_required,
        "state_recorded": state_recorded,
        "state_warning": state_warning,
    })


def _evidence_fingerprint(root: Path, evidence_path: str) -> Optional[Dict[str, Any]]:
    candidate = (root / evidence_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.exists() or candidate == root or (root / ".hermes-builder") in candidate.parents:
        return None

    stat = candidate.stat()
    if candidate.is_file():
        digest = ""
        if stat.st_size <= 16 * 1024 * 1024:
            hasher = hashlib.sha256()
            with candidate.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
            digest = hasher.hexdigest()
        return {
            "kind": "file",
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": digest,
        }
    if candidate.is_dir():
        entries: List[str] = []
        for path in _walk_project_files(candidate, max_files=500):
            resolved = path.resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            try:
                item_stat = resolved.stat()
            except OSError:
                continue
            entries.append(
                f"{_rel(resolved, candidate)}:{item_stat.st_size}:{item_stat.st_mtime_ns}"
            )
        payload = "\n".join(sorted(entries)).encode("utf-8")
        return {
            "kind": "directory",
            "entries": len(entries),
            "manifest_sha256": hashlib.sha256(payload).hexdigest(),
        }
    return {"kind": "other", "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _acceptance_evidence_snapshot(root: Path, state: Dict[str, Any]) -> Dict[str, Any]:
    contract = state.get("acceptance_contract")
    if not isinstance(contract, dict):
        return {}
    criteria = contract.get("criteria")
    if not isinstance(criteria, list):
        return {}
    snapshot: Dict[str, Any] = {}
    for criterion in criteria:
        if not isinstance(criterion, dict):
            continue
        for evidence_path in _listify(criterion.get("evidence_paths")):
            rel = str(evidence_path).strip()
            if rel and rel not in snapshot:
                snapshot[rel] = _evidence_fingerprint(root, rel)
    return snapshot


def _normalize_acceptance_criteria(
    root: Path,
    incoming: Any,
) -> tuple[List[Dict[str, Any]], List[str]]:
    """Validate and normalize acceptance criteria before they reach project state."""
    if isinstance(incoming, dict):
        incoming = incoming.get("criteria")
    if not isinstance(incoming, list):
        return [], ["'criteria' must be a list."]
    if not incoming:
        return [], ["At least one criterion is required; use action=clear to remove a contract."]

    normalized: List[Dict[str, Any]] = []
    errors: List[str] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(incoming):
        label = f"criterion {index + 1}"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object.")
            continue

        criterion_id = str(item.get("id") or "").strip()
        description = str(item.get("description") or "").strip()
        evidence_paths = [
            str(path).strip()
            for path in _listify(item.get("evidence_paths"))
            if str(path).strip()
        ]
        verification_commands = [
            str(command).strip()
            for command in _listify(item.get("verification_commands"))
            if str(command).strip()
        ]

        if not criterion_id:
            errors.append(f"{label} needs a non-empty id.")
        elif not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", criterion_id):
            errors.append(
                f"{label} id '{criterion_id}' may contain only letters, numbers, dot, dash, and underscore."
            )
        elif criterion_id in seen_ids:
            errors.append(f"Duplicate criterion id '{criterion_id}'.")
        else:
            seen_ids.add(criterion_id)
        if not description:
            errors.append(f"{label} needs a non-empty description.")
        if not evidence_paths:
            errors.append(f"{label} needs at least one evidence_path.")
        if not verification_commands:
            errors.append(f"{label} needs at least one verification_command.")

        safe_paths: List[str] = []
        for evidence_path in evidence_paths:
            candidate = Path(evidence_path)
            resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
            try:
                relative = resolved.relative_to(root)
            except ValueError:
                errors.append(f"{label} evidence path escapes the project root: {evidence_path}")
                continue
            if not relative.parts or relative.parts[0] == ".hermes-builder":
                errors.append(
                    f"{label} evidence must be a project artifact outside .hermes-builder: {evidence_path}"
                )
                continue
            normalized_path = str(relative)
            if normalized_path not in safe_paths:
                safe_paths.append(normalized_path)

        normalized.append({
            "id": criterion_id,
            "description": description,
            "evidence_paths": safe_paths,
            "verification_commands": list(dict.fromkeys(verification_commands)),
        })

    return normalized, errors


def builder_acceptance(args: Dict[str, Any], **_: Any) -> str:
    project_path = args.get("project_path", "")
    action = str(args.get("action", "read")).strip().lower() or "read"

    root = Path(project_path).resolve()
    if not root.is_dir():
        return _json({
            "success": False,
            "project_path": project_path,
            "summary": "Project path does not exist or is not a directory.",
            "state": {},
        })

    state = _load_state(root)
    if action not in {"read", "set", "replace", "update", "clear"}:
        return _json({
            "success": False,
            "project_path": project_path,
            "summary": "Unsupported action. Use read, replace, update, or clear.",
            "state": state,
        })

    if action == "clear":
        try:
            state["acceptance_contract"] = {
                "criteria": [],
                "verification_baseline": len(state.get("verification") or []),
                "updated_at": _now_iso(),
            }
            guard = _anchor_guard(root, _guard_from_state(state))
            guard.update({
                "acceptance_required": False,
                "acceptance_ready": True,
                "last_acceptance_at": _now_iso(),
                "last_acceptance_reason": "No acceptance criteria recorded.",
                "last_receipt_ready": False,
            })
            state["guard"] = guard
            _save_state(root, state)
            return _json({
                "success": True,
                "project_path": project_path,
                "state_path": str(_state_path(root)),
                "summary": "Cleared acceptance contract.",
                "criteria": [],
                "satisfied": [],
                "unsatisfied": [],
                "all_satisfied": True,
                "reason": "No acceptance criteria recorded.",
                "state_recorded": True,
            })
        except Exception as exc:
            return _json({
                "success": False,
                "project_path": project_path,
                "summary": f"Failed to clear acceptance contract: {exc}",
                "state": state,
            })

    if action in {"set", "replace", "update"}:
        incoming = args.get("criteria")
        if incoming is None:
            incoming = args.get("contract")
        if incoming is None:
            return _json({
                "success": False,
                "project_path": project_path,
                "summary": "Provide 'criteria' or 'contract' for replace/update.",
                "state": state,
            })
        normalized, validation_errors = _normalize_acceptance_criteria(root, incoming)
        if validation_errors:
            return _json({
                "success": False,
                "project_path": project_path,
                "summary": "Acceptance contract validation failed.",
                "errors": validation_errors,
                "state": state,
            })
        verification = state.get("verification") or []
        verification_baseline = len(verification) if isinstance(verification, list) else 0
        contract_updated_at = _now_iso()
        if action in {"set", "replace"}:
            state["acceptance_contract"] = {
                "criteria": normalized,
                "verification_baseline": verification_baseline,
                "updated_at": contract_updated_at,
            }
        else:
            existing = state.get("acceptance_contract") or {}
            existing_criteria = existing.get("criteria") or []
            if not isinstance(existing_criteria, list):
                existing_criteria = []
            merged_by_id = {
                str(item.get("id")): item
                for item in existing_criteria
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            }
            for item in normalized:
                merged_by_id[item["id"]] = item
            state["acceptance_contract"] = {
                "criteria": list(merged_by_id.values()),
                "verification_baseline": verification_baseline,
                "updated_at": contract_updated_at,
            }
        try:
            _save_state(root, state)
        except Exception as exc:
            return _json({
                "success": False,
                "project_path": project_path,
                "summary": f"Failed to persist acceptance contract: {exc}",
                "state": state,
            })

    criteria = (state.get("acceptance_contract") or {}).get("criteria") or []
    evaluation = _evaluate_acceptance(root, state)
    receipt_ready = evaluation["all_satisfied"]
    guard = _anchor_guard(root, _guard_from_state(state))
    guard["acceptance_required"] = _acceptance_required(state)
    guard["acceptance_ready"] = receipt_ready
    guard["last_acceptance_at"] = _now_iso()
    guard["last_acceptance_reason"] = evaluation["reason"]
    if action in {"set", "replace", "update"}:
        # A changed contract defines a new stage. Prior verification remains in
        # history, but it must not keep the new evidence batch receipt-locked or
        # count as current acceptance proof (the contract baseline handles the
        # latter). Reopen a coherent edit window immediately.
        guard.update({
            "last_verify_success": None,
            "writes_since_budget": 0,
            "writes_since_verify": 0,
            "repair_patches_remaining": None,
            "failure_plan_required": False,
            "verify_required": False,
            "receipt_required": False,
            "last_budget_after_verify": False,
            "last_receipt_ready": False,
            "test_phase_required": False,
            "scope_phase_required": True,
            "last_scope_contract_reason": "Acceptance contract changed; implement and reverify the new evidence set.",
        })
    state["guard"] = guard
    try:
        _save_state(root, state)
    except Exception:
        pass

    already_complete = bool(action == "read" and guard.get("last_receipt_ready") and receipt_ready)
    return _json({
        "success": True,
        "project_path": project_path,
        "state_path": str(_state_path(root)),
        "action": action,
        "criteria": criteria,
        "satisfied": evaluation["satisfied"],
        "unsatisfied": evaluation["unsatisfied"],
        "all_satisfied": receipt_ready,
        "reason": evaluation["reason"],
        "state_recorded": True,
        "already_complete": already_complete,
        "next_required": (
            ["The stage is already receipted. Stop calling builder tools and send the final answer now."]
            if already_complete
            else []
        ),
    })


def _acceptance_required(state: Dict[str, Any]) -> bool:
    contract = state.get("acceptance_contract")
    if contract is None:
        return False
    if not isinstance(contract, dict):
        return bool(contract)
    criteria = contract.get("criteria", [])
    return bool(criteria) or not isinstance(criteria, list)


def _evaluate_acceptance(root: Path, state: Dict[str, Any]) -> Dict[str, Any]:
    contract = state.get("acceptance_contract")
    if contract is None:
        contract = {"criteria": []}
    if not isinstance(contract, dict):
        return {
            "satisfied": [],
            "unsatisfied": [{"id": "", "satisfied": False, "invalid": ["Malformed acceptance contract."]}],
            "all_satisfied": False,
            "reason": "Malformed acceptance contract: expected an object.",
        }
    criteria = contract.get("criteria", [])
    if not isinstance(criteria, list):
        return {
            "satisfied": [],
            "unsatisfied": [{"id": "", "satisfied": False, "invalid": ["Malformed criteria collection."]}],
            "all_satisfied": False,
            "reason": "Malformed acceptance contract: criteria must be a list.",
        }
    if not criteria:
        return {
            "satisfied": [],
            "unsatisfied": [],
            "all_satisfied": True,
            "reason": "No acceptance criteria recorded.",
        }

    verification = state.get("verification") or []
    if not isinstance(verification, list):
        verification = []
    try:
        verification_baseline = int(contract.get("verification_baseline", 0) or 0)
    except (TypeError, ValueError):
        verification_baseline = 0
    verification_baseline = max(0, min(len(verification), verification_baseline))
    latest_by_command: Dict[str, Dict[str, Any]] = {}
    for item in verification[verification_baseline:]:
        if not isinstance(item, dict):
            continue
        # Resume/receipt summaries are useful history, not acceptance proof.
        # Ignore them before selecting the latest record so an agent cannot
        # accidentally mask a passing builder_verify checkpoint with a later
        # human-style summary for the same command.
        if item.get("source") != "builder_verify" or "exit_code" not in item:
            continue
        command = str(item.get("command", "")).strip()
        if command:
            latest_by_command[command] = item
    successful_commands = {
        command
        for command, item in latest_by_command.items()
        if item.get("source") == "builder_verify"
        and item.get("exit_code") == 0
        and not item.get("timed_out")
        and not item.get("zero_tests_detected")
    }
    current_evidence = _acceptance_evidence_snapshot(root, state)

    satisfied: List[Dict[str, Any]] = []
    unsatisfied: List[Dict[str, Any]] = []
    reasons: List[str] = []
    seen_ids: set[str] = set()
    for criterion in criteria:
        if not isinstance(criterion, dict):
            unsatisfied.append({"id": "", "satisfied": False, "invalid": ["Criterion is not an object."]})
            reasons.append("Malformed acceptance criterion.")
            continue
        criterion_id = str(criterion.get("id") or "")
        description = str(criterion.get("description") or "")
        evidence_paths = [str(path) for path in _listify(criterion.get("evidence_paths")) if str(path).strip()]
        verification_commands = [str(cmd) for cmd in _listify(criterion.get("verification_commands")) if str(cmd).strip()]

        invalid: List[str] = []
        if not criterion_id:
            invalid.append("missing id")
        if not description:
            invalid.append("missing description")
        if not evidence_paths:
            invalid.append("no evidence paths")
        if not verification_commands:
            invalid.append("no verification commands")
        if criterion_id and criterion_id in seen_ids:
            invalid.append("duplicate id")
        seen_ids.add(criterion_id)

        missing_evidence: List[str] = []
        unsafe_evidence: List[str] = []
        for rel in evidence_paths:
            candidate = (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                unsafe_evidence.append(rel)
                continue
            if candidate == root or candidate == root / ".hermes-builder" or (root / ".hermes-builder") in candidate.parents:
                unsafe_evidence.append(rel)
                continue
            if not candidate.exists():
                missing_evidence.append(rel)

        missing_verification = [cmd for cmd in verification_commands if cmd not in successful_commands]
        changed_evidence: List[str] = []
        for command in verification_commands:
            if command not in successful_commands:
                continue
            recorded_evidence = latest_by_command[command].get("acceptance_evidence")
            if not isinstance(recorded_evidence, dict):
                changed_evidence.extend(evidence_paths)
                continue
            for evidence_path in evidence_paths:
                if evidence_path in missing_evidence or evidence_path in unsafe_evidence:
                    continue
                if recorded_evidence.get(evidence_path) != current_evidence.get(evidence_path):
                    changed_evidence.append(evidence_path)
        changed_evidence = list(dict.fromkeys(changed_evidence))
        criterion_satisfied = (
            not invalid
            and not missing_evidence
            and not unsafe_evidence
            and not missing_verification
            and not changed_evidence
        )
        entry = {
            "id": criterion_id,
            "description": description,
            "evidence_paths": evidence_paths,
            "verification_commands": verification_commands,
            "missing_evidence": missing_evidence,
            "unsafe_evidence": unsafe_evidence,
            "missing_verification": missing_verification,
            "changed_evidence": changed_evidence,
            "invalid": invalid,
            "satisfied": criterion_satisfied,
        }
        if criterion_satisfied:
            satisfied.append(entry)
        else:
            unsatisfied.append(entry)
            if missing_evidence:
                reasons.append(f"{criterion_id or 'criterion'} missing evidence: {missing_evidence[:5]}")
            if unsafe_evidence:
                reasons.append(f"{criterion_id or 'criterion'} unsafe evidence: {unsafe_evidence[:5]}")
            if missing_verification:
                reasons.append(f"{criterion_id or 'criterion'} missing verification: {missing_verification[:5]}")
            if invalid:
                reasons.append(f"{criterion_id or 'criterion'} invalid: {invalid}")
            if changed_evidence:
                reasons.append(
                    f"{criterion_id or 'criterion'} changed after verification: {changed_evidence[:5]}"
                )

    all_satisfied = not unsatisfied
    reason = "All acceptance criteria satisfied." if all_satisfied else "; ".join(reasons[:20])
    return {
        "satisfied": satisfied,
        "unsatisfied": unsatisfied,
        "all_satisfied": all_satisfied,
        "reason": reason,
    }


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
    language_policy = _language_stage_policy(root)
    env_artifacts = _environment_artifact_dirs(root)
    project_map_for_budget = _build_project_map(root, max_files=700)
    missing_required_tests = _missing_required_tests(root, project_map_for_budget)

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
    if len(source_files) >= max_source_files:
        issues.append({
            "code": "source-file-budget-exceeded",
            "message": "The current phase has reached the staged-kernel source-file budget.",
            "evidence": f"source_files={len(source_files)} >= max_source_files={max_source_files}",
        })
    if max_test_files == 0 and len(test_files) > 0 or max_test_files > 0 and len(test_files) >= max_test_files:
        issues.append({
            "code": "test-file-budget-exceeded",
            "message": "The current phase has reached the staged-kernel test-file budget.",
            "evidence": f"test_files={len(test_files)} >= max_test_files={max_test_files}",
        })
    if len(source_dirs) >= max_source_dirs:
        issues.append({
            "code": "source-dir-budget-exceeded",
            "message": "The current phase has reached the staged-kernel source-directory budget.",
            "evidence": f"source_dirs={len(source_dirs)} >= max_source_dirs={max_source_dirs}",
        })
    if mixed_package_dirs:
        issues.append({
            "code": "go-mixed-package-dirs",
            "message": "One or more Go directories contain multiple package names.",
            "evidence": json.dumps(mixed_package_dirs, ensure_ascii=True, sort_keys=True),
        })
    warnings: List[Dict[str, Any]] = []
    if env_artifacts:
        warnings.append({
            "code": "environment-artifact-dirs",
            "message": "Local dependency/environment artifact directories are present and should not be created during staged kernel builds.",
            "evidence": f"directories={env_artifacts}",
        })

    previous_guard: Dict[str, Any] = {}
    state_for_budget: Dict[str, Any] = {}
    try:
        state_for_budget = _load_state(root)
        previous_guard = _anchor_guard(root, _guard_from_state(state_for_budget))
    except Exception:
        previous_guard = {}
        state_for_budget = _default_state(root)
    scope_contract = _scope_contract_status(root, state_for_budget, project_map_for_budget)
    last_verify_at = str(previous_guard.get("last_verify_at") or "")
    last_receipt_at = str(previous_guard.get("last_receipt_at") or "")
    receipt_is_current = bool(last_receipt_at and last_verify_at and last_receipt_at >= last_verify_at)
    test_phase_pending = bool(
        missing_required_tests
        and previous_guard.get("last_verify_success") is True
    )
    scope_phase_pending = bool(
        scope_contract.get("required")
        and not scope_contract.get("ready")
        and previous_guard.get("last_verify_success") is True
    )
    post_verify_pending = (
        previous_guard.get("last_verify_success") is True
        and not after_verify
        and not receipt_is_current
        and not test_phase_pending
        and not scope_phase_pending
    )

    actions: List[str] = []
    if issues and not scope_phase_pending:
        actions.extend([
            "Stop adding files for this phase now.",
            "If verification has not passed for the current file set, run builder_verify before any more write_file or patch calls.",
            "If verification already passed, call builder_resume and builder_receipt, then defer the extra scope to a later phase.",
            f"Follow the {language_policy['preset']} preset: {language_policy['repair']}",
        ])
    elif post_verify_pending:
        actions.extend([
            "A passing builder_verify checkpoint is already recorded for this stage.",
            "Call builder_budget again with after_verify=true if needed, then call builder_receipt now.",
            "Do not write more files or send the final answer before builder_receipt.",
        ])
    elif test_phase_pending:
        actions.extend([
            "A passing compile/check checkpoint is recorded, but focused tests are still missing.",
            "Open the test/hardening phase now: add one real discovered test file for the current kernel.",
            f"Rerun builder_verify with the language test command: {language_policy['verify']}.",
            "Do not call builder_receipt as final handoff until the test verifier passes with tests discovered.",
        ])
    elif scope_phase_pending:
        actions.extend([
            "A passing verifier is recorded, but the saved objective is under-covered by the current source/test corpus.",
            f"Matched objective anchors: {scope_contract.get('matched_terms', [])}.",
            f"Missing objective anchors to consider next: {scope_contract.get('missing_terms', [])[:12]}.",
            "Open one scoped feature/test batch that covers missing objective anchors, preferring patches to existing files if the file budget is already full.",
            "Do not call builder_receipt as final handoff until scope coverage is no longer under-covered.",
        ])
    elif after_verify:
        actions.extend([
            "The current phase is within budget after verification.",
            "builder_verify has already recorded the verification; call builder_resume if you need to add checkpoint notes.",
            "Call builder_receipt if this stage is complete.",
        ])
    else:
        write_call_limit = _write_call_limit(root)
        actions.extend([
            "The current phase is within budget.",
            f"The next source/test batch is capped at {write_call_limit} write_file/patch calls.",
            "After that capped batch, run builder_budget and builder_verify before expanding scope.",
            f"Use the {language_policy['preset']} preset: {language_policy['verify']}.",
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
        if issues and not scope_phase_pending:
            guard["verify_required"] = True
        elif test_phase_pending:
            guard["writes_since_budget"] = 0
            guard["verify_required"] = False
            guard["receipt_required"] = False
            guard["test_phase_required"] = True
            guard["scope_phase_required"] = False
            guard["last_missing_tests_reason"] = "; ".join(missing_required_tests)[:1000]
        elif scope_phase_pending:
            guard["writes_since_budget"] = 0
            guard["verify_required"] = False
            guard["receipt_required"] = False
            guard["test_phase_required"] = False
            guard["scope_phase_required"] = True
            guard["last_scope_contract_reason"] = str(scope_contract.get("reason", ""))[:1000]
        elif post_verify_pending:
            guard["writes_since_budget"] = 0
            guard["verify_required"] = False
            guard["receipt_required"] = True
            guard["test_phase_required"] = False
            guard["scope_phase_required"] = False
        elif after_verify:
            guard["writes_since_budget"] = 0
            guard["verify_required"] = False
            if guard.get("last_verify_success") is True:
                if missing_required_tests:
                    guard["receipt_required"] = False
                    guard["test_phase_required"] = True
                    guard["scope_phase_required"] = False
                    guard["last_missing_tests_reason"] = "; ".join(missing_required_tests)[:1000]
                elif scope_contract.get("required") and not scope_contract.get("ready"):
                    guard["receipt_required"] = False
                    guard["test_phase_required"] = False
                    guard["scope_phase_required"] = True
                    guard["last_scope_contract_reason"] = str(scope_contract.get("reason", ""))[:1000]
                else:
                    guard["receipt_required"] = True
                    guard["test_phase_required"] = False
                    guard["scope_phase_required"] = False
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
        "policy": language_policy,
        "environment_artifacts": env_artifacts,
        "missing_required_tests": missing_required_tests,
        "scope_contract": scope_contract,
        "over_budget": bool(issues),
        "hard_stop": bool(issues and not scope_phase_pending),
        "allowed_next_tools": (
            ["builder_verify", "builder_resume", "builder_receipt"]
            if issues and not scope_phase_pending
            else ["write_file", "patch", "builder_budget", "builder_verify", "builder_resume"]
            if test_phase_pending or scope_phase_pending
            else ["builder_resume", "builder_receipt"]
            if after_verify or post_verify_pending
            else ["write_file", "patch", "builder_budget", "builder_verify"]
        ),
        "issues": issues,
        "warnings": warnings,
        "actions": actions,
        "enforcement": {
            "state_recorded": state_recorded,
            "state_warning": state_warning,
            "writes_since_budget": guard.get("writes_since_budget", 0),
            "writes_since_verify": guard.get("writes_since_verify", 0),
            "write_call_limit": _write_call_limit(root),
            "verify_required": bool(guard.get("verify_required", False)),
            "receipt_required": bool(guard.get("receipt_required", False)),
            "objective_required": bool(guard.get("objective_required", False)),
            "test_phase_required": bool(guard.get("test_phase_required", False)),
            "last_missing_tests_reason": guard.get("last_missing_tests_reason", ""),
            "scope_phase_required": bool(guard.get("scope_phase_required", False)),
            "last_scope_contract_reason": guard.get("last_scope_contract_reason", ""),
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
    language_policy = _language_stage_policy(root)
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
        recommendations.insert(1, "Node first slice should not run installs or create node_modules; use package.json, one module, and one node:test file.")
    if project_map.get("swift"):
        recommendations.insert(0, "SwiftPM detected; builder_verify defaults to swift build and swift test when commands are omitted.")
        recommendations.insert(1, "Swift first slice should be one library target, one XCTest target, one implementation file, and one XCTest file.")
    if project_map.get("python"):
        recommendations.insert(0, "Python project detected; builder_verify defaults to unittest for plain tests, pytest only when declared, and compileall when no tests exist.")
        recommendations.insert(1, "Python stdlib first slice should not run pip/uv installs or create .venv; prefer unittest unless pytest is explicitly declared.")
    if project_map.get("rust"):
        recommendations.insert(0, "Cargo project detected; builder_verify defaults to cargo test when commands are omitted.")
        recommendations.insert(1, "Rust first slice should prefer Cargo.toml plus src/lib.rs with inline tests; full cargo test is final proof.")
    if project_map.get("go"):
        recommendations.insert(0, "Go module detected; builder_verify defaults to go test ./... when commands are omitted.")
        recommendations.insert(1, "Go first slice should be go.mod, one package implementation file, and one *_test.go file with one package name per directory.")
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
        "policy": language_policy,
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
    language_policy = _language_stage_policy(root)
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
        verify_command = f"builder_verify command: {language_policy['verify']}"
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
            "goal": "Record the objective, acceptance proof, first phase, important decisions, and immediate next steps.",
            "max_file_batch": 0,
            "tools": ["builder_resume", "builder_acceptance"],
            "done_when": "Project-local state contains the objective, next steps, and concrete acceptance criteria with evidence paths and verifier commands.",
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
            "goal": "Evaluate acceptance, then summarize files, decisions, verification, and remaining limitations before the final answer.",
            "max_file_batch": 0,
            "tools": ["builder_acceptance", "builder_receipt"],
            "done_when": "Every recorded acceptance criterion is satisfied and the compact receipt includes its artifacts and passing verifier commands.",
        },
    ])

    phases = phases[:max_phases]
    state_recorded = False
    state_warning = ""
    scope_contract = {
        "required": False,
        "objective_terms": _extract_objective_terms(objective),
    }
    try:
        state = _load_state(root)
        if objective and not state.get("objective"):
            state["objective"] = objective
        guard = _anchor_guard(root, _guard_from_state(state))
        guard["language_profile"] = _detect_language_profile(root)
        guard["objective_required"] = not bool(str(state.get("objective") or "").strip())
        state["guard"] = guard
        _save_state(root, state)
        state_recorded = True
    except Exception as exc:
        state_warning = f"Could not persist builder objective: {exc}"
    if objective:
        scope_contract = {
            "required": len(_extract_objective_terms(objective)) >= 6,
            "objective_terms": _extract_objective_terms(objective),
            "receipt_rule": (
                "builder_receipt checks that verified source/test files cover enough saved objective anchors "
                "before final handoff."
            ),
        }
    else:
        scope_contract = {
            "required": False,
            "objective_terms": [],
            "next_required": "Call builder_resume action=update with objective set to the concrete user request before source edits.",
        }
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
        "policy": language_policy,
        "scope_contract": scope_contract,
        "state_recorded": state_recorded,
        "state_warning": state_warning,
        "phases": phases,
        "rules": [
            "Touch no more than the phase max_file_batch before verifying or recording state.",
            f"Use the {language_policy['preset']} first slice: " + "; ".join(language_policy["first_slice"]),
            "Forbidden in this first slice: " + "; ".join(language_policy["forbidden"]),
            "Hard stop after 4 file writes/patches in one phase: run builder_verify before expanding scope.",
            "Call builder_budget after each source/test batch and after successful verification; if it reports over_budget, stop adding scope and receipt/defer.",
            "Before source edits, call builder_acceptance action=replace with non-empty criteria; each criterion needs project evidence paths and exact builder_verify commands.",
            "After builder_budget reports within budget, the next source/test batch is still capped at two files or three write_file/patch calls before builder_verify.",
            "For super-complex objectives, build a verified kernel first and record deferred layers instead of attempting the full system in one turn.",
            "Before writing source, choose stable language identity and keep it consistent: Node module style, Swift target names, Python import root, Rust crate/module names, and one Go package name per directory.",
            "For Go, if builder_map shows mixed_package_dirs or builder_verify reports found packages X and Y, fix package declarations or move files before behavior work.",
            "After the first builder_verify, fix only verification failures; do not add new features.",
            "After any failed builder_verify, make at most two focused patches before rerunning builder_verify.",
            "After builder_verify succeeds, do not rerun the same command via terminal; call builder_resume, builder_budget, then builder_receipt.",
            "If verification still fails after one focused fix pass, call builder_receipt and report the remaining failure.",
            "Before the session reaches roughly 70% of the active model context (or 45k tokens when the limit is unknown), force a checkpoint/receipt instead of starting another feature pass.",
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
    state = _load_state(root)

    if action == "replace":
        # ``builder_resume`` owns checkpoint fields, not the acceptance
        # contract or its guard evidence. Models commonly seed acceptance and
        # resume state in the same parallel tool turn. Replacing the entire
        # document here used to let the resume write silently erase a contract
        # that builder_acceptance had just persisted.
        defaults = _default_state(root)
        for field in (
            "objective",
            "status",
            "current_phase",
            "completed",
            "next_steps",
            "decisions",
            "files_touched",
            "verification",
            "notes",
        ):
            state[field] = defaults[field]

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
                normalized_verification: List[Dict[str, Any]] = []
                for item in incoming:
                    if isinstance(item, dict):
                        note = dict(item)
                        note.pop("acceptance_evidence", None)
                        note["source"] = "builder_resume"
                        note["recorded_at"] = _now_iso()
                    else:
                        note = {
                            "source": "builder_resume",
                            "note": _clip(item, 2000),
                            "recorded_at": _now_iso(),
                        }
                    normalized_verification.append(note)
                incoming = normalized_verification
            else:
                incoming = [_clip(item, 1200) for item in incoming]
            state[state_key] = _append_unique(list(state.get(state_key, [])), incoming, max_items=max_items)

        guard = _anchor_guard(root, _guard_from_state(state))
        guard["language_profile"] = _detect_language_profile(root)
        guard["objective_required"] = not bool(str(state.get("objective") or "").strip())
        guard["last_receipt_ready"] = False
        # Manual verification notes are deliberately non-authoritative. Only
        # builder_verify may change verifier guard state or unlock a receipt.
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
        if guard.get("last_verify_success") is True:
            next_required.extend([
                "The manual verification summary was saved as a note; the trusted passing builder_verify checkpoint remains authoritative.",
                "Call builder_budget with after_verify=true, then builder_receipt before adding more scope.",
            ])
        else:
            next_required.extend([
                "Manual verification summaries are checkpoint notes, not proof.",
                "Run builder_verify before builder_budget or builder_receipt.",
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
    compact = bool(args.get("compact", True))

    root = Path(project_path).resolve()
    if not root.is_dir():
        return _json({
            "success": False,
            "project_path": project_path,
            "summary": "Project path does not exist or is not a directory.",
            "receipt": {},
        })

    project_map = _build_project_map(root, max_files=700)
    missing_required_tests = _missing_required_tests(root, project_map)
    state_path = _state_path(root)
    state = _load_state(root)
    scope_contract = _scope_contract_status(root, state, project_map)
    acceptance = _evaluate_acceptance(root, state)
    acceptance_required = _acceptance_required(state)
    state_exists = state_path.exists()
    git = _git_status(root, max_lines=max_files)
    guard = _anchor_guard(root, _guard_from_state(state))

    if (
        guard.get("last_receipt_ready")
        and not guard.get("last_receipt_blocked_reason")
        and _safe_int(guard.get("writes_since_verify", 0), 0, min_value=0) == 0
        and (not acceptance_required or acceptance.get("all_satisfied"))
    ):
        return _json({
            "success": True,
            "project_path": str(root),
            "state_path": str(state_path),
            "ready_to_report": True,
            "already_complete": True,
            "blocking_warnings": [],
            "summary": "This unchanged stage already has a successful receipt.",
            "receipt": {
                "project": project_map["name"],
                "project_path": str(root),
                "files_touched": list(state.get("files_touched", []))[:max_files],
                "acceptance_contract": {"required": acceptance_required, **acceptance},
                "warnings": [],
                "blocking_warnings": [],
            },
            "next_required": [
                "Stop calling builder tools and send the final answer now; no project evidence changed."
            ],
            "state_recorded": False,
            "state_warning": "",
        })

    touched = list(state.get("files_touched", []))[:max_files]
    if not touched and git.get("changed_files"):
        touched = [line[3:] if len(line) > 3 else line for line in git.get("changed_files", [])][:max_files]

    verification = list(state.get("verification", []))
    for item in verification_results:
        verification.append(item if isinstance(item, dict) else {"note": _clip(item, 2000)})

    warnings: List[str] = []
    blocking_warnings: List[str] = []
    latest_verification = next(
        (
            item
            for item in reversed(state.get("verification", []))
            if isinstance(item, dict)
            and item.get("source") == "builder_verify"
            and "exit_code" in item
        ),
        None,
    )
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
    for reason in missing_required_tests:
        blocking_warnings.append(reason)
    if scope_contract.get("required") and not scope_contract.get("ready"):
        blocking_warnings.append(
            "Saved objective is under-covered by the verified source/test corpus: "
            f"{scope_contract.get('reason')} Missing anchors include "
            f"{scope_contract.get('missing_terms', [])[:12]}."
        )
    if acceptance_required and not acceptance.get("all_satisfied"):
        blocking_warnings.append(
            "Recorded acceptance contract is not satisfied: "
            f"{acceptance.get('reason', 'missing acceptance proof')}"
        )
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
        "language_profile": guard.get("language_profile") or _detect_language_profile(root),
        "objective": state.get("objective", ""),
        "status": state.get("status", ""),
        "current_phase": state.get("current_phase", ""),
        "completed": state.get("completed", [])[:max_files],
        "next_steps": state.get("next_steps", [])[:max_files],
        "decisions": state.get("decisions", [])[:max_files],
        "files_touched": touched,
        "verification": verification[-min(max_files, 12):],
        "scope_contract": scope_contract,
        "acceptance_contract": {
            "required": acceptance_required,
            **acceptance,
        },
        "available_scripts": project_map["scripts"],
        "git": git,
        "warnings": blocking_warnings + warnings,
        "blocking_warnings": blocking_warnings,
    }
    if not compact:
        receipt.update({
            "node": project_map.get("node", {}),
            "swift": project_map.get("swift", {}),
            "python": project_map.get("python", {}),
            "rust": project_map.get("rust", {}),
            "go": project_map.get("go", {}),
        })

    state_recorded = False
    state_warning = ""
    try:
        guard = _anchor_guard(root, _guard_from_state(state))
        guard["acceptance_required"] = acceptance_required
        guard["acceptance_ready"] = bool(acceptance.get("all_satisfied"))
        guard["last_acceptance_at"] = _now_iso()
        guard["last_acceptance_reason"] = str(acceptance.get("reason", ""))[:1000]
        guard["last_receipt_ready"] = ready_to_report
        if ready_to_report:
            guard["receipt_required"] = False
            guard["verify_required"] = False
            guard["writes_since_budget"] = 0
            guard["failure_plan_required"] = False
            guard["test_phase_required"] = False
            guard["last_missing_tests_reason"] = ""
            guard["scope_phase_required"] = False
            guard["last_scope_contract_reason"] = ""
            guard["last_receipt_blocked_reason"] = ""
            guard["last_receipt_ready"] = True
        elif missing_required_tests and guard.get("last_verify_success") is True:
            guard["receipt_required"] = False
            guard["verify_required"] = False
            guard["writes_since_budget"] = 0
            guard["failure_plan_required"] = False
            guard["test_phase_required"] = True
            guard["scope_phase_required"] = False
            guard["last_missing_tests_reason"] = "; ".join(missing_required_tests)[:1000]
            guard["last_receipt_blocked_reason"] = "; ".join(blocking_warnings or warnings)[:1000]
        elif scope_contract.get("required") and not scope_contract.get("ready") and guard.get("last_verify_success") is True:
            guard["receipt_required"] = False
            guard["verify_required"] = False
            guard["writes_since_budget"] = 0
            guard["failure_plan_required"] = False
            guard["test_phase_required"] = False
            guard["scope_phase_required"] = True
            guard["last_scope_contract_reason"] = str(scope_contract.get("reason", ""))[:1000]
            guard["last_receipt_blocked_reason"] = "; ".join(blocking_warnings or warnings)[:1000]
        else:
            guard["last_receipt_blocked_reason"] = "; ".join(blocking_warnings or warnings)[:1000]
            guard["last_receipt_ready"] = False
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
        "next_required": (
            ["Stage complete. Stop calling builder tools and send the final answer now."]
            if ready_to_report
            else [
                "Address the blocking_warnings only. Do not rerun an unchanged verifier or receipt."
            ]
        ),
        "state_recorded": state_recorded,
        "state_warning": state_warning,
    })
