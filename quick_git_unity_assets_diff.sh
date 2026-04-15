#!/usr/bin/env bash
# Quick runner for git_unity_assets_diff.py
#   No args: prompts for dates (and optional branch), then runs Python.
#   Two YYYY-MM-DD args: ./quick_git_unity_assets_diff.sh 2026-01-01 2026-04-15 [branch] [extra python args...]
#   Otherwise: all args forwarded to Python (e.g. HEAD~20 HEAD, or full --since-date ...). *.meta excluded by default in Python.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$SCRIPT_DIR/git_unity_assets_diff.py"

if [ ! -f "$PY" ]; then
  echo "Missing: $PY" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3 or use: xcode-select --install" >&2
  exit 1
fi

DEFAULT_FLAGS=(--non-code --list-limit 30)

is_iso_date() {
  [[ "${1:-}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]
}

if [ "$#" -eq 0 ]; then
  read -r -p "Start date YYYY-MM-DD (since): " SINCE
  read -r -p "End date YYYY-MM-DD (until, inclusive): " UNTIL
  read -r -p "Branch for rev-list [HEAD]: " BRANCH
  BRANCH=${BRANCH:-HEAD}
  if [ -z "$SINCE" ] || [ -z "$UNTIL" ]; then
    echo "Both dates are required." >&2
    exit 1
  fi
  if ! is_iso_date "$SINCE" || ! is_iso_date "$UNTIL"; then
    echo "Dates must look like YYYY-MM-DD." >&2
    exit 1
  fi
  exec python3 "$PY" \
    --since-date "$SINCE" \
    --until-date "$UNTIL" \
    --date-branch "$BRANCH" \
    "${DEFAULT_FLAGS[@]}"
fi

if [ "$#" -ge 2 ] && is_iso_date "$1" && is_iso_date "$2"; then
  SINCE=$1
  UNTIL=$2
  shift 2
  BRANCH="HEAD"
  if [ "$#" -ge 1 ] && [[ "$1" != -* ]]; then
    BRANCH=$1
    shift
  fi
  exec python3 "$PY" \
    --since-date "$SINCE" \
    --until-date "$UNTIL" \
    --date-branch "$BRANCH" \
    "${DEFAULT_FLAGS[@]}" \
    "$@"
fi

exec python3 "$PY" "$@"
