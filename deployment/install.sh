#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# RBX Family Manager - installation script for a fresh Linux server
#
# Usage:
#   ./deployment/install.sh              # install everything
#   ./deployment/install.sh --systemd    # also install + enable the systemd unit
#
# Safe to re-run: it upgrades dependencies instead of duplicating them.
# ---------------------------------------------------------------------------
set -euo pipefail

APP_USER="${APP_USER:-$USER}"
APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
WITH_SYSTEMD=0

[[ "${1:-}" == "--systemd" ]] && WITH_SYSTEMD=1

info()  { printf '\033[1;34m[info]\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m[ ok ]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()   { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------- 1. Python
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die \
  "python3 not found. Install it with: sudo apt update && sudo apt install -y python3 python3-venv python3-pip"

PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python 3.10+ required (found $PY_VERSION). On Ubuntu: sudo apt install -y python3.12-venv"
info "Python $PY_VERSION detected"

cd "$APP_DIR"

# ---------------------------------------------------------------- 2. venv
if [[ ! -d "$VENV_DIR" ]]; then
  info "Creating virtual environment at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --quiet --upgrade pip
info "Installing dependencies"
python -m pip install --quiet -r requirements.txt
ok "Dependencies installed"

# ---------------------------------------------------------------- 3. .env
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  warn ".env created from .env.example — you MUST edit it:"
  warn "    nano $APP_DIR/.env"
  warn "Set DISCORD_TOKEN and OWNER_ID, then re-run this script."
else
  ok ".env already present (left untouched)"
fi

# --------------------------------------------------------- 4. Database init
mkdir -p data
if [[ -f .env ]]; then
  info "Initialising database"
  python bot.py --init-db
  ok "Database ready at $(grep -E '^DATABASE_PATH=' .env | cut -d= -f2 || echo 'data/rbx_family.db')"
fi

# ------------------------------------------------------------- 5. systemd
if [[ "$WITH_SYSTEMD" -eq 1 ]]; then
  UNIT_FILE="rbx-family-manager.service"
  if command -v systemctl >/dev/null 2>&1; then
    info "Installing systemd unit"
    sudo tee /etc/systemd/system/$UNIT_FILE > /dev/null <<EOF
[Unit]
Description=RBX Family Manager Discord bot
Documentation=https://github.com/RBXFamily/rbx-family-manager
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
Environment=PATH=$VENV_DIR/bin:/usr/bin
ExecStart=$VENV_DIR/bin/python $APP_DIR/bot.py
Restart=always
RestartSec=5
# Give Discord + SQLite a moment to flush on shutdown.
TimeoutStopSec=30
KillSignal=SIGINT
StandardOutput=journal
StandardError=journal
SyslogIdentifier=rbx-family-manager
# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$APP_DIR/data

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now "$UNIT_FILE" || warn "Could not start the service yet (is .env configured?)"
    ok "systemd unit installed and enabled"
    warn "Logs: journalctl -u $UNIT_FILE -f"
  else
    warn "systemctl not found — skipping systemd installation."
  fi
fi

ok "Install complete. Start the bot with: ./deployment/start.sh"
