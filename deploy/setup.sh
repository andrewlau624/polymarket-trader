#!/usr/bin/env bash
# One-time setup for an Oracle Always Free (or any Ubuntu) box.
#
#   bash deploy/setup.sh
#
# Then:
#   paper: sudo systemctl start mm-bot && journalctl -u mm-bot -f
#   live : edit /etc/mm-bot.env, append --live to ExecStart in
#          /etc/systemd/system/mm-bot.service, daemon-reload, restart
#
# Override the defaults if needed:  APP_DIR=/opt/polymarket RUN_USER=ubuntu bash deploy/setup.sh
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
RUN_USER="${RUN_USER:-$(id -un)}"

echo "app dir : $APP_DIR"
echo "run user: $RUN_USER"

sudo apt-get update -y
sudo apt-get install -y python3-venv

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# env file for live trading (paper mode needs nothing here)
if [ ! -f /etc/mm-bot.env ]; then
  sudo tee /etc/mm-bot.env >/dev/null <<'EOF'
# chmod 600. Only required for `--live`.
# Use a DEDICATED, low-balance wallet -- never your main one.
POLYMARKET_PRIVATE_KEY=
#POLYMARKET_FUNDER=
#POLYMARKET_SIGNATURE_TYPE=1
EOF
  sudo chmod 600 /etc/mm-bot.env
fi

sed -e "s#__APP_DIR__#$APP_DIR#g" -e "s#__USER__#$RUN_USER#g" \
  "$APP_DIR/deploy/mm-bot.service" | sudo tee /etc/systemd/system/mm-bot.service >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable mm-bot

echo
echo "installed. start it with:"
echo "  sudo systemctl start mm-bot"
echo "  journalctl -u mm-bot -f"
