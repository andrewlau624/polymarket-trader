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

from src.pm import clob, rewards
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

        # require a genuine two-sided book on both tokens: without a real bid
        # and ask the midpoint is noise and resting orders are a gift to takers
        if any(not (b and a) for b, a in books.values()):
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
            comp += sum(rewards.book_scores(bids, asks, mid, ms, min_size))

            bid_px = round(qmid - d, 3)
            bid_px = min(bid_px, round(qmid - tick, 3))
            if 0 < bid_px < 1:
                self._quote(cond, tid, "buy", bid_px, size)
                our += rewards.our_score(size, bid_px, mid, ms)
            if held > 0:
                ask_px = round(qmid + d, 3)
                ask_px = max(ask_px, round(qmid + tick, 3))
                if 0 < ask_px < 1:
                    self._quote(cond, tid, "sell", ask_px, min(size, held))
                    our += rewards.our_score(min(size, held), ask_px, mid, ms)

        for t in mids:
            self.last_mid[t] = mids[t]
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
        print(f"Polymarket MM bot | mode={self.mode} | paper-safe unless --live")
        markets = self.select_markets()

        it = 0
        while True:
            it += 1
            total_est = 0.0
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
                    log(self.args.log_path, metric)
                    if "est_share" in metric:
                        print(f"  {metric['question'][:42]:<42} share={metric['est_share']:.3f} "
                              f"est=${est:.2f}/day")
            if self.mode == "paper":
                self._paper_fills(markets)
            pnl = self.mark_to_market(markets) if self.mode == "paper" else 0.0
            print(f"[iter {it}] est_rewards = ${total_est:.2f}/day | "
                  f"open={self.broker.snapshot().get('n_open')} "
                  f"positions={len(self.broker.positions)} "
                  f"paper_pnl={pnl:+.2f}")
            if self.args.once or (self.args.iterations and it >= self.args.iterations):
                break
            time.sleep(self.args.refresh)

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
                    help="how many reward markets to scan for competition")
    ap.add_argument("--scan-workers", type=int, default=16)
    ap.add_argument("--min-est-daily", type=float, default=1.0,
                    help="skip markets whose estimated reward is below this $/day")
    ap.add_argument("--reprice-cents", type=float, default=None,
                    help="only re-quote when mid moves this many cents")
    ap.add_argument("--inventory-skew", type=float, default=None,
                    help="shift quotes against inventory (0=off, 1=strong)")
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

    if args.live and not os.environ.get("POLYMARKET_PRIVATE_KEY"):
        raise SystemExit("--live requires POLYMARKET_PRIVATE_KEY in the environment")

    MarketMaker(cfg, args).loop()


if __name__ == "__main__":
    main()
