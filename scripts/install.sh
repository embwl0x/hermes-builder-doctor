#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Install Hermes Builder Doctor into a Hermes home.

Usage:
  scripts/install.sh [--hermes-home PATH] [--force] [--dry-run] [--verify]

Options:
  --hermes-home PATH  Hermes home directory. Defaults to $HERMES_HOME or ~/.hermes.
  --force             Replace an existing builder-doctor install after backing it up.
  --dry-run           Print planned actions without changing files.
  --verify            Run scripts/verify-install.sh after copying.
  -h, --help          Show this help.

Examples:
  scripts/install.sh --verify
  scripts/install.sh --hermes-home "$HOME/.hermes" --force --verify
EOF
}

log() {
  printf '%s\n' "$*"
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

run() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '[dry-run]'
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
  else
    "$@"
  fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
FORCE=0
DRY_RUN=0
VERIFY=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --hermes-home)
      [ "$#" -ge 2 ] || die "--hermes-home requires a path"
      HERMES_HOME="$2"
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --verify)
      VERIFY=1
      shift
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

PLUGIN_SRC="$REPO_ROOT/plugin/builder-doctor"
SKILL_SRC="$REPO_ROOT/skills/builder-doctor"
PLUGIN_DST="$HERMES_HOME/plugins/builder-doctor"
SKILL_DST="$HERMES_HOME/skills/software-development/builder-doctor"

[ -f "$PLUGIN_SRC/plugin.yaml" ] || die "missing plugin source at $PLUGIN_SRC"
[ -f "$PLUGIN_SRC/tools.py" ] || die "missing plugin tools.py at $PLUGIN_SRC"
[ -f "$SKILL_SRC/SKILL.md" ] || die "missing skill source at $SKILL_SRC"

log "Hermes home: $HERMES_HOME"
log "Plugin destination: $PLUGIN_DST"
log "Skill destination: $SKILL_DST"

if { [ -e "$PLUGIN_DST" ] || [ -e "$SKILL_DST" ]; } && [ "$FORCE" != "1" ]; then
  die "builder-doctor already appears installed; rerun with --force to replace with backups"
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
if [ "$FORCE" = "1" ]; then
  BACKUP_ROOT="$HERMES_HOME/backups/builder-doctor/$STAMP"
  if [ -e "$PLUGIN_DST" ]; then
    run mkdir -p "$BACKUP_ROOT"
    run mv "$PLUGIN_DST" "$BACKUP_ROOT/plugin"
  fi
  if [ -e "$SKILL_DST" ]; then
    run mkdir -p "$BACKUP_ROOT"
    run mv "$SKILL_DST" "$BACKUP_ROOT/skill"
  fi
fi

run mkdir -p "$HERMES_HOME/plugins" "$HERMES_HOME/skills/software-development"
run cp -R "$PLUGIN_SRC" "$PLUGIN_DST"
run cp -R "$SKILL_SRC" "$SKILL_DST"

if [ "$VERIFY" = "1" ]; then
  run bash "$SCRIPT_DIR/verify-install.sh" --hermes-home "$HERMES_HOME"
fi

if [ "$DRY_RUN" = "1" ]; then
  cat <<EOF

Dry run complete. No files were changed.

Next steps:
  1. Rerun without --dry-run to install.
  2. Restart Hermes or its gateway so plugins and skills reload.
  3. Confirm the builder-doctor toolset exposes:
     builder_map, builder_doctor, builder_budget, builder_plan, builder_resume, builder_acceptance, builder_verify, builder_failure_plan, builder_receipt.
EOF
else
  cat <<EOF

Installed Hermes Builder Doctor.

Next steps:
  1. Restart Hermes or its gateway so plugins and skills reload.
  2. Confirm the builder-doctor toolset exposes:
     builder_map, builder_doctor, builder_budget, builder_plan, builder_resume, builder_acceptance, builder_verify, builder_failure_plan, builder_receipt.
  3. For small local models, use the staged-kernel prompt pattern in docs/HERMES_AGENT_SETUP.md.
EOF
fi
