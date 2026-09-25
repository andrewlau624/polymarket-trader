#!/usr/bin/env bash
# income_bot under cron. ONE crontab line, one lock (flock inside the bot):
#
#   */5 * * * *  CAP=5 /home/ihearthim/polymarket-trader/income_cycle.sh
#
# Every run manages fills/hedges/settlement first. Then:
#   * in the first 5 minutes of FULL_HOURS it scans the whole slate (pre-game)
#   * otherwise it day-trades live games for ~3.8 minutes (exits at once if
#     nothing is live), so consecutive runs cover a game end to end
# Picking the mode here, not with two cron lines, is what stops a 4-minute
# in-play loop from holding the lock across every full-scan slot.
#
# Do NOT run cron_cycle.sh on the same account at the same time: the two bots
# never touch each other's orders, but they would both spend the same cash.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
PY="$PWD/.venv/bin/python"
LOG="$PWD/income.log"
set -a; . /etc/pm-us.env 2>/dev/null || true; set +a

if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 8000000 ]; then
  mv -f "$LOG" "$LOG.1"
fi

# the old MM service cancel_all()s on boot - that would strip resting legs
for svc in pm-us-live pm-us-paper; do
  if systemctl is-enabled "$svc" >/dev/null 2>&1; then
    sudo systemctl disable "$svc" >/dev/null 2>&1 \
      || echo "[$(date -u +%FT%TZ)] !! $svc enabled and could not be disabled" >> "$LOG"
  fi
done

LIVE_FLAG=""
[ "${LIVE:-1}" = "1" ] && LIVE_FLAG="--live"
ARGS=(--capital "${CAP:-5}" --strategies "${STRATEGIES:-taker_arb,rest_hedge,value,inplay_arb,divergence}"
      --edge-min "${EDGE_MIN:-0.03}" --max-game-loss "${MAX_GAME_LOSS:-2}"
      --max-total-loss "${MAX_TOTAL_LOSS:-5}" --daily-loss "${DAILY_LOSS:-2}")

HOUR=$((10#$(date -u +%H))); MIN=$((10#$(date -u +%M)))
MODE="${MODE:-}"
if [ -z "$MODE" ]; then
  MODE=inplay
  for h in ${FULL_HOURS:-13 17 21 1}; do
    [ "$HOUR" -eq "$h" ] && [ "$MIN" -lt 5 ] && MODE=full
  done
fi
if [ "$MODE" = "full" ]; then
  timeout 1500 "$PY" income_bot.py $LIVE_FLAG "${ARGS[@]}" >> "$LOG" 2>&1
else
  timeout 290 "$PY" income_bot.py $LIVE_FLAG --manage-only \
    --inplay-minutes "${INPLAY_MIN:-3.8}" --poll "${POLL:-5}" "${ARGS[@]}" >> "$LOG" 2>&1
fi
rc=$?
[ "$rc" -ne 0 ] && echo "[$(date -u +%FT%TZ)] income_bot rc=$rc (124 = timeout, 2 = corrupt state)" >> "$LOG"
exit 0
