#!/usr/bin/env bash
# One complete trading cycle. Designed for cron, not for a daemon.
#
# The ladder edge is SLOW: violations stood in 12 of 15 observations across ten
# sweeps over several hours. A 20-minute daemon loop was therefore paying for a
# persistent machine to re-discover the same opportunities. Four cron runs a day
# capture nearly the same thing on a box that costs nothing.
#
# That matters more than any strategy change: at $18/mo hosting this system
# loses $109/yr at ANY capital, because depth caps the edge before capital does.
# At $4/mo it makes $59/yr. At $0 it makes $107/yr.
#
#   crontab -e
#   7 13,17,21,1 * * *  /home/ihearthim/polymarket-trader/cron_cycle.sh
set -uo pipefail
cd "$(dirname "$0")" || exit 1
PY="$PWD/.venv/bin/python"
LOG="$PWD/cron.log"
set -a; . /etc/pm-us.env 2>/dev/null || true; set +a

say() { echo "[$(date -u +%FT%TZ)] $*" >> "$LOG"; }

say "cycle start"

# a stale lock from a killed run must not block every future cycle
if [ -f research/ladder_bot.lock ]; then
  pid=$(cut -d' ' -f1 research/ladder_bot.lock 2>/dev/null)
  if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
    say "another run is live (pid $pid); skipping"
    exit 0
  fi
  say "clearing stale lock"
  rm -f research/ladder_bot.lock
fi

# 1. the proven trade: monotonicity pairs, one sweep
# DEPTH over frequency. The daemon spent its API budget scanning 12 strikes
# on 12 games seventy-two times a day; the edge persisted 12 of 15 times across
# ten sweeps over hours, so that frequency bought nothing. Four cron runs at 24
# strikes on 25 games use 77% fewer calls and cover 4.2x more of the board.
# Every violation found so far sat within +-6 points of the money simply
# because that is all --near 12 ever looked at.
timeout 1800 "$PY" ladder_bot.py --live --once \
  --max-capital "${CAP:-5}" --near "${NEAR:-24}" --max-games "${GAMES:-25}" \
  --min-credit "${MIN_CREDIT:-0.01}" >> "$LOG" 2>&1
say "ladder sweep rc=$?"

# 2. record prices for the forward calibration study (free, builds the NBA case)
timeout 600 "$PY" snapshot_prices.py >> "$LOG" 2>&1
say "snapshot rc=$?"

# 3. flag the day a nightly-sport ladder appears - the thing that changes the math
timeout 300 "$PY" watch_families.py 2>&1 | grep -E "NEW LADDER|nightly" >> "$LOG"

say "cycle done"
