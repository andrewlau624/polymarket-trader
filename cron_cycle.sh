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

# MEMORY CAP, verified before it is trusted.
#
# ulimit -v limits VIRTUAL ADDRESS SPACE, not resident memory, and numpy/pandas
# reserve well over a gigabyte of mappings against ~90MB of RSS. A 700MB cap
# therefore killed python on import, before it could log anything - the guard
# added to prevent an OOM became the thing stopping the bot. It was tested on
# macOS, where ulimit -v is a no-op, so the failure only appeared in production.
#
# So: pick a limit with headroom, then PROVE the real imports survive it. If
# they do not, run uncapped rather than silently dead - a bot that cannot start
# is worse than one that might use too much memory.
MEM_MB="${MEM_MB:-3000}"
if ulimit -v $((MEM_MB * 1024)) 2>/dev/null; then
  if "$PY" -c "import numpy, pandas" >/dev/null 2>&1; then
    say "mem cap ${MEM_MB}MB (verified: numpy+pandas import under it)"
  else
    ulimit -v unlimited 2>/dev/null || true
    say "warn: ${MEM_MB}MB cap broke numpy/pandas - running UNCAPPED. Raise MEM_MB."
  fi
else
  say "warn: could not set ulimit; running uncapped"
fi

# keep our own log from becoming the next unbounded thing
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 8000000 ]; then
  mv -f "$LOG" "$LOG.1"
  say "rotated cron.log"
fi

# The old MM service calls cancel_all() on startup. With maker-first execution
# the resting orders ARE the position, so letting that service exist is a
# standing threat to every pending pair - and a reboot is enough to trigger it.
for svc in pm-us-live pm-us-paper; do
  if systemctl is-enabled "$svc" >/dev/null 2>&1; then
    say "!! $svc is ENABLED - it cancels all orders on boot. Disabling."
    sudo systemctl disable "$svc" >/dev/null 2>&1 \
      || say "   could not disable $svc; run 'make quiet'"
  fi
done

say "cycle start"

# a stale lock from a killed run must not block every future cycle
if [ -f research/ladder_bot.lock ]; then
  pid=$(cut -d' ' -f1 research/ladder_bot.lock 2>/dev/null)
  if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
    # A run that outlives its own timeout is wedged, not busy. Skipping
    # forever means every later cycle silently does nothing - which is what
    # happened when a pre-upgrade process held the lock for 20+ minutes while
    # newer cycles queued up behind it and exited.
    lock_age=$(( $(date +%s) - $(stat -c %Y research/ladder_bot.lock 2>/dev/null || echo 0) ))
    if [ "$lock_age" -gt "${LOCK_MAX_AGE:-2100}" ]; then
      say "run $pid has held the lock ${lock_age}s (over ${LOCK_MAX_AGE:-2100}s) - killing it"
      kill "$pid" 2>/dev/null || true
      sleep 3
      kill -9 "$pid" 2>/dev/null || true
      rm -f research/ladder_bot.lock
    else
      say "another run is live (pid $pid, ${lock_age}s); skipping"
      exit 0
    fi
  else
    say "clearing stale lock"
    rm -f research/ladder_bot.lock
  fi
fi

# 1. the proven trade: monotonicity pairs, one sweep
# DEPTH over frequency. The daemon spent its API budget scanning 12 strikes
# on 12 games seventy-two times a day; the edge persisted 12 of 15 times across
# ten sweeps over hours, so that frequency bought nothing. Four cron runs at 24
# strikes on 25 games use 77% fewer calls and cover 4.2x more of the board.
# Every violation found so far sat within +-6 points of the money simply
# because that is all --near 12 ever looked at.
timeout 1800 "$PY" ladder_bot.py --live --once \
  --max-capital "${CAP:-5}" --near "${NEAR:-24}" --max-games "${GAMES:-0}" --base-shares "${BASE_SHARES:-5}" \
  --min-credit "${MIN_CREDIT:-0.002}" --hurdle "${HURDLE:-0.004}" \
  ${VERTICALS:+--verticals} --min-ev "${MIN_EV:-0.02}" >> "$LOG" 2>&1
rc=$?
say "ladder sweep rc=$rc"
if [ "$rc" -ne 0 ]; then
  say "!! sweep exited non-zero. rc=124 is the timeout; rc=1 with no output"
  say "   usually means the process could not start - check MEM_MB."
fi

# 2. record prices for the forward calibration study (free, builds the NBA case)
timeout 600 "$PY" snapshot_prices.py >> "$LOG" 2>&1
say "snapshot rc=$?"

# 3. flag the day a nightly-sport ladder appears - the thing that changes the math
timeout 300 "$PY" watch_families.py 2>&1 | grep -E "NEW LADDER|nightly" >> "$LOG"

say "cycle done"

# report anything the memory cap killed, rather than failing silently
if grep -q "MemoryError\|Cannot allocate" "$LOG" 2>/dev/null; then
  say "!! a step hit the ${MEM_MB}MB cap - raise MEM_MB or report it"
fi
