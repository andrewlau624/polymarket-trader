#!/usr/bin/env bash
# Kalshi vs Polymarket US recorder under cron. Records only, never trades
# (xvenue_recorder.py, TEST_PLAN.md). Public endpoints: needs no keys.
#
#   */5 * * * *  /home/ihearthim/polymarket-trader/xvenue_cycle.sh
set -uo pipefail
cd "$(dirname "$0")" || exit 1
LOG="$PWD/xvenue.log"
export PYTHONUNBUFFERED=1
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 4000000 ]; then mv -f "$LOG" "$LOG.1"; fi
timeout 295 "$PWD/.venv/bin/python" xvenue_recorder.py --minutes 4.6 >> "$LOG" 2>&1
exit 0
