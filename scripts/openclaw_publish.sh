#!/usr/bin/env bash
set -euo pipefail

BRANCH="${AUTO_POST_BRANCH:-main}"

git pull --rebase origin "$BRANCH"
python scripts/auto_publish.py --push
