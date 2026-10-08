#!/usr/bin/env bash
set -euo pipefail

BRANCH="${AUTO_POST_BRANCH:-main}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"
VENV_PYTHON="$VENV_DIR/bin/python"

cd "$(dirname "$0")/.."

# Prevent overlapping invocations from racing commits or publishing twice.
exec 9>".git/ayncode-publish.lock"
flock -n 9 || { echo "Journal publisher already running; skipping."; exit 0; }

if [ -s "$HOME/.nvm/nvm.sh" ]; then
  source "$HOME/.nvm/nvm.sh"
  nvm use --silent default >/dev/null || nvm use --silent node >/dev/null
fi
command -v openclaw >/dev/null || { echo "OpenClaw is not on PATH." >&2; exit 1; }
git pull --ff-only origin "$BRANCH"

if [ ! -x "$VENV_PYTHON" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install .

export AUTO_POST_GENERATOR_COMMAND="${AUTO_POST_GENERATOR_COMMAND:-$VENV_PYTHON scripts/openclaw_codex_article_generator.py}"
export AUTO_POST_REQUIRE_GENERATOR="${AUTO_POST_REQUIRE_GENERATOR:-true}"
export AUTO_POST_EDITORIAL_BACKEND="openclaw"
export AUTO_POST_EVENT_HOURS="${AUTO_POST_EVENT_HOURS:-168}"
export AUTO_POST_FALLBACK_EVENT_HOURS="${AUTO_POST_FALLBACK_EVENT_HOURS:-336}"
# Publishing repo content never needs the website's database.
export DATABASE_URL="sqlite:///:memory:"
export AUTO_POST_DYNAMIC_TOPIC="${AUTO_POST_DYNAMIC_TOPIC:-true}"
export AUTO_POST_USE_IMAGE_GENERATION="${AUTO_POST_USE_IMAGE_GENERATION:-false}"
export AUTO_POST_IMAGE_MODEL="${AUTO_POST_IMAGE_MODEL:-comfy/workflow}"
export AUTO_POST_IMAGE_ASPECT_RATIO="${AUTO_POST_IMAGE_ASPECT_RATIO:-16:9}"
export AUTO_POST_TOPIC="${AUTO_POST_TOPIC:-AI tech news}"
export AUTO_POST_EVENT_QUERY="${AUTO_POST_EVENT_QUERY:-artificial intelligence OR AI OR OpenAI OR Anthropic OR Google DeepMind OR Microsoft AI OR Nvidia OR robotics OR chips OR developer tools OR cloud software OR cybersecurity OR enterprise software}"
export AUTO_POST_AUDIENCE="${AUTO_POST_AUDIENCE:-founders}"
export AUTO_POST_ANGLE="${AUTO_POST_ANGLE:-Keep the article tightly tied to a recent AI-heavy tech news topic, make the title distinct from prior posts, and focus on practical implications for builders, founders, and operators.}"
if [ "${1:-}" = "--dry-run" ]; then
  shift
  "$VENV_PYTHON" scripts/auto_publish.py --dry-run "$@"
else
  "$VENV_PYTHON" scripts/auto_publish.py --push "$@"
fi
