#!/usr/bin/env bash
# income_bot under cron. Two cadences, one lock (flock inside the bot):
#
#   */5 * * * *      MODE=manage  /path/income_cycle.sh   # fills, hedges, settles
#   7 13,17,21,1 * * * MODE=full  /path/income_cycle.sh   # + scan and trade
#
# The manage pass is what bounds a naked leg to minutes. ladder_bot waited up
# to 12 hours between cron runs to notice a half-filled pair.
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
ARGS=(--capital "${CAP:-5}" --strategies "${STRATEGIES:-taker_arb,rest_hedge,value}"
      --edge-min "${EDGE_MIN:-0.03}" --max-game-loss "${MAX_GAME_LOSS:-2}"
      --max-total-loss "${MAX_TOTAL_LOSS:-5}" --daily-loss "${DAILY_LOSS:-2}")

if [ "${MODE:-full}" = "manage" ]; then
  timeout 240 "$PY" income_bot.py $LIVE_FLAG --manage-only "${ARGS[@]}" >> "$LOG" 2>&1
else
  timeout 1500 "$PY" income_bot.py $LIVE_FLAG "${ARGS[@]}" >> "$LOG" 2>&1
fi
rc=$?
[ "$rc" -ne 0 ] && echo "[$(date -u +%FT%TZ)] income_bot rc=$rc (124 = timeout, 2 = corrupt state)" >> "$LOG"
exit 0
