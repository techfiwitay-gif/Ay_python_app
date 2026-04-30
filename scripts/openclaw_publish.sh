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
export AUTO_POST_TOPIC="${AUTO_POST_TOPIC:-AI tech news}"
export AUTO_POST_EVENT_QUERY="${AUTO_POST_EVENT_QUERY:-artificial intelligence OR AI OR OpenAI OR Anthropic OR Google DeepMind OR Microsoft AI OR Nvidia OR robotics OR chips OR developer tools OR cloud software}"
export AUTO_POST_AUDIENCE="${AUTO_POST_AUDIENCE:-founders}"
export AUTO_POST_ANGLE="${AUTO_POST_ANGLE:-Keep the article tightly tied to a recent AI-heavy tech news topic, make the title distinct from prior posts, and focus on practical implications for builders, founders, and operators.}"
"$VENV_PYTHON" scripts/auto_publish.py --push
