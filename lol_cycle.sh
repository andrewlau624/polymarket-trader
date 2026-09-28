#!/usr/bin/env bash
# LoL recorder under cron. Records only, never trades (lol_recorder.py).
#
#   */5 * * * *  /home/ihearthim/polymarket-trader/lol_cycle.sh
#
# Exits in a second or two when the venue lists no live LoL match.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
LOG="$PWD/lol.log"
set -a; . /etc/pm-us.env 2>/dev/null || true; set +a
export PYTHONUNBUFFERED=1
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 4000000 ]; then mv -f "$LOG" "$LOG.1"; fi
timeout 295 "$PWD/.venv/bin/python" lol_recorder.py --minutes 4.6 --every "${EVERY:-3}" >> "$LOG" 2>&1
exit 0
