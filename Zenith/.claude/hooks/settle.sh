#!/usr/bin/env bash
# Stop — when anything changed this turn, rebuild the list so the next session
# and every search see the truth, and speak up if files were dropped in by hand.
# Runs in the background; nobody ever waits on it.
set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
[ -x "$ROOT/os" ] || exit 0
[ -f "$ROOT/.os/config.json" ] || exit 0
[ -f "$ROOT/.os/.dirty" ] || exit 0
rm -f "$ROOT/.os/.dirty" 2>/dev/null
"$ROOT/os" index --notify --quiet 2>/dev/null || true
exit 0
