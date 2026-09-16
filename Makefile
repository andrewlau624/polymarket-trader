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
MARKETS   ?= 2
MIN_POOL  ?= 1000
MAX_TARGET ?= 0    # 0 = any; e.g. 1000 to prefer small-Target-Size programs
PERIOD     ?= any  # any | early | day_of | live | daily_event  (daily_event pays daily)
CATEGORY   ?= any  # any | sports | politics | crypto | economics ...
MAX_PER_PERIOD ?= 0 # cap markets per period (2 = diversify across live/daily)
ENDING_WITHIN ?= 0 # only periods ending within N hours (faster payout signal)
RESELECT  ?= 15    # minutes between re-picking markets (live windows end fast)
SCAN      ?= 200   # candidates to book-scan in `make hunt`

# makes API keys from ENVFILE available to any recipe line
LOAD = set -a; . $(ENVFILE) 2>/dev/null || true; set +a;

.DEFAULT_GOAL := help
.PHONY: help setup pull check hunt account cancel paper live-test run stop restart status logs logs-paper results report install-services

help:
	@echo ""
	@echo "  Polymarket US bot"
	@echo "  ------------------------------------------------------------"
	@echo "  make setup           install python deps into .venv"
	@echo "  make pull            git pull latest code"
	@echo "  make check           verify keys, show balance + wallet programs"
	@echo "  make account         your cash, open orders, positions, real rewards"
	@echo "  make cancel          cancel ALL open orders (clean slate)"
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
	@echo "  knobs: SIZE=$(SIZE) contracts/order  MARKETS=$(MARKETS)  MIN_POOL=$(MIN_POOL)  MAX_TARGET=$(MAX_TARGET)  PERIOD=$(PERIOD)  ENDING_WITHIN=$(ENDING_WITHIN)"
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

hunt:
	$(LOAD) $(PY) mm_bot_us.py --hunt --period $(PERIOD) --category $(CATEGORY) --min-pool $(MIN_POOL) --max-target $(MAX_TARGET) --scan $(SCAN)

# ---------- paper (safe) -------------------------------------------------

paper: install-services
	@echo "starting PAPER run (no real orders)…"
	@sudo systemctl enable --now $(PAPER_SVC)
	@sudo systemctl status $(PAPER_SVC) --no-pager | head -6

report:
	$(LOAD) $(PY) mm_bot_us.py --report

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
	@echo "starting LIVE bot (bid-only, $(SIZE) contracts/order, $(MARKETS) markets)…"
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
	  'ExecStart=$(PY) -u mm_bot_us.py --live --buy-only --max-markets $(MARKETS) --min-pool $(MIN_POOL) --max-target $(MAX_TARGET) --category $(CATEGORY) --period $(PERIOD) --max-per-period $(MAX_PER_PERIOD) --ending-within $(ENDING_WITHIN) --size $(SIZE) --refresh 30 --reselect-min $(RESELECT)' \
	  'Restart=always' \
	  'RestartSec=20' \
	  '' \
	  '[Install]' \
	  'WantedBy=multi-user.target' \
	  | sudo tee /etc/systemd/system/$(LIVE_SVC).service >/dev/null
	@sudo systemctl daemon-reload
