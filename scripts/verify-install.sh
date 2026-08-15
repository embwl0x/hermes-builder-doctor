#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Verify a Hermes Builder Doctor install.

Usage:
  scripts/verify-install.sh [--hermes-home PATH]

Options:
  --hermes-home PATH  Hermes home directory. Defaults to $HERMES_HOME or ~/.hermes.
  -h, --help          Show this help.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --hermes-home)
      [ "$#" -ge 2 ] || die "--hermes-home requires a path"
      HERMES_HOME="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

PLUGIN_DIR="$HERMES_HOME/plugins/builder-doctor"
SKILL_DIR="$HERMES_HOME/skills/software-development/builder-doctor"

printf 'Checking Hermes Builder Doctor install under %s\n' "$HERMES_HOME"

[ -f "$PLUGIN_DIR/plugin.yaml" ] || die "missing $PLUGIN_DIR/plugin.yaml"
[ -f "$PLUGIN_DIR/tools.py" ] || die "missing $PLUGIN_DIR/tools.py"
[ -f "$PLUGIN_DIR/__init__.py" ] || die "missing $PLUGIN_DIR/__init__.py"
[ -f "$SKILL_DIR/SKILL.md" ] || die "missing $SKILL_DIR/SKILL.md"

python3 -m py_compile "$PLUGIN_DIR/tools.py" "$PLUGIN_DIR/__init__.py"

for tool in builder_map builder_doctor builder_budget builder_plan builder_resume builder_acceptance builder_verify builder_failure_plan builder_receipt; do
  grep -q "$tool" "$PLUGIN_DIR/__init__.py" || die "tool not referenced in __init__.py: $tool"
  grep -q "$tool" "$PLUGIN_DIR/plugin.yaml" "$SKILL_DIR/SKILL.md" || die "tool not documented: $tool"
done

printf 'Hermes Builder Doctor files are present and Python compiles cleanly.\n'
