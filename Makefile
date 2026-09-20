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
NEAR      ?= 12    # strikes nearest a pick'em to scan (violations cluster there)
GAMES     ?= 12    # ladders to sweep in `make ladder-scan`
EDGE_LOG  ?= research/us_edge_log.jsonl

# makes API keys from ENVFILE available to any recipe line
LOAD = set -a; . $(ENVFILE) 2>/dev/null || true; set +a;

.DEFAULT_GOAL := help
.PHONY: help setup pull check hunt account cancel flatten flatten-live calibrate tape watch lag scores keynumbers ladder ladder-test ladder-scan paper live-test run stop restart status logs logs-paper results report install-services

help:
	@echo ""
	@echo "  Polymarket US bot"
	@echo "  ------------------------------------------------------------"
	@echo "  make setup           install python deps into .venv"
	@echo "  make pull            git pull latest code"
	@echo "  make check           verify keys, show balance + wallet programs"
	@echo "  make account         your cash, open orders, positions, real rewards"
	@echo "  make cancel          cancel ALL open orders (clean slate)"
	@echo "  make flatten         DRY RUN: show the sells that would unwind inventory"
	@echo "  make flatten-live    actually post those maker sells"
	@echo "  make hunt            scan EVERY program, list quotable markets"
	@echo "                       e.g.  make hunt PERIOD=daily  |  make hunt MIN_POOL=0"
	@echo ""
	@echo "  make paper           start PAPER run (no orders, collects data)"
	@echo "  make report          summarize the paper run"
	@echo ""
	@echo "  make live-test       place ONE tiny real order then cancel"
	@echo "  make run             start LIVE bot in the background"
	@echo "  make stop            stop the live + paper services"
	@echo "  make restart         restart the live bot"
	@echo "  make status          is it running?"
	@echo "  make logs            follow live output   (Ctrl-C to stop watching)"
	@echo "  make logs-paper      follow paper output"
	@echo ""
	@echo "  make results         real rewards earned + paper estimate"
	@echo ""
	@echo "  RESEARCH (no capital at risk)"
	@echo "  make calibrate       price vs realized win rate, split by category"
	@echo "  make tape            cache the tape for TAPE_CAT=$(TAPE_CAT) and label it"
	@echo "  make watch           log the US book against the live ESPN event feed"
	@echo "  make lag             analyse that log: latency + win-prob divergence"
	@echo "  make scores          cache historical NFL/CFB finals from ESPN"
	@echo "  make keynumbers      how lumpy football margins really are"
	@echo "  make ladder          spread-ladder shape on the live venue (GAME=<base>)"
	@echo "  make ladder-scan     sweep every ladder, total the lockable dollars"
	@echo ""
	@echo "  knobs: SIZE=$(SIZE) contracts/order  MARKETS=$(MARKETS)  MAX_INV=\$$$(MAX_INV)/market  BUY BAND=$(MIN_PX)-$(MAX_PX)"
	@echo "         MIN_POOL=$(MIN_POOL)  MAX_TARGET=$(MAX_TARGET)  PERIOD=$(PERIOD)  ENDING_WITHIN=$(ENDING_WITHIN)"
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

flatten-live:
	@echo "posting post-only sells at the best ask for every long position…"
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

# sweep every game's ladder and total the lockable dollars
ladder-scan:
	$(LOAD) $(PY) run_ladder.py --league $(LEAGUE) --scan-all --near $(NEAR) --max-games $(GAMES)

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
