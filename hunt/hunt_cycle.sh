#!/usr/bin/env bash
# Watchdog for the hunt recorders (record only, never trade). Starts any that is
# not running, so they survive crashes and reboots. Cron, every 5 minutes:
#
#   */5 * * * *  /home/ihearthim/polymarket-trader/hunt/hunt_cycle.sh
#
# ODDSPAPI_KEY must be in /etc/pm-us.env or the repo's .env (hunt/clp_odds.py).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
set -a; . /etc/pm-us.env 2>/dev/null || true; set +a
export PYTHONUNBUFFERED=1
PY="$PWD/.venv/bin/python"
mkdir -p research/ttlive research/niche research/lag research/clp

start() {   # start <log> <script> [args...]  - unless that script is already running
  local log=$1; shift
  pgrep -f "$1" >/dev/null && return 0
  echo "$(date -u +%FT%TZ) starting $*" >> "$log"
  nohup "$PY" "$@" >> "$log" 2>&1 &
}

start research/ttlive/run.log hunt/tt_live.py --minutes 1000000
start research/niche/run.log  hunt/niche_recorder.py --minutes 1000000
start research/lag/run.log    hunt/lag_recorder.py --minutes 1000000 --ahead 3
start research/clp/map.log    hunt/clp_map.py --loop 20
start research/clp/run.log    hunt/clp_odds.py --loop 4

# keep run logs from growing without bound (recordings are daily files, kept)
for f in research/*/run.log research/clp/map.log; do
  [ -f "$f" ] && [ "$(wc -c < "$f")" -gt 5000000 ] && mv -f "$f" "$f.1"
done
exit 0
