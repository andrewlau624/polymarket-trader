"""Polymarket market-making bot for liquidity rewards.

Strategy: in each rewards market, rest qualifying orders within max_spread of
the midpoint on both outcome tokens (buy YES and buy NO), sized >= min_size,
and refresh as the book moves. If both bids fill you own a complete set that
merges for $1 for less than $1 -> spread capture, on top of the rewards.

Safety first: paper broker is the default. Live orders require --live AND
POLYMARKET_PRIVATE_KEY, and every order is checked against notional and
inventory caps.
"""

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
import yaml

from src.pm import clob, rewards, rewards_api, selector, sizing, stats
from src.pm.execution import ClobBroker, PaperBroker


DATA_API = "https://data-api.polymarket.com"


def log(path, row):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(row) + "\n")


def best_mid(bids, asks):
    if bids and asks:
        return (bids[0][0] + asks[0][0]) / 2.0
    if bids:
        return bids[0][0]
    if asks:
        return asks[0][0]
    return None


class MarketMaker:
    def __init__(self, cfg, args):
        self.cfg = cfg
        self.args = args
        self.orders = {}  # condition_id -> [order_id]
        self._notional = {}       # condition_id -> resting buy notional
        self._total_notional = 0.0
        self.last_mid = {}        # token_id -> mid we last quoted at
        self.last_trade_ts = {}   # token_id -> newest trade timestamp seen
        self.reward_estimate = {}  # condition_id -> last est $/day
        self.start_time = time.time()
        self.bankroll = float(cfg.get("bankroll", 100.0))
        self.reward_accrued = 0.0
        self._n_fills_logged = 0
        self.selected = []
        # accounting / edge measurement
        self.avg_cost = {}
        self.realized = 0.0
        self.last_book = {}      # token_id -> (best_bid, best_ask)
        self.pending_fills = []  # (token, side, price, ts) awaiting drift check
        self.adverse = []
        self.buys = 0
        self.sells = 0
        self._last_select = time.time()
        self.scoring = None
        self.earnings_real = None
        if args.live:
            self.broker = ClobBroker()
            self.mode = "live"
        else:
            self.broker = PaperBroker()
            self.mode = "paper"

    def _cancel(self, cond):
        for oid in self.orders.get(cond, []):
            self.broker.cancel(oid)
        self.orders[cond] = []
        self._total_notional -= self._notional.get(cond, 0.0)
        self._notional[cond] = 0.0

    def _quote(self, cond, token_id, side, price, size):
        risk = self.cfg["risk"]
        if side == "buy":
            notional = size * price
            self._notional[cond] = self._notional.get(cond, 0.0)
            if self._notional[cond] + notional > risk["max_notional_per_market"]:
                return None
            if self._total_notional + notional > risk["max_total_notional"]:
                return None
            if self.broker.positions.get(token_id, 0.0) + size > risk["max_inventory_per_token"]:
                return None
            self._notional[cond] += notional
            self._total_notional += notional
        oid = self.broker.place(token_id, side, price, size, condition_id=cond)
        self.orders.setdefault(cond, []).append(oid)
        return oid

    def run_market(self, market):
        cond = market["condition_id"]
        ms = market["max_spread_cents"]
        min_size = max(market["min_size"], market["min_order_size"])
        size = min_size * self.args.size_mult
        d = self.args.spread_frac * ms / 100.0
        tick = market["tick"]

        books = {}
        for tok in market["tokens"][:2]:
            try:
                b = clob.get_book(tok["token_id"])
            except Exception:
                return None
            books[tok["token_id"]] = (
                clob._levels(b.get("bids"), "bid"),
                clob._levels(b.get("asks"), "ask"),
            )

        for tid, (b, a) in books.items():
            self.last_book[tid] = (b[0][0] if b else None, a[0][0] if a else None)

        # require a genuine two-sided book on both tokens: without a real bid
        # and ask the midpoint is noise and resting orders are a gift to takers
        if any(not (b and a) for b, a in books.values()):
            return None
        # refuse wide books: the mid is fiction and quoting mid-d puts us above
        # the real bid, so we fill and instantly mark a loss on liquidation
        cap = self.args.max_book_spread_cents / 100.0
        if any((a[0][0] - b[0][0]) > cap for b, a in books.values()):
            return None
        mids = {t: best_mid(*books[t]) for t in books}
        if any(v is None for v in mids.values()):
            return None

        metric = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "mode": self.mode,
            "condition_id": cond,
            "question": market["question"][:70],
            "daily_rate": market["daily_rate"],
            "mids": mids,
        }

        # only re-quote when the midpoint has moved enough: churning every loop
        # just gives away queue priority (and fills) for no reason
        tol = self.args.reprice_cents / 100.0
        moved = any(
            abs(mids[t] - self.last_mid.get(t, -1)) > tol for t in mids
        )
        if not moved and self.orders.get(cond):
            metric["repriced"] = False
            return metric
        self._cancel(cond)

        skew_k = self.args.inventory_skew
        max_inv = self.cfg["risk"]["max_inventory_per_token"]
        comp = 0.0
        our = 0.0
        for tok in market["tokens"][:2]:
            tid = tok["token_id"]
            mid = mids[tid]
            held = self.broker.positions.get(tid, 0.0)
            # inventory skew: shift quotes down when long, up when short
            skew = skew_k * d * (held / max_inv) if max_inv else 0.0
            qmid = mid - skew
            bids, asks = books[tid]
            best_bid, best_ask = bids[0][0], asks[0][0]
            comp += sum(rewards.book_scores(bids, asks, mid, ms, min_size))
            band = ms / 100.0

            # never bid above the real bid; stay inside the reward band
            bid_px = min(round(qmid - d, 3), round(best_bid - tick, 3))
            if 0 < bid_px < 1 and bid_px >= qmid - band:
                self._quote(cond, tid, "buy", bid_px, size)
                our += rewards.our_score(size, bid_px, mid, ms)
            if held > 0:
                ask_px = max(round(qmid + d, 3), round(best_ask + tick, 3))
                if 0 < ask_px < 1 and ask_px <= qmid + band:
                    self._quote(cond, tid, "sell", ask_px, min(size, held))
                    our += rewards.our_score(min(size, held), ask_px, mid, ms)

        for t in mids:
            self.last_mid[t] = mids[t]
        metric["repriced"] = True
        metric["our_score"] = round(our, 1)
        metric["competition_score"] = round(comp, 1)
        metric["est_share"] = round(rewards.estimate_share(our, comp), 4)
        metric["est_daily_usd"] = round(rewards.estimate_share(our, comp) * market["daily_rate"], 3)
        metric["quote_size"] = size
        metric["distance_cents"] = round(d * 100, 3)
        return metric

    def _est_daily(self, market):
        """Cheap single-book estimate of $/day if we rest a quote here."""
        tok = market["tokens"][0]
        try:
            b = clob.get_book(tok["token_id"])
        except Exception:
            return 0.0
        bids = clob._levels(b.get("bids"), "bid")
        asks = clob._levels(b.get("asks"), "ask")
        mid = best_mid(bids, asks)
        if mid is None or not bids or not asks:
            return 0.0
        ms = market["max_spread_cents"]
        min_size = max(market["min_size"], market["min_order_size"])
        size = min_size * self.args.size_mult
        d = self.args.spread_frac * ms / 100.0
        bid = min(round(mid - d, 3), round(mid - market["tick"], 3))
        if bid <= 0 or bid >= 1:
            return 0.0
        our = rewards.our_score(size, bid, mid, ms)
        comp = sum(rewards.book_scores(bids, asks, mid, ms, min_size))
        return rewards.estimate_share(our, comp) * market["daily_rate"]

    def select_markets(self):
        """Scan the cached full reward universe (two-stage) and rank by est $/day."""
        cfg = self.cfg
        uni = rewards_api.sampling_universe(ttl=self.args.universe_ttl, verbose=True)
        picked = selector.select(
            uni,
            min_pool=cfg["markets"]["min_daily_rate"],
            min_hours=cfg["markets"].get("min_hours_to_end", 6),
            shortlist=self.args.shortlist,
            max_markets=cfg["markets"]["max_markets"],
            size_mult=self.args.size_mult,
            spread_frac=self.args.spread_frac,
            workers=self.args.scan_workers,
            spread_cap_cents=self.args.max_book_spread_cents,
            log=print,
        )
        picked = [m for m in picked if m["est_daily_usd"] >= self.args.min_est_daily]
        print(f"selected {len(picked)} markets (min est ${self.args.min_est_daily}/day)")
        for m in picked[:15]:
            print(f"  ~${m['est_daily_usd']:>6.2f}/day  pool=${m['daily_rate']:>6.1f} "
                  f"share={m['share']:.3f} depth={m['depth']:>6.0f}  {m['question'][:44]}")
        return picked

    def _legacy_select_markets(self):
        cfg = self.cfg
        cands = rewards.reward_markets(
            min_daily_rate=cfg["markets"]["min_daily_rate"],
            limit=self.args.scan_limit,
        )
        # skip markets resolving soon: prices move fast and resting orders get picked off
        min_hours = cfg["markets"].get("min_hours_to_end", 6)
        if min_hours:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            kept = []
            for m in cands:
                ed = m.get("end_date")
                if ed:
                    try:
                        dt = datetime.fromisoformat(str(ed).replace("Z", "+00:00"))
                        if (dt - now).total_seconds() < min_hours * 3600:
                            continue
                    except ValueError:
                        pass
                kept.append(m)
            cands = kept
        print(f"scanning {len(cands)} rewards markets for competition...")
        with ThreadPoolExecutor(max_workers=self.args.scan_workers) as ex:
            ests = list(ex.map(self._est_daily, cands))
        ranked = sorted(zip(ests, cands), key=lambda x: x[0], reverse=True)
        picked = [(e, m) for e, m in ranked if e >= self.args.min_est_daily]
        picked = picked[: cfg["markets"]["max_markets"]]
        print(f"selected {len(picked)} markets (min est ${self.args.min_est_daily}/day)")
        for e, m in picked:
            print(f"  ~${e:>6.2f}/day  pool=${m['daily_rate']:>6.1f}  "
                  f"minSize={m['min_size']:>5.0f} maxSpread={m['max_spread_cents']:.1f}c  "
                  f"{m['question'][:48]}")
        return [m for _, m in picked]

    def loop(self):
        print(f"Polymarket MM bot | mode={self.mode} | paper-safe unless --live "
              f"| bankroll ${self.bankroll:.2f}")
        markets = self.select_markets()
        self.selected = markets
        if self.args.sizing_report:
            self._sizing_report(markets)
            return

        it = 0
        last_ts = time.time()
        while True:
            it += 1
            if (self.args.reselect_min
                    and (time.time() - self._last_select) / 60.0 >= self.args.reselect_min):
                print(f"\n-- reselecting markets ({self.args.reselect_min} min) --")
                markets = self.select_markets()
                self.selected = markets
                self._last_select = time.time()
            total_est = 0.0
            repriced = 0
            for m in markets:
                try:
                    metric = self.run_market(m)
                except Exception as e:
                    print(f"  ! {m['question'][:40]}: {type(e).__name__} {str(e)[:50]}")
                    continue
                if metric:
                    est = metric.get("est_daily_usd")
                    if est is None:
                        est = self.reward_estimate.get(m["condition_id"], 0.0)
                    else:
                        self.reward_estimate[m["condition_id"]] = est
                    total_est += est
                    if metric.get("repriced"):
                        repriced += 1
                        log(self.args.log_path, metric)
                        print(f"  {metric['question'][:42]:<42} share={metric.get('est_share', 0):.3f} "
                              f"est=${est:.2f}/day")
            if self.mode == "paper":
                self._paper_fills(markets)
                self._log_new_fills()
                self._update_adverse()
            else:
                self._read_live_rewards()

            now = time.time()
            self.reward_accrued += total_est * max(now - last_ts, 0) / 86400.0
            last_ts = now

            state = stats.build_state(
                self.broker, markets, self.mode, self.bankroll, self.start_time,
                total_est, self.reward_accrued, self.last_mid, markets,
                last_book=self.last_book, avg_cost=self.avg_cost,
                realized=self.realized, adverse=self.adverse,
                buys=self.buys, sells=self.sells,
                earnings_real=self.earnings_real, scoring=self.scoring,
            )
            stats.write(os.path.dirname(self.args.log_path) or ".", state,
                        timeseries_path=self.args.timeseries_path)
            log(self.args.timeseries_path, {
                "ts": state["updated"], "uptime_min": state["uptime_min"],
                "pnl_mid": state["pnl"], "pnl_liq": state["pnl_conservative"],
                "realized": state["realized"], "unrealized": state["unrealized"],
                "rewards_accrued": state["est_rewards_accrued"],
                "fills": state["fills"], "buys": state["buys"], "sells": state["sells"],
                "adverse_mean": state["adverse_mean"], "open_orders": state["open_orders"],
            })
            print(f"[iter {it}] est_rewards=${total_est:.2f}/day repriced={repriced} "
                  f"open={state['open_orders']} fills={state['buys']}B/{state['sells']}S "
                  f"pnl_mid=${state['pnl']:+.2f} pnl_liq=${state['pnl_conservative']:+.2f} "
                  f"rewards=${state['est_rewards_accrued']:.2f} adv={state['adverse_mean']:+.4f}")
            if self.args.once or (self.args.iterations and it >= self.args.iterations):
                break
            time.sleep(self.args.refresh)

    def _read_live_rewards(self):
        """Live: are our orders scoring, and what have we actually earned?"""
        oids = [o for lst in self.orders.values() for o in lst][:25]
        if oids:
            try:
                sc = rewards_api.scoring_orders(self.broker.client, oids)
                ok = sum(1 for v in sc.values() if v)
                self.scoring = f"{ok}/{len(oids)}"
            except Exception:
                pass
        try:
            self.earnings_real = rewards_api.earnings(self.broker.client)
        except Exception:
            pass

    def _sizing_report(self, markets):
        """Show per-order size/notional for several bankrolls, bounded by depth."""
        books = {}
        for m in markets:
            for tok in m["tokens"][:2]:
                try:
                    b = clob.get_book(tok["token_id"])
                except Exception:
                    continue
                books[tok["token_id"]] = (
                    clob._levels(b.get("bids"), "bid"),
                    clob._levels(b.get("asks"), "ask"),
                )
        for bankroll in (self.bankroll, 1_000.0, 1_000_000.0):
            budget, rows = sizing.plan(
                bankroll, markets, books, util=self.args.util,
                size_mult=self.args.size_mult,
            )
            deployed = sum(r["notional"] for r in rows)
            print(f"\n=== bankroll ${bankroll:,.0f} | per-order budget ${budget:,.0f} "
                  f"| deployed ${deployed:,.0f} ({len(rows)} orders) ===")
            print(f"{'market':<40}{'out':<7}{'mid':>6}{'depth':>9}{'min':>6}"
                  f"{'size':>9}{'notional':>10}  binds")
            for r in rows:
                print(f"{r['market']:<40}{str(r['outcome'])[:6]:<7}{r['mid']:>6.3f}"
                      f"{r['book_depth']:>9.0f}{r['min_size']:>6.0f}{r['size']:>9.0f}"
                      f"{r['notional']:>10.0f}  {r['binding']}")

    def _log_new_fills(self):
        fills = getattr(self.broker, "fills", []) or []
        for f in fills[self._n_fills_logged:]:
            log(self.args.log_path, {"type": "fill", **f})
            tok = f["token_id"]
            side = f["side"]
            px = float(f["price"])
            sz = float(f["size"])
            pos = self.broker.positions.get(tok, 0.0)
            if side == "buy":
                old = self.avg_cost.get(tok, 0.0)
                self.avg_cost[tok] = (old * max(pos - sz, 0.0) + px * sz) / pos if pos > 0 else px
                self.buys += 1
            else:
                self.realized += (px - self.avg_cost.get(tok, 0.0)) * sz
                self.sells += 1
            self.pending_fills.append((tok, side, px, time.time()))
        self._n_fills_logged = len(fills)

    def _update_adverse(self, window=900.0):
        """Measure price drift 15 min after each fill: the real MM cost."""
        now = time.time()
        keep = []
        for tok, side, px, ts in self.pending_fills:
            if now - ts < window:
                keep.append((tok, side, px, ts))
                continue
            mid = self.last_mid.get(tok)
            if mid is None:
                continue
            signed = 1.0 if side == "buy" else -1.0
            self.adverse.append(signed * (mid - px))
        self.pending_fills = keep

        if self.mode == "live":
            self.broker.cancel_all()
        print("snapshot:", json.dumps(self.broker.snapshot(), default=str)[:400])

    def _paper_fills(self, markets):
        """Fill against the real trade tape: a trade that crosses our quote hits it."""
        for m in markets:
            cond = m["condition_id"]
            try:
                trades = requests.get(
                    f"{DATA_API}/trades", params={"market": cond, "limit": 300}, timeout=15
                ).json()
            except Exception:
                continue
            for t in sorted(trades, key=lambda x: x.get("timestamp") or 0):
                tok = str(t.get("asset"))
                ts = float(t.get("timestamp") or 0)
                if ts <= self.last_trade_ts.get(tok, 0):
                    continue
                try:
                    self.broker.on_trade(tok, t.get("side"), float(t["price"]))
                except (TypeError, ValueError):
                    pass
                self.last_trade_ts[tok] = ts

    def mark_to_market(self, markets):
        val = self.broker.cash
        for m in markets:
            for tok in m["tokens"][:2]:
                tid = tok["token_id"]
                pos = self.broker.positions.get(tid, 0.0)
                val += pos * self.last_mid.get(tid, 0.0)
        return val


def main():
    ap = argparse.ArgumentParser(description="Polymarket liquidity-rewards market maker.")
    ap.add_argument("--config", default="config_mm.yaml")
    ap.add_argument("--live", action="store_true", help="place REAL orders (needs creds)")
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    ap.add_argument("--iterations", type=int, default=0, help="0 = run forever")
    ap.add_argument("--max-markets", type=int, default=None)
    ap.add_argument("--min-daily-rate", type=float, default=None)
    ap.add_argument("--spread-frac", type=float, default=None,
                    help="quote distance as a fraction of max_spread (0.5 = halfway)")
    ap.add_argument("--size-mult", type=float, default=None,
                    help="quote size = min_size * multiplier")
    ap.add_argument("--refresh", type=int, default=None, help="seconds between refreshes")
    ap.add_argument("--scan-limit", type=int, default=250,
                    help="(legacy selector) how many markets to scan")
    ap.add_argument("--shortlist", type=int, default=600,
                    help="biggest-pool markets to book-scan each selection")
    ap.add_argument("--universe-ttl", type=int, default=1800,
                    help="seconds to cache the ~17k reward-market universe")
    ap.add_argument("--reselect-min", type=int, default=30,
                    help="minutes between re-selections (0 = never)")
    ap.add_argument("--scan-workers", type=int, default=20)
    ap.add_argument("--min-est-daily", type=float, default=1.0,
                    help="skip markets whose estimated reward is below this $/day")
    ap.add_argument("--max-book-spread-cents", type=float, default=5.0,
                    help="skip markets whose book is wider than this (mid is fiction)")
    ap.add_argument("--reprice-cents", type=float, default=None,
                    help="only re-quote when mid moves this many cents")
    ap.add_argument("--inventory-skew", type=float, default=None,
                    help="shift quotes against inventory (0=off, 1=strong)")
    ap.add_argument("--bankroll", type=float, default=None,
                    help="starting paper capital shown on the stats page")
    ap.add_argument("--serve-stats", type=int, default=0, metavar="PORT",
                    help="serve the stats page on 127.0.0.1:PORT (access via ssh -L)")
    ap.add_argument("--sizing-report", action="store_true",
                    help="print per-trade sizing for $1k/$1M and exit")
    ap.add_argument("--check", action="store_true",
                    help="live preflight: auth, balance, allowance, min order cost, then exit")
    ap.add_argument("--util", type=float, default=0.8, help="fraction of bankroll deployed")
    ap.add_argument("--timeseries-path", default="research/mm_timeseries.jsonl")
    ap.add_argument("--log-path", default=None)
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    cfg["markets"]["max_markets"] = args.max_markets or cfg["markets"]["max_markets"]
    cfg["markets"]["min_daily_rate"] = (
        args.min_daily_rate if args.min_daily_rate is not None else cfg["markets"]["min_daily_rate"]
    )
    args.size_mult = args.size_mult if args.size_mult is not None else cfg["markets"]["quote_size_mult"]
    args.spread_frac = args.spread_frac if args.spread_frac is not None else cfg["markets"]["spread_frac"]
    args.refresh = args.refresh or cfg["markets"]["refresh_seconds"]
    args.reprice_cents = (
        args.reprice_cents if args.reprice_cents is not None
        else cfg["markets"].get("reprice_cents", 0.5)
    )
    args.inventory_skew = (
        args.inventory_skew if args.inventory_skew is not None
        else cfg["markets"].get("inventory_skew", 0.5)
    )
    args.log_path = args.log_path or cfg["log_path"]
    if args.bankroll is not None:
        cfg["bankroll"] = args.bankroll

    if (args.live or args.check) and not os.environ.get("POLYMARKET_PRIVATE_KEY"):
        raise SystemExit("--live/--check requires POLYMARKET_PRIVATE_KEY in the environment")

    if args.check:
        _preflight(cfg, args)
        return

    if args.serve_stats:
        _serve_stats(os.path.dirname(args.log_path) or ".", args.serve_stats)

    MarketMaker(cfg, args).loop()


def _preflight(cfg, args):
    """Live readiness: creds, balance, allowance, and the capital each market needs."""
    from src.pm.execution import ClobBroker
    from src.pm import selector, rewards_api

    print("== live preflight ==")
    try:
        broker = ClobBroker()
    except Exception as e:
        raise SystemExit(f"auth failed: {type(e).__name__}: {e}")
    info = broker.preflight()
    print(f"wallet address : {info['address']}")
    print(f"balance/allow  : {json.dumps(info['balance_allowance'])[:300]}")
    print(f"existing orders: {info['open_orders']}")

    uni = rewards_api.sampling_universe(ttl=args.universe_ttl, verbose=True)
    picked = selector.select(
        uni, min_pool=cfg["markets"]["min_daily_rate"],
        min_hours=cfg["markets"].get("min_hours_to_end", 6),
        shortlist=min(args.shortlist, 200),
        max_markets=10, size_mult=args.size_mult, spread_frac=args.spread_frac,
        workers=args.scan_workers, spread_cap_cents=args.max_book_spread_cents,
    )
    print("\ncheapest markets this bot would quote (both sides required):")
    need = []
    for m in picked:
        per_side = m["min_size"] * m["mid"]
        need.append((per_side, m))
        print(f"  ${per_side:>6.2f}/side (${per_side*2:>6.2f} both)  pool ${m['daily_rate']:>5.0f}  "
              f"est ${m['est_daily_usd']:>6.2f}/day  {m['question'][:42]}")
    if need:
        cheapest = min(n for n, _ in need)
        print(f"\nMINIMUM WORKING CAPITAL (1 market, both sides): ~${cheapest*2:.2f} "
              f"(+ buffer for fills/exits)")


def _serve_stats(directory, port):
    import http.server
    import threading

    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=directory, **k)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"stats page: http://127.0.0.1:{port}/stats.html  "
          f"(tunnel with: ssh -L {port}:127.0.0.1:{port} <user>@<host>)")


if __name__ == "__main__":
    main()
