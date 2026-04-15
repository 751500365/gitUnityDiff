#!/usr/bin/env bash
# Same as running quick_git_unity_assets_diff.sh with no arguments (interactive dates).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/quick_git_unity_assets_diff.sh"
