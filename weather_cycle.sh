#!/usr/bin/env bash
# Weather recorder under cron. Records only, never trades (weather_recorder.py).
#
#   */5 * * * *  /home/ihearthim/polymarket-trader/weather_cycle.sh
set -uo pipefail
cd "$(dirname "$0")" || exit 1
LOG="$PWD/weather.log"
set -a; . /etc/pm-us.env 2>/dev/null || true; set +a
export PYTHONUNBUFFERED=1
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 4000000 ]; then mv -f "$LOG" "$LOG.1"; fi
timeout 295 "$PWD/.venv/bin/python" weather_recorder.py --minutes 4.6 --every "${EVERY:-90}" >> "$LOG" 2>&1
exit 0
