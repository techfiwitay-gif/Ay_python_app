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

export AUTO_POST_GENERATOR_COMMAND="${AUTO_POST_GENERATOR_COMMAND:-$VENV_PYTHON scripts/openclaw_codex_article_generator.py}"
export AUTO_POST_REQUIRE_GENERATOR="${AUTO_POST_REQUIRE_GENERATOR:-true}"
export AUTO_POST_DYNAMIC_TOPIC="${AUTO_POST_DYNAMIC_TOPIC:-true}"
export AUTO_POST_USE_IMAGE_GENERATION="${AUTO_POST_USE_IMAGE_GENERATION:-false}"
export AUTO_POST_IMAGE_MODEL="${AUTO_POST_IMAGE_MODEL:-comfy/workflow}"
export AUTO_POST_IMAGE_ASPECT_RATIO="${AUTO_POST_IMAGE_ASPECT_RATIO:-16:9}"
export AUTO_POST_TOPIC="${AUTO_POST_TOPIC:-Microsoft AI news}"
export AUTO_POST_EVENT_QUERY="${AUTO_POST_EVENT_QUERY:-Microsoft AI OR Microsoft Copilot OR Azure AI OR Microsoft OpenAI OR Microsoft developer tools OR Microsoft Windows AI OR Microsoft cloud software}"
export AUTO_POST_AUDIENCE="${AUTO_POST_AUDIENCE:-tech readers}"
export AUTO_POST_ANGLE="${AUTO_POST_ANGLE:-Report a Microsoft-related AI or technology news story clearly. Make the title distinct from prior posts, keep claims tied to sources, and do not force a founder angle unless the story is specifically about founders or startups.}"
"$VENV_PYTHON" scripts/auto_publish.py --push
