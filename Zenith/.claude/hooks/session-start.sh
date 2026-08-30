#!/usr/bin/env bash
# SessionStart — hand Claude the state of the folder before the first prompt.
# Never blocks, never fails a session: every path exits 0.
set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
[ -x "$ROOT/os" ] || exit 0
[ -f "$ROOT/.os/config.json" ] || exit 0
"$ROOT/os" brief --json --quiet 2>/dev/null || true
exit 0
