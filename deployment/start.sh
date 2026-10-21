#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# RBX Family Manager - start the bot in the foreground
#
#   ./deployment/start.sh
#
# Use this for local testing. On a server, run it under systemd instead:
#   sudo systemctl start rbx-family-manager
# ---------------------------------------------------------------------------
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"

cd "$APP_DIR"

if [[ ! -f .env ]]; then
  echo "[fail] No .env file found. Copy .env.example to .env and fill it in." >&2
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[info] Creating virtual environment..."
  python3 -m venv "$VENV_DIR"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt
else
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
fi

mkdir -p data
exec python bot.py "$@"
