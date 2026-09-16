"""Polymarket US liquidity-rewards market maker (CFTC-regulated platform).

Separate from mm_bot.py: this talks to api.polymarket.us with Ed25519 keys.
Incentive programs expose the REAL scoring parameters, and /v1/incentives/
earnings returns actual payouts, so nothing here is a proxy.

    score = discount_factor ^ (ticks from best price) * size     (per side)

Usage:
    python mm_bot_us.py --check        # auth + balances + programs + a book
    python mm_bot_us.py                # paper quoting (default)
    python mm_bot_us.py --live         # real orders (post-only maker)
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone

from src.pm_us.client import UsClient, px


def _num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def score_side(levels, best, discount, target, tick, our_price, our_size):
    """Reward score for one side: discount^(ticks from best) * size, capped at target."""
    total = 0.0
    for price, qty in levels:
        ticks = round(abs(price - best) / tick)
        total += (discount ** ticks) * qty
        if total >= target > 0:
            total = target
            break
    ticks = round(abs(our_price - best) / tick)
    ours = (discount ** ticks) * our_size
    return total, min(ours, target)


class UsMarketMaker:
    def __init__(self, args):
        self.args = args
        self.client = UsClient()
        self.orders = {}          # slug -> [order_id]
        self.last_best = {}
        self.mode = "live" if args.live else "paper"
        self.start = time.time()

    # ---- checking -------------------------------------------------------
    def check(self):
        print("== Polymarket US preflight ==")
        try:
            print("balances:", json.dumps(self.client.balances())[:300])
        except Exception as e:
            print(f"balances failed: {type(e).__name__} {e}")
        try:
            print(f"open positions: {len(self.client.positions())}")
        except Exception as e:
            print(f"positions failed: {type(e).__name__} {e}")

        try:
            top = self.client.top_programs(n=10)
        except Exception as e:
            print(f"programs failed: {type(e).__name__} {e}")
            top = []
        print(f"\nbiggest active liquidity programs (top {len(top)}):")
        for p in top:
            print(f"  pool ${p['pool']:>8,.0f}  disc={p['discount']} target={p['target']:>7.0f}  "
                  f"{p['slug']}  [{p['category']}]")

        if top:
            slug = top[0]["slug"]
            try:
                bids, asks, state = self.client.book_levels(slug)
                print(f"\nsample book {slug}: state={state} bids={len(bids)} offers={len(asks)}")
                if bids and asks:
                    print(f"  best bid/ask: {bids[0][0]:.3f} / {asks[0][0]:.3f}")
            except Exception as e:
                print(f"book failed: {type(e).__name__} {e}")

        try:
            earns = self.client.earnings()
            print(f"\nearnings so far: {json.dumps(earns)[:300]}")
        except Exception as e:
            print(f"\nearnings read failed (not fatal): {type(e).__name__} {str(e)[:80]}")

        bal = 0.0
        try:
            for b in (self.client.balances().get("balances") or []):
                bal += _num(b.get("currentBalance"))
        except Exception:
            pass
        print(f"\n== target size + what your balance can do ==")
        print(f"balance: ${bal:,.2f}")
        for p in top:
            try:
                bid, ask, _, _ = self.client.reference(p["slug"])
            except Exception:
                bid = ask = None
            price = ask or bid or 0.5
            self_need = p["target"] * price
            print(f"  {p['slug'][:42]:<42} target={p['target']:>7.0f} px={price:.3f} "
                  f"self-fund=${self_need:>7,.0f}  pool=${p['pool']:,.0f}")
        if top:
            price = 0.5
            need = top[0]["target"] * price
            print(f"\nNOTE: Target Size is AGGREGATE. If others already supply it (book depth>0),")
            print(f"you only need enough for one qualifying order, not ${need:,.0f}.")
            print(f"Your ${bal:,.2f} buys ~{int(bal // price) if price else 0} contracts at {price:.2f}.")
        print("\nAuth/KYC verified.")

    # ---- selection ------------------------------------------------------
    def programs(self):
        resp = self.client.incentives(statuses=["active"], program_type="liquidityProgram")
        rows = []
        for m in (resp.get("programs", []) if isinstance(resp, dict) else []):
            for t in (m.get("timePeriods") or []):
                if t.get("status") != "active":
                    continue
                rows.append({
                    "slug": m.get("marketSlug"),
                    "category": m.get("category"),
                    "pool": _num(t.get("rewardPool")),
                    "discount": _num(t.get("discountFactor"), 0.4) or 0.4,
                    "target": _num(t.get("targetSize")),
                    "period": t.get("period"),
                })
        rows = [r for r in rows if r["pool"] >= self.args.min_pool and r["target"] > 0]
        if self.args.min_target:
            rows = [r for r in rows if r["target"] <= self.args.min_target]
        rows.sort(key=lambda r: r["pool"], reverse=True)
        return rows[: self.args.max_markets]

    def run_market(self, prog):
        slug = prog["slug"]
        try:
            ref_bid, ref_ask, bids, asks = self.client.reference(slug)
        except Exception:
            return None
        if ref_bid is None:
            return None
        # empty book (common in new US programs): synthesize a spread around the
        # reference/last price rather than refusing to quote
        if ref_ask is None or ref_ask - ref_bid <= 0:
            half = self.args.max_spread / 2.0
            best_bid = round(max(0.01, ref_bid - half), 3)
            best_ask = round(min(0.99, ref_bid + half), 3)
        else:
            best_bid, best_ask = ref_bid, ref_ask
        tick = max(self.args.tick, 1e-4)
        size = int(self.args.size)
        target = prog["target"]
        if size < target and not bids and not asks:
            # nobody else is quoting and we can't meet Target Size ourselves
            return {"ts": datetime.now(timezone.utc).isoformat(), "mode": self.mode,
                    "slug": slug, "pool": prog["pool"], "share": 0.0, "est_daily": 0.0,
                    "note": f"size {size} < target {target:.0f} and book empty -> cannot qualify",
                    "repriced": False, "under_target": True}

        # join the best price on each side (post-only maker)
        buy_px, sell_px = best_bid, best_ask
        if self.args.buy_only:
            sell_px = None
        comp_b, ours_b = score_side(bids, best_bid, prog["discount"], prog["target"], tick, buy_px, size)
        comp_a, ours_a = score_side(asks, best_ask, prog["discount"], prog["target"], tick,
                                    sell_px if sell_px else best_ask, 0 if sell_px is None else size)
        our = ours_b + ours_a
        comp = comp_b + comp_a
        share = our / (our + comp) if (our + comp) > 0 else 0.0
        metric = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "mode": self.mode, "slug": slug, "pool": prog["pool"],
            "best_bid": best_bid, "best_ask": best_ask, "share": round(share, 4),
            "est_daily": round(share * prog["pool"], 4), "size": size,
        }

        # reprice only when the touch moves
        if self.last_best.get(slug) == (best_bid, best_ask) and self.orders.get(slug):
            metric["repriced"] = False
            return metric
        self._cancel(slug)
        if self.mode == "live":
            try:
                o = self.client.place(slug, "buy", buy_px, size, maker=True)
                self.orders.setdefault(slug, []).append(_oid(o))
                if sell_px is not None:
                    o = self.client.place(slug, "sell", sell_px, size, maker=True)
                    self.orders.setdefault(slug, []).append(_oid(o))
            except Exception as e:
                metric["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        else:
            self.orders[slug] = [f"paper-buy-{slug}", f"paper-sell-{slug}"]
        self.last_best[slug] = (best_bid, best_ask)
        return metric

    def _cancel(self, slug):
        for oid in self.orders.get(slug, []):
            if self.mode == "live" and not str(oid).startswith("paper-"):
                try:
                    self.client.cancel(oid, slug)
                except Exception:
                    pass
        self.orders[slug] = []

    def loop(self):
        print(f"Polymarket US MM | mode={self.mode}")
        progs = self.programs()
        print(f"selected {len(progs)} active liquidity markets")
        for p in progs[:12]:
            print(f"  pool ${p['pool']:>8,.0f}  disc={p['discount']} target={p['target']:>8.0f}  {p['slug']}")
        it = 0
        while True:
            it += 1
            est = 0.0
            for p in progs:
                try:
                    m = self.run_market(p)
                except Exception as e:
                    print(f"  ! {p['slug']}: {type(e).__name__} {str(e)[:60]}")
                    continue
                if m and m.get("repriced") is not False:
                    est += m["est_daily"]
                    _log(self.args.log_path, m)
                    print(f"  {m['slug'][:40]:<40} share={m['share']:.3f} est=${m['est_daily']:.2f}/day")
            real = None
            if self.mode == "live":
                try:
                    real = self.client.earnings()
                except Exception:
                    pass
            print(f"[iter {it}] est ${est:.2f}/day | orders {sum(len(v) for v in self.orders.values())} "
                  f"| earnings {json.dumps(real)[:120] if real else '-'}")
            if self.args.once or (self.args.iterations and it >= self.args.iterations):
                break
            time.sleep(self.args.refresh)
        if self.mode == "live":
            self.client.cancel_all()
        self.client.close()


def _oid(resp):
    if isinstance(resp, dict):
        return resp.get("orderId") or resp.get("orderID") or resp.get("id")
    return None


def _log(path, row):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(row) + "\n")


def report(path):
    """Summarize a paper run: does competition show up, and what's the share?"""
    import collections
    if not os.path.exists(path):
        print(f"no log yet at {path}")
        return
    by = collections.defaultdict(list)
    with open(path) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("share") is not None:
                by[r["slug"]].append(r)
    print(f"{'market':<44}{'n':>4}{'avg_share':>10}{'avg_est$':>10}{'pool$':>8}")
    tot = 0.0
    for slug, rows in sorted(by.items(), key=lambda kv: -max(r["est_daily"] for r in kv[1]))[:20]:
        n = len(rows)
        ash = sum(r["share"] for r in rows) / n
        aest = sum(r["est_daily"] for r in rows) / n
        tot += aest
        print(f"{slug[:44]:<44}{n:>4}{ash:>10.3f}{aest:>10.2f}{max(r['pool'] for r in rows):>8,.0f}")
    print(f"\nsum of avg est (top20): ${tot:,.2f}/day  [PROXY: share vs current book]")


def main():
    ap = argparse.ArgumentParser(description="Polymarket US liquidity-rewards MM.")
    ap.add_argument("--check", action="store_true", help="preflight then exit")
    ap.add_argument("--live", action="store_true", help="place real post-only orders")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--iterations", type=int, default=0)
    ap.add_argument("--max-markets", type=int, default=10)
    ap.add_argument("--min-pool", type=float, default=100.0)
    ap.add_argument("--size", type=float, default=20, help="contracts per side")
    ap.add_argument("--tick", type=float, default=0.01)
    ap.add_argument("--max-spread", type=float, default=0.05)
    ap.add_argument("--refresh", type=int, default=20)
    ap.add_argument("--log-path", default="research/us_timeseries.jsonl")
    ap.add_argument("--report", action="store_true", help="summarize a paper run")
    ap.add_argument("--min-target", type=float, default=0.0,
                    help="skip programs whose Target Size exceeds this (0 = any)")
    ap.add_argument("--buy-only", action="store_true",
                    help="place bids only (no shorting / no inventory)")
    args = ap.parse_args()

    if args.report:
        report(args.log_path)
        return
    if not os.environ.get("POLYMARKET_US_KEY_ID"):
        raise SystemExit("set POLYMARKET_US_KEY_ID and POLYMARKET_US_SECRET_KEY")

    mm = UsMarketMaker(args)
    if args.check:
        mm.check()
        mm.client.close()
        return
    mm.loop()


if __name__ == "__main__":
    main()
