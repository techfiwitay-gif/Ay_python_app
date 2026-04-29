#!/usr/bin/env bash
set -euo pipefail

BRANCH="${AUTO_POST_BRANCH:-main}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"
VENV_PYTHON="$VENV_DIR/bin/python"

cd "$(dirname "$0")/.."

git pull --rebase origin "$BRANCH"

if [ ! -x "$VENV_PYTHON" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install .
"$VENV_PYTHON" scripts/auto_publish.py --push
