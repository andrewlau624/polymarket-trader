# Polymarket US bot — one-word commands.
#
#   make            list every command
#   make check      verify API keys + show balances / target-size costs
#   make paper      start the no-risk paper run (collects data)
#   make live-test  place ONE tiny real order, then cancel (1 minute)
#   make run        start the live bot as a background service
#   make stop       stop everything
#   make results    show real rewards earned + paper report
#   make logs       follow the live bot's output
#
# Override defaults inline, e.g.:  make live-test SIZE=10
#                                   make run MARKETS=3 SIZE=10

SHELL     := /bin/bash
APP_DIR   := $(CURDIR)
PY        := $(APP_DIR)/.venv/bin/python
ENVFILE   := /etc/pm-us.env
PAPER_SVC := pm-us-paper
LIVE_SVC  := pm-us-live
USER_NAME ?= $(shell id -un)

# params (override on the command line)
SIZE      ?= 5
NOTIONAL  ?= 0     # $ per order (overrides SIZE); e.g. 5 = ~$5/order
MARKETS   ?= 2
# $ of cost basis per market before the bot stops bidding it (0 = no cap)
MAX_INV   ?= 10
# Price band the bot is willing to BUY in. Exits are never gated by it.
# 0.60 is the crossover in run_calibration.py -- but see RESEARCH.md S3: that
# study is 77% ESPORTS and has only 15 observations in Sports, so for a UFC or
# CFB venue this number is a cross-market PRIOR (favourite-longshot bias is
# well documented in betting markets generally), not a measured result. It is
# set to avoid a region with negative evidence, not to chase a positive one.
MIN_PX    ?= 0.60
MAX_PX    ?= 0.90
MIN_POOL  ?= 1000
MAX_TARGET ?= 0    # 0 = any; e.g. 1000 to prefer small-Target-Size programs
PERIOD     ?= any  # any | early | day_of | live | daily_event  (daily_event pays daily)
CATEGORY   ?= any  # any | sports | politics | crypto | economics ...
MAX_PER_PERIOD ?= 0 # cap markets per period (2 = diversify across live/daily)
ENDING_WITHIN ?= 0 # only periods ending within N hours (faster payout signal)
RESELECT  ?= 30    # minutes between re-picking markets (live windows end fast)
SCAN      ?= 200   # candidates to book-scan in `make hunt`
WATCH_MIN ?= 120   # minutes for `make watch` to record book + event feed
TAPE_CAT  ?= Sports  # category for `make tape` (Sports, Crypto, Politics, LoL, …)
TAPE_EVENTS ?= 300
LEAGUE    ?= cfb   # cfb | nfl, for `make keynumbers` and `make ladder`
GAME      ?=       # e.g. asc-cfb-clmsn-cah-2026-09-25 for `make ladder`
# strikes nearest a pick'em to scan (violations cluster there)
NEAR      ?= 12
# ladders to sweep per cycle
GAMES     ?= 25
# minutes between full sweeps
CYCLE     ?= 20 in `make ladder-dry`
# $ for the first live trial
TRIAL_CAP ?= 2
# A trial needs ONE violation, not a full sweep. 3 games x 8 strikes is
# ~30s instead of ~15min, which shrinks the window where a dropped session
# could kill the process between the two legs of a pair.
VSLUG     ?=              # a specific market slug for `make verify`
BANKROLL  ?= 20
HOSTING   ?= 18
ES_CYCLE  ?= 3
TRIAL_GAMES ?= 3
TRIAL_NEAR ?= 8
# window for the trial. CFB plays Thu-Sat, so 1 finds nothing on a
# Sunday; 7 reaches the next slate.
TRIAL_DAYS ?= 7
RANK      ?= turnover  # turnover (return/day) | value (biggest credit)
MIN_CREDIT ?= 0.01
EDGE_LOG  ?= research/us_edge_log.jsonl

# makes API keys from ENVFILE available to any recipe line
LOAD = set -a; . $(ENVFILE) 2>/dev/null || true; set +a;

.DEFAULT_GOAL := help

man:
	@cat PLAYBOOK.md

# ============ ESPORTS ============
es:
	@cat ESPORTS.md

es-discover:
	$(LOAD) $(PY) esports_bot.py --discover

es-dry:
	$(LOAD) $(PY) esports_bot.py --bankroll $(BANKROLL) --cycle $(ES_CYCLE)

es-live:
	@echo "LIVE esports bot. bankroll \$$$(BANKROLL), quarter Kelly, halts at 20% drawdown."
	$(LOAD) $(PY) esports_bot.py --live --bankroll $(BANKROLL) --cycle $(ES_CYCLE)

es-report:
	@$(PY) -c "import json,os;p='research/esports_trades.jsonl';\
rows=[json.loads(l) for l in open(p)] if os.path.exists(p) else [];\
import collections;c=collections.Counter(r['kind'] for r in rows);\
print(f'{len(rows)} records:',dict(c));\
[print(' ARB',r['event'],round(r['profit'],3)) for r in rows if r['kind']=='arb'][:20]"

bo3:
	$(PY) run_bo3.py

# do a game's outright legs sum to 1? the only structure on outright-only sports
exhaustive:
	$(LOAD) $(PY) run_exhaustive.py

# price a ladder off a BOOKMAKER line and trade the shape, market-neutral
bookline:
	$(LOAD) $(PY) run_bookline.py $(if $(GAME),--game $(GAME),--list)

# multi-outcome / negative-risk bundles: do all legs sum to 1?
multi:
	$(LOAD) $(PY) run_multi.py

# is this worth running at all?
economics:
	@$(PY) run_economics.py --hosting $(HOSTING) --capital $(BANKROLL)

deploy:
	@cat DEPLOY.md

live:
	@cat LIVE.md

# one full cycle, exactly as cron would run it
cycle:
	@./cron_cycle.sh && tail -12 cron.log

# daily price snapshot -> calibration curve for THIS venue (cron it)
snapshot:
	$(LOAD) $(PY) snapshot_prices.py

snapshot-settle:
	$(LOAD) $(PY) snapshot_prices.py --settle

snapshot-calibrate:
	@$(PY) snapshot_prices.py --calibrate

# live LoL state -> model win probability, no venue needed
es-feed:
	@$(PY) -c "import sys;sys.path.insert(0,'.');\
from src.esports.feeds import lol_live,lol_window,lol_effective_gold;\
from src.esports.winprob import lol_win_prob;\
ms=lol_live();print(f'{len(ms)} LoL matches live');\
[print(' ',m['league'],m['teams'],'game',m['game_id']) for m in ms];\
g=[m for m in ms if m['game_id']];\
w=lol_window(g[0]['game_id']) if g else None;\
print('  state',w['state'],'goldDiff',w['gold_diff'],'-> adj',round(lol_effective_gold(w)),'-> P(blue)@25min',round(lol_win_prob(lol_effective_gold(w),25),3)) if w else print('  no live frame')"

esports-calib:
	$(PY) run_calibration.py --category Esports

# ============ THE SHORT SET ============
# Eight commands cover normal use. `make man` explains the rest.

# money, positions, orders
money: account

# what is actually running, and what is it burning
ps:
	@echo "--- our python processes ---"
	@ps -eo pid,etime,pcpu,pmem,comm,args | \
	  grep -E "python[0-9.]*( -u)? (ladder_bot|log_edge|mm_bot_us|run_crossmarket|run_calibration|run_swing_offline|label_tape)\.py" \
	  | grep -v grep || echo "  none"
	@echo "--- lockfile ---"
	@cat research/ladder_bot.lock 2>/dev/null || echo "  none"
	@echo "--- systemd (enabled = returns on reboot) ---"
	@systemctl is-enabled $(LIVE_SVC) 2>/dev/null | sed 's/^/  pm-us-live: /' || true
	@systemctl is-active  $(LIVE_SVC) 2>/dev/null | sed 's/^/  pm-us-live: /' || true
	@systemctl is-enabled $(PAPER_SVC) 2>/dev/null | sed 's/^/  pm-us-paper: /' || true
	@systemctl is-active  $(PAPER_SVC) 2>/dev/null | sed 's/^/  pm-us-paper: /' || true
	@echo "--- load ---"
	@uptime

# hunt for mispricings in the background (places nothing)
find:
	@$(MAKE) --no-print-directory ladder-bg
	@echo "scanning. check with: make found"

# what the hunt found
found:
	@$(MAKE) --no-print-directory ladder-report

# place ONE bounded live trade ($(TRIAL_CAP) cap, 3 ladders, ~30s)
trade:
	@$(MAKE) --no-print-directory ladder-trial

# get out of everything: cancel orders, exit positions where cheap
out:
	@$(MAKE) --no-print-directory cancel
	@$(MAKE) --no-print-directory flatten-cross

# same, for real
out-live:
	@$(MAKE) --no-print-directory cancel
	@$(MAKE) --no-print-directory flatten-cross-live

# what does a market actually settle on? read the venue's own rules
rules:
	@test -n "$(VSLUG)" || (echo "usage: make rules VSLUG=<market-slug>"; exit 1)
	@$(MAKE) --no-print-directory verify

# stop every bot
quiet:
	-@$(MAKE) --no-print-directory ladder-kill
	-@$(MAKE) --no-print-directory stop 2>/dev/null || true
	@echo "disabling services so they do not return on boot…"
	-@sudo systemctl disable $(LIVE_SVC) 2>/dev/null || true
	-@sudo systemctl disable $(PAPER_SVC) 2>/dev/null || true
	@echo "stopped and disabled. `make run` re-enables the old MM bot."


.PHONY: es es-discover es-dry es-live es-report es-feed bo3 exhaustive bookline multi economics deploy live cycle snapshot snapshot-settle snapshot-calibrate esports-calib money ps find found trade out out-live rules quiet man help setup pull check hunt account cancel flatten flatten-live flatten-cross flatten-cross-live calibrate tape watch lag scores keynumbers ladder ladder-test verify ladder-scan ladder-probe ladder-dry ladder-bg ladder-kill ladder-report ladder-trial crossmarket crossmarket-bg crossmarket-report families families-watch families-history allmarkets keyvertical paper live-test run stop restart status logs logs-paper results report install-services

help:
	@echo ""
	@echo "  Polymarket US"
	@echo "  ---------------------------------------------------------"
	@echo "  make money      cash, positions, orders"
	@echo "  make find       hunt for mispricings (background, no orders)"
	@echo "  make found      what the hunt found"
	@echo "  make trade      place ONE bounded live trade (\$$$(TRIAL_CAP))"
	@echo "  make out        cancel orders + exit positions (dry run)"
	@echo "  make quiet      stop every bot"
	@echo "  make ps         what is running, and its CPU"
	@echo ""
	@echo "  make rules VSLUG=<slug>   what a market settles on"
	@echo "  make man                  the full playbook"
	@echo ""
	@echo "  ESPORTS (CS2 / Valorant / LoL)"
	@echo "  make es           the strategy, in full"
	@echo "  make es-discover  what esports the venue lists"
	@echo "  make es-dry       run it, place nothing"
	@echo "  make es-live      run it for real (BANKROLL=$(BANKROLL))"
	@echo ""
	@echo "  first time here:  make setup"
	@echo "  latest code:      make pull"
	@echo ""

setup:
	@echo "installing dependencies…"
	$(PY) -m pip install -q -r requirements.txt
	@echo "done."

pull:
	git pull

check:
	$(LOAD) $(PY) mm_bot_us.py --check --period $(PERIOD) --category $(CATEGORY) --min-pool $(MIN_POOL)

account:
	$(LOAD) $(PY) mm_bot_us.py --account

cancel:
	$(LOAD) $(PY) mm_bot_us.py --cancel-all

flatten:
	$(LOAD) $(PY) mm_bot_us.py --flatten

# exit NOW at the bid: pays the spread, frees the capital immediately
flatten-cross:
	$(LOAD) $(PY) mm_bot_us.py --flatten --cross
	@echo ""
	@echo "that was a DRY RUN. to execute:  make flatten-cross-live"

flatten-cross-live:
	$(LOAD) $(PY) mm_bot_us.py --flatten --cross --live

flatten-live:
	@echo "long positions: resting sells at the ask. short positions: crossing buy-backs."
	$(LOAD) $(PY) mm_bot_us.py --flatten --live
	@echo ""
	@echo "they rest until filled. 'make account' to check, 'make cancel' to pull them."
	@echo "NOTE: 'make run' cancels all resting orders on startup, including these."

hunt:
	$(LOAD) $(PY) mm_bot_us.py --hunt --period $(PERIOD) --category $(CATEGORY) --min-pool $(MIN_POOL) --max-target $(MAX_TARGET) --scan $(SCAN)

# ---------- paper (safe) -------------------------------------------------

paper: install-services
	@echo "starting PAPER run (no real orders)…"
	@sudo systemctl enable --now $(PAPER_SVC)
	@sudo systemctl status $(PAPER_SVC) --no-pager | head -6

report:
	$(LOAD) $(PY) mm_bot_us.py --report

# ---------- research (no orders, no capital) -----------------------------

calibrate:
	$(PY) run_calibration.py --split-half --by-category

# the cached tape is mostly esports; fill in the category you actually trade
tape:
	$(PY) fetch_tape.py --category $(TAPE_CAT) --max-events $(TAPE_EVENTS) --match-only
	$(PY) label_tape.py

watch:
	@echo "logging the book + ESPN plays for $(WATCH_MIN) minutes (no orders placed)…"
	$(LOAD) $(PY) log_edge.py --minutes $(WATCH_MIN) --out $(EDGE_LOG)

lag:
	$(PY) analyze_lag.py $(EDGE_LOG)

# spread-ladder shape: key numbers + monotonicity (direction-neutral)
scores:
	$(PY) fetch_scores.py --league nfl --from 2012 --to 2025
	$(PY) fetch_scores.py --league cfb --from 2016 --to 2025

keynumbers:
	$(PY) run_keynumbers.py --league $(LEAGUE)

ladder:
	$(LOAD) $(PY) run_ladder.py --league $(LEAGUE) $(if $(GAME),--slug-prefix $(GAME),--list)

ladder-test:
	$(PY) run_ladder.py --self-test

# confirm what a strike settles on, using games that ALREADY resolved
verify:
	$(LOAD) $(PY) verify_semantics.py $(if $(VSLUG),--slug $(VSLUG),)

# sweep every game's ladder and total the lockable dollars
ladder-scan:
	$(LOAD) $(PY) run_ladder.py --league $(LEAGUE) --scan-all --near $(NEAR) --max-games $(GAMES)

# Does the venue allow selling a contract we do not hold? The paired trade is
# impossible if not. Sends ONE 1-share sell and cancels it.
ladder-probe:
	$(LOAD) $(PY) ladder_bot.py --probe --yes

# Scans the whole slate on a cycle and places NOTHING.
ladder-dry:
	$(LOAD) $(PY) ladder_bot.py --near $(NEAR) --max-games $(GAMES) --cycle-min $(CYCLE) --rank $(RANK)

# Same, detached: survives ^C and logout. Check on it with `make ladder-report`.
ladder-bg:
	@$(LOAD) nohup $(PY) -u ladder_bot.py --near $(NEAR) --max-games $(GAMES) \
	  --cycle-min $(CYCLE) --rank $(RANK) > ladder_dry.out 2>&1 & echo "pid $$! -> ladder_dry.out"
	@echo "stop it with:  make ladder-kill"

ladder-kill:
	@if [ -f research/ladder_bot.lock ]; then \
	  pid=$$(cut -d' ' -f1 research/ladder_bot.lock); \
	  kill $$pid 2>/dev/null && echo "stopped pid $$pid" || echo "pid $$pid already gone"; \
	  rm -f research/ladder_bot.lock; \
	else echo "no lockfile"; fi
	@# sweep up any instance started before the lockfile existed
	-@pkill -f "[l]adder_bot.py --" 2>/dev/null && echo "swept a stray" || true

# two books, one event: moneyline vs the ladder's zero crossing
crossmarket:
	$(LOAD) $(PY) run_crossmarket.py --max-games $(GAMES)

# same, detached: survives a dropped ssh session. results also land in
# research/crossmarket.jsonl either way.
crossmarket-bg:
	@$(LOAD) nohup $(PY) -u run_crossmarket.py --max-games $(GAMES) \
	  > crossmarket.out 2>&1 & echo "pid $$! -> crossmarket.out"
	@echo "watch it with:  tail -f crossmarket.out"

crossmarket-report:
	@$(PY) crossmarket_report.py
	@tail -3 crossmarket.out 2>/dev/null || true

# exact-margin verticals: small premium, ~100:1 payoff, REAL risk
keyvertical:
	@test -n "$(GAME)" || (echo "usage: make keyvertical GAME=asc-cfb-clmsn-cah-2026-09-25"; exit 1)
	$(LOAD) $(PY) run_keyvertical.py --slug-prefix $(GAME) --league $(LEAGUE)

# what market families exist at all (totals? UFC rounds? half/quarter ladders?)
families:
	$(LOAD) $(PY) run_crossmarket.py --families

# snapshot the venue's market families and flag anything NEW. A ladder on a
# nightly sport (NBA/NHL from late Oct) is what makes this a daily strategy.
families-watch:
	$(LOAD) $(PY) watch_families.py

# EVERY market the venue lists, not just the reward-paying ones
allmarkets:
	$(LOAD) $(PY) list_all_markets.py --probe

families-history:
	@$(PY) watch_families.py --history

ladder-report:
	@$(PY) ladder_report.py
	@echo ""
	@tail -3 ladder_dry.out 2>/dev/null || true

# First LIVE run: tiny cap, one sweep, games settling within a day so the
# settlement semantics get confirmed tonight instead of next weekend.
ladder-trial:
	@echo "stopping any dry scanner first (it holds the rate-limit lock)…"
	-@$(MAKE) --no-print-directory ladder-kill
	@echo "LIVE. cap \$$$(TRIAL_CAP), one sweep, games settling within $(TRIAL_DAYS) day(s)."
	@echo "Watch for [PAIRED] vs [failed]. Then: make account"
	@echo "Afterwards, restart the scanner with: make ladder-bg"
	@echo "takes ~30s. do NOT ^C it: that can kill it between the two legs of a pair."
	$(LOAD) $(PY) ladder_bot.py --live --once --max-capital $(TRIAL_CAP) \
	  --max-days $(TRIAL_DAYS) --near $(TRIAL_NEAR) --max-games $(TRIAL_GAMES) \
	  --min-credit $(MIN_CREDIT)

# ---------- live ---------------------------------------------------------

live-test:
	@echo "LIVE TEST: placing 1 tiny order ($(SIZE) contracts), bid-only."
	@echo "It cancels automatically when done. Watch for any 'error:' line."
	@$(LOAD) $(PY) mm_bot_us.py --live --buy-only \
		--max-markets 1 --min-pool $(MIN_POOL) --size $(SIZE) \
		--iterations 2 --refresh 20
	@echo ""
	@echo "if you saw no error, the order placed fine. Now run:  make run"

run: install-services
	@echo "starting LIVE bot ($(SIZE) contracts/order, $(MARKETS) markets, max \$$$(MAX_INV) inventory each)…"
	@sudo systemctl enable --now $(LIVE_SVC)
	@sudo systemctl restart $(LIVE_SVC)
	@sudo systemctl status $(LIVE_SVC) --no-pager | head -6
	@echo ""
	@echo "check back with:  make results"

stop:
	-@sudo systemctl stop $(LIVE_SVC) 2>/dev/null || true
	-@sudo systemctl stop $(PAPER_SVC) 2>/dev/null || true
	@echo "stopped. (all resting orders were cancelled)"
	@echo "NOTE: still ENABLED, so it returns on reboot. `make quiet` disables it."

restart:
	@sudo systemctl restart $(LIVE_SVC)
	@sudo systemctl status $(LIVE_SVC) --no-pager | head -6

status:
	@echo "--- live ---";  sudo systemctl status $(LIVE_SVC)  --no-pager 2>/dev/null | head -6 || echo "not installed"
	@echo "--- paper ---"; sudo systemctl status $(PAPER_SVC) --no-pager 2>/dev/null | head -6 || echo "not installed"

logs:
	@sudo journalctl -u $(LIVE_SVC) -f

logs-paper:
	@sudo journalctl -u $(PAPER_SVC) -f

# ---------- results ------------------------------------------------------

results:
	@echo "=== REAL rewards earned (from Polymarket US) ==="
	@$(LOAD) $(PY) -c "from src.pm_us.client import UsClient; import json; print(json.dumps(UsClient().earnings(), indent=1))"
	@echo ""
	@echo "=== PAPER estimate (what the bot thinks it would earn) ==="
	@$(LOAD) $(PY) mm_bot_us.py --report

# ---------- systemd units ------------------------------------------------

install-services:
	@printf '%s\n' \
	  '[Unit]' \
	  'Description=Polymarket US paper run' \
	  'After=network-online.target' \
	  'Wants=network-online.target' \
	  '' \
	  '[Service]' \
	  'Type=simple' \
	  'User=$(USER_NAME)' \
	  'WorkingDirectory=$(APP_DIR)' \
	  'EnvironmentFile=-$(ENVFILE)' \
	  'ExecStart=$(PY) -u mm_bot_us.py --max-markets 15 --min-pool $(MIN_POOL) --refresh 60' \
	  'Restart=always' \
	  'RestartSec=15' \
	  '' \
	  '[Install]' \
	  'WantedBy=multi-user.target' \
	  | sudo tee /etc/systemd/system/$(PAPER_SVC).service >/dev/null
	@printf '%s\n' \
	  '[Unit]' \
	  'Description=Polymarket US live market maker' \
	  'After=network-online.target' \
	  'Wants=network-online.target' \
	  '' \
	  '[Service]' \
	  'Type=simple' \
	  'User=$(USER_NAME)' \
	  'WorkingDirectory=$(APP_DIR)' \
	  'EnvironmentFile=-$(ENVFILE)' \
	  'ExecStart=$(PY) -u mm_bot_us.py --live --buy-only --max-markets $(MARKETS) --min-pool $(MIN_POOL) --max-target $(MAX_TARGET) --category $(CATEGORY) --period $(PERIOD) --max-per-period $(MAX_PER_PERIOD) --ending-within $(ENDING_WITHIN) --size $(SIZE) --notional $(NOTIONAL) --max-inventory $(MAX_INV) --min-price $(MIN_PX) --max-price $(MAX_PX) --refresh 30 --reselect-min $(RESELECT)' \
	  'Restart=always' \
	  'RestartSec=20' \
	  '' \
	  '[Install]' \
	  'WantedBy=multi-user.target' \
	  | sudo tee /etc/systemd/system/$(LIVE_SVC).service >/dev/null
	@sudo systemctl daemon-reload
