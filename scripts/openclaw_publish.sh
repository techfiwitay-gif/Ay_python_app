#!/usr/bin/env bash
set -euo pipefail

BRANCH="${AUTO_POST_BRANCH:-main}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$(dirname "$0")/.."

git pull --rebase origin "$BRANCH"
"$PYTHON_BIN" -m pip install .
"$PYTHON_BIN" scripts/auto_publish.py --push
