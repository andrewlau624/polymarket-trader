"""Polymarket US liquidity-rewards market maker (CFTC-regulated platform).

Separate from mm_bot.py: this talks to api.polymarket.us with Ed25519 keys.
Incentive programs expose the REAL scoring parameters, and /v1/incentives/
earnings returns actual payouts, so nothing here is a proxy.

    score = discount_factor ^ (ticks from best price) * size     (per side)

Usage:
    python mm_bot_us.py --check        # auth + balances + programs + a book
    python mm_bot_us.py                # paper quoting (default)
    python mm_bot_us.py --live         # real orders (post-only maker)
    python mm_bot_us.py --account      # cash, orders, positions, real rewards
    python mm_bot_us.py --flatten      # dry-run the sells that unwind inventory
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


# every money field on this API can arrive as a bare number, a numeric string,
# or an Amount object {"value": "9.01", "currency": "USD"}
def _amt(x):
    """Amount-aware float, or None when the field is absent/unparseable.

    Returns None rather than 0.0 so a key the API never sent renders as 'n/a'
    instead of a confident $0.00 (which is how 'in positions $0.00' got printed
    against $34 of real inventory).
    """
    if isinstance(x, dict):
        x = x.get("value")
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _money(v):
    return "n/a" if v is None else f"${v:,.2f}"


def _position_row(v):
    """(net shares, cost basis $, realized $) from one portfolio position."""
    if not isinstance(v, dict):
        return 0.0, 0.0, 0.0
    return (_amt(v.get("netPosition")) or 0.0,
            _amt(v.get("cost")) or 0.0,
            _amt(v.get("realized")) or 0.0)


# fields the balance line already explains; anything else gets dumped raw so
# the real schema is discoverable instead of guessed at
_KNOWN_BALANCE_KEYS = {"currentBalance", "buyingPower", "balanceReservation",
                       "assetNotional", "currency"}
# statuses that mean the reward actually landed. Anything else (SKIPPED,
# PENDING, ...) has not paid, so it is reported separately.
_PAID_STATUSES = {"PAID", "CREDITED", "COMPLETED", "SETTLED", "SUCCESS"}


def _hours_between(a, b):
    if not a or not b:
        return None
    try:
        from datetime import datetime
        d1 = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        d2 = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
        return (d2 - d1).total_seconds() / 3600.0
    except ValueError:
        return None


def _hours_left(iso):
    if not iso:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).total_seconds() / 3600.0
    except ValueError:
        return None


def cat_of(p):
    """Category label for a program, falling back to subcategory."""
    return (p.get("category") or p.get("subcategory") or "").strip()


def cat_matches(p, want):
    """Forgiving category match.

    An exact equality test on this field silently matched 0 of 2418 programs
    while the whole book was sports, because nobody had checked what the venue
    actually puts there. Substring, either direction, across category and
    subcategory.
    """
    if not want or want == "any":
        return True
    w = want.lower().strip()
    for v in (p.get("category"), p.get("subcategory")):
        v = (v or "").lower().strip()
        if v and (w in v or v in w):
            return True
    return False


def cat_labels(progs, limit=14):
    """'sports(1200), mma(80), …' - what the venue really returns."""
    from collections import Counter
    c = Counter(cat_of(p) or "(none)" for p in progs)
    return ", ".join(f"{k}({v})" for k, v in c.most_common(limit))


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
        self.pos = {}             # slug -> position row, refreshed per iteration
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
        allp = []
        try:
            allp = self.client.all_programs()
            from collections import Counter
            cnt = Counter((p["period"] or "?") for p in allp)
            pool = {}
            for p in allp:
                pool[p["period"] or "?"] = pool.get(p["period"] or "?", 0.0) + p["pool"]
            print("\n== every active liquidity program, by reward period ==")
            for k, c in cnt.most_common():
                print(f"  period {k:<10} markets/periods={c:>5}   total daily pool ${pool[k]:>10,.0f}")
            print("  (use --period <name> to target one)")
        except Exception as e:
            print(f"period summary failed: {type(e).__name__} {e}")

        # candidate probe: respects --period/--category/--min-pool and shows
        # whether each market has a real (quotable) book
        try:
            cand = [p for p in allp if p["period"] != "?"]
            if self.args.period and self.args.period != "any":
                cand = [p for p in cand if (p["period"] or "").lower() == self.args.period]
            if self.args.category and self.args.category != "any":
                cand = [p for p in cand if cat_matches(p, self.args.category)]
            cand = [p for p in cand if p["pool"] >= self.args.min_pool]
            if self.args.max_target:
                cand = [p for p in cand if p["target"] <= self.args.max_target]
            cand.sort(key=lambda r: r["pool"], reverse=True)
            cand = cand[:20]
            print(f"\n== candidates (period={self.args.period}, category={self.args.category}, "
                  f"min_pool=${self.args.min_pool:,.0f}) ==")
            if not cand:
                print("  none match those filters")
            for p in cand:
                try:
                    b, a, _ = self.client.book_levels(p["slug"])
                    depth = f"{len(b):>3}/{len(a):<3}"
                    ok = "quotable" if (b and a) else "EMPTY"
                except Exception as e:
                    depth, ok = "  ?/  ?", type(e).__name__
                print(f"  book {depth} {ok:<8} pool ${p['pool']:>7,.0f} target={p['target']:>7.0f} "
                      f"[{p['period']:<6}] {p['slug'][:40]}")
        except Exception as e:
            print(f"candidate probe failed: {type(e).__name__} {e}")

        print(f"\nbiggest active liquidity programs (top {len(top)}):")
        for p in top:
            hl = _hours_left(p.get("end"))
            dur = _hours_between(p.get("start"), p.get("end"))
            hl_s = f"{hl:5.1f}h" if hl is not None else "  now"
            dur_s = f"{dur/24:5.1f}d" if dur else "   ? "
            print(f"  pool ${p['pool']:>8,.0f} target={p['target']:>7.0f} "
                  f"[{p.get('period') or '?':<6} {dur_s} ends {hl_s}]  {p['slug'][:38]}")

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
                    "event_start": m.get("eventStartTime"),
                    "start": t.get("start"),
                    # ongoing programs omit 'end'; fall back to the event start
                    "end": t.get("end") or m.get("eventStartTime"),
                })
                rows[-1]["hours_left"] = _hours_left(rows[-1]["end"])
                rows[-1]["duration_hours"] = _hours_between(
                    rows[-1]["start"], rows[-1]["end"])
        rows = [r for r in rows if r["pool"] >= self.args.min_pool and r["target"] > 0]
        if self.args.max_target:
            # small-target programs are where a small order is a meaningful share
            rows = [r for r in rows if r["target"] <= self.args.max_target]
        if self.args.category and self.args.category != "any":
            rows = [r for r in rows if cat_matches(r, self.args.category)]
        if self.args.period and self.args.period != "any":
            rows = [r for r in rows if (r["period"] or "").lower() == self.args.period]
        if self.args.ending_within:
            # periods ending soon settle sooner -> faster payout signal
            rows = [r for r in rows
                    if r["hours_left"] is not None and 0 <= r["hours_left"] <= self.args.ending_within]
        rows.sort(key=lambda r: r["pool"], reverse=True)
        return rows[: self.args.scan]

    def _refresh_positions(self):
        """One positions call per loop iteration; sizing and exits both read it."""
        try:
            self.pos = self.client.positions() or {}
        except Exception:
            pass          # stale inventory is better than a dead iteration

    def _inventory(self, slug):
        """(net shares held, cost basis $) for one market."""
        net, cost, _ = _position_row(self.pos.get(slug))
        return net, cost

    def _size_for(self, price):
        if self.args.notional:
            return max(1, int(self.args.notional / max(price or 0.5, 0.01)))
        return int(self.args.size)

    def estimate(self, prog):
        """Share / est $/day for OUR size on this program (book-based)."""
        slug = prog["slug"]
        try:
            ref_bid, ref_ask, bids, asks = self.client.reference(slug)
        except Exception:
            return None
        if ref_bid is None:
            return None
        if ref_ask is None or ref_ask - ref_bid <= 0:
            half = self.args.max_spread / 2.0
            best_bid = round(max(0.01, ref_bid - half), 3)
            best_ask = round(min(0.99, ref_bid + half), 3)
        else:
            best_bid, best_ask = ref_bid, ref_ask
        tick = max(self.args.tick, 1e-4)
        size = self._size_for(best_bid)
        held, _held_cost = self._inventory(prog["slug"])
        sell_size = size if not self.args.buy_only else int(min(held, size))
        cb, ob = score_side(bids, best_bid, prog["discount"], prog["target"], tick, best_bid, size)
        ca, oa = score_side(asks, best_ask, prog["discount"], prog["target"], tick,
                            best_ask, sell_size)
        in_band = (self.args.min_price <= best_bid <= self.args.max_price
                   and self.args.min_price <= best_ask <= self.args.max_price)
        our, comp = ob + oa, cb + ca
        share = our / (our + comp) if (our + comp) > 0 else 0.0
        # the pool covers the WHOLE period, so your run-rate is pool/share spread
        # over the period length. A $3k pool over a 3h live window beats a $10k
        # pool over a 10-day early window ($24k/day vs $1k/day).
        dur_h = prog.get("duration_hours") or 24.0
        run_rate = share * prog["pool"] / max(dur_h / 24.0, 1e-6)
        # Target Size uses RAW size. If the book's total on a side is below it,
        # the side doesn't qualify and nobody scores -> skip those markets.
        raw_b = sum(q for _, q in bids)
        raw_a = sum(q for _, q in asks)
        target = prog["target"]
        meets = raw_b >= target and raw_a >= target
        # A book whose size sits far from the touch scores ~0 after the discount
        # factor, so a 20-lot at the touch appears to take ~100% of the pool.
        # That is an artifact of quoting a period that has not started yet, not
        # an opportunity: the makers arrive when the game does.
        comp_total = cb + ca
        speculative = comp_total < max(target * 0.01, 1.0)
        return {"best_bid": best_bid, "best_ask": best_ask, "bids": bids, "asks": asks,
                "share": share, "est_period": share * prog["pool"], "est_daily": run_rate,
                "raw_bid": raw_b, "raw_ask": raw_a, "meets_target": meets,
                "comp_score": comp_total, "speculative": speculative,
                "quotable": bool(bids and asks) and in_band}

    def account(self):
        """Plain-English view of your live account: cash, orders, positions, earnings."""
        c = self.client

        positions = {}
        try:
            positions = c.positions() or {}
        except Exception as e:
            print(f"positions failed: {type(e).__name__} {e}")
        cost_basis = sum(_position_row(v)[1] for v in positions.values())

        print("== ACCOUNT ==")
        reserved = None
        try:
            rows = c.balances().get("balances") or []
            if not rows:
                print("  (the API returned no balance rows)")
            for b in rows:
                cash = _amt(b.get("currentBalance"))
                bp = _amt(b.get("buyingPower"))
                reserved = _amt(b.get("balanceReservation"))
                print(f"  cash {_money(cash)}   buying power {_money(bp)}   "
                      f"reserved {_money(reserved)}")
                # Observed with zero open orders: cash, buyingPower and
                # balanceReservation all come back as the SAME number, so the
                # breakdown carries no information and 'free = cash - reserved'
                # would read $0.00 on an account that is entirely free.
                three = [v for v in (cash, bp, reserved) if v is not None]
                if len(three) == 3 and max(three) - min(three) <= 0.01:
                    print(f"  (the venue reports all three as the same figure - read "
                          f"it as {_money(cash)} cash; the split is not populated)")
                    reserved = None          # do not compare order collateral to it
                elif cash is not None and reserved is not None:
                    free = cash - reserved
                    print(f"  free (cash - reserved) {_money(free)}")
                    if bp is not None and abs(bp - free) > 0.01:
                        print(f"  ! the API's buyingPower {_money(bp)} disagrees with "
                              f"cash - reserved {_money(free)}")
                extra = sorted(set(b) - _KNOWN_BALANCE_KEYS)
                if extra:
                    print(f"  other balance fields: "
                          f"{json.dumps({k: b[k] for k in extra})[:240]}")
        except Exception as e:
            print(f"  balances failed: {type(e).__name__} {e}")
        # assetNotional has reported $0.00 against real inventory, so value the
        # book from the portfolio instead of trusting that field
        print(f"  in positions {_money(cost_basis)} at cost across "
              f"{len(positions)} market{'' if len(positions) == 1 else 's'}")

        print("\n== OPEN ORDERS ==")
        committed = 0.0
        try:
            orders = c.open_orders()
            if not orders:
                print("  none")
            for o in sorted(orders, key=lambda r: str(r.get("marketSlug") or "")):
                slug = o.get("marketSlug") or o.get("slug") or "?"
                side = str(o.get("intent") or o.get("side") or "?")
                if side.startswith("ORDER_INTENT_"):
                    side = side[len("ORDER_INTENT_"):]
                price = _amt(o.get("price")) or 0.0
                orig = _amt(o.get("quantity")) or 0.0
                # a partial fill leaves 'quantity' at the original size, so the
                # working size is remainingQuantity when the venue sends it
                left = _amt(o.get("remainingQuantity"))
                left = orig if left is None else left
                filled = max(orig - left, 0.0)
                oid = str(o.get("orderId") or o.get("id") or "")[:10]
                if side.startswith("BUY"):
                    committed += left * price
                fill_s = f"{filled:.0f} filled" if filled > 0 else ""
                print(f"  {side:<10} {left:>6.0f} @ {price:<5.2f} = ${left * price:>6.2f}  "
                      f"{fill_s:<10} {slug[:38]:<38} {oid}")
            print(f"  total: {len(orders)} orders, ${committed:,.2f} of buy-side collateral")
            if reserved is not None and committed > reserved + 0.01:
                print(f"  ! these bids need ${committed:,.2f} but the venue only reserved "
                      f"{_money(reserved)} -")
                print(f"    some rows are stale or already partly filled")
        except Exception as e:
            print(f"  orders failed: {type(e).__name__} {e}")

        print("\n== POSITIONS ==")
        if not positions:
            print("  none (nothing filled yet)")
        else:
            print(f"  {'market':<38} {'net':>6} {'cost':>9} {'avg':>7} {'realized':>9}")
            for k, v in sorted(positions.items()):
                net, cost, realized = _position_row(v)
                avg = cost / net if net else 0.0
                print(f"  {k[:38]:<38} {net:>6.0f} {'$%.2f' % cost:>9} "
                      f"{avg:>7.3f} {'$%.2f' % realized:>9}")
            print(f"  {'TOTAL':<38} {'':>6} {'$%.2f' % cost_basis:>9}")

        print("\n== REWARDS EARNED (real) ==")
        try:
            data = c.earnings()
            rows = data.get("rewards") if isinstance(data, dict) else None
            if rows is None:
                print(f"  unrecognized shape: {json.dumps(data)[:240]}")
            elif not rows:
                print("  none yet")
            else:
                by = {}
                for r in rows:
                    st = str(r.get("status") or "?")
                    n, tot = by.get(st, (0, 0.0))
                    by[st] = (n + 1, tot + (_amt(r.get("reward")) or 0.0))
                for st, (n, tot) in sorted(by.items(), key=lambda kv: -kv[1][1]):
                    mark = "" if st.upper() in _PAID_STATUSES else "   (not credited)"
                    print(f"  {st:<12} {n:>4} rewards  ${tot:>9.4f}{mark}")
                total = sum(t for _, t in by.values())
                paid = sum(t for st, (_, t) in by.items() if st.upper() in _PAID_STATUSES)
                print(f"  {'TOTAL':<12} {len(rows):>4} rewards  ${total:>9.4f}"
                      f"   credited ${paid:,.4f}")
        except Exception as e:
            print(f"  earnings failed: {type(e).__name__} {e}")
        print("\nNOTE: rewards land after the period ends (<=5 business days) + <=2 to credit.")

    def _reference_retry(self, slug, attempts=4):
        """reference() with backoff.

        A single flaky read used to skip a position silently, which is how a
        flatten reported different skips on two consecutive runs and left a
        naked short open. Rate limiting is normal here; giving up after one
        try is not.
        """
        for i in range(attempts):
            try:
                bid, ask, _b, _a = self.client.reference(slug)
                if bid is not None or ask is not None:
                    return bid, ask
            except Exception:
                pass
            if i < attempts - 1:
                time.sleep(1.0 * (2 ** i))
        return None, None

    def flatten(self):
        """Post maker sells for every open position, unwinding inventory.

        Without --live this is a dry run. Orders rest at the best ask, so they
        pay no spread; 'make cancel' (or a live bot start) pulls them.
        """
        try:
            positions = self.client.positions() or {}
        except Exception as e:
            print(f"positions failed: {type(e).__name__} {e}")
            return
        longs = {k: v for k, v in positions.items() if _position_row(v)[0] >= 1}
        shorts = {k: v for k, v in positions.items() if _position_row(v)[0] <= -1}
        if not longs and not shorts:
            print("no positions to flatten")
            return
        # Flatten must be IDEMPOTENT. It places resting sells, so re-running it
        # without clearing its own prior orders double-posts: two 6-share sells
        # against a 6-share position can fill twice and open a short, which is
        # precisely what this command exists to prevent. Told the operator to
        # re-run it, and that is exactly what happened.
        existing = {}
        try:
            for o in (self.client.open_orders() or []):
                sl = o.get("marketSlug") or o.get("slug")
                oid = o.get("orderId") or o.get("id")
                if sl and oid:
                    existing.setdefault(sl, []).append(oid)
        except Exception as e:
            print(f"! could not list open orders ({type(e).__name__}); a re-run "
                  f"may double-post. Run `make cancel` first to be safe.")
        if existing:
            n = sum(len(v) for v in existing.values())
            print(f"clearing {n} existing order(s) on {len(existing)} market(s) "
                  f"so this does not double-post…")
            if self.mode == "live":
                for sl, oids in existing.items():
                    for oid in oids:
                        try:
                            self.client.cancel(oid, sl)
                        except Exception as e:
                            print(f"  ! cancel {str(oid)[:10]} on {sl[:30]} failed: "
                                  f"{type(e).__name__}")

        print(f"== FLATTEN ({self.mode}) ==")
        proceeds = pnl_total = 0.0
        skipped = []
        for slug, v in sorted(longs.items()):
            net, cost, _ = _position_row(v)
            qty = int(net)
            bid, ask = self._reference_retry(slug)
            # Resting at the ask costs nothing but may never fill on a thin
            # book; crossing at the bid exits now and pays the spread. The right
            # answer is per position, not per run: on this account three markets
            # cost 0.9-1.6% to cross while a fourth cost 37.5%, so an
            # all-or-nothing flag would either strand $15 of capital or throw
            # $1.77 away. Cross where the spread is tight, rest where it is not.
            rel = ((ask - bid) / ask) if (bid and ask and ask > 0) else 1.0
            do_cross = self.args.cross and rel <= self.args.cross_max_pct
            if do_cross:
                price = bid if bid else ask
            else:
                price = ask if ask else (bid + self.args.tick if bid else None)
            if price is None:
                print(f"  {slug[:38]:<38} NO PRICE after retries - still open")
                skipped.append(slug)
                continue
            price = round(min(0.99, max(0.01, price)), 3)
            avg = cost / qty if qty else 0.0
            pnl = (price - avg) * qty
            proceeds += price * qty
            pnl_total += pnl
            # never offer more than is actually held
            qty = min(qty, int(net))
            spread_note = ""
            if bid is not None and ask is not None:
                give = (ask - bid) * qty
                if self.args.cross:
                    spread_note = (f" [{rel:.1%} spread -> "
                                   f"{'CROSS' if do_cross else 'REST'}"
                                   f", crossing would cost ${give:.2f}]")
                else:
                    spread_note = (f" [bid {bid:.3f}/ask {ask:.3f}; crossing "
                                   f"costs ${give:.2f}]")
            note = (f"{qty:>5} @ {price:.3f}  (avg {avg:.3f}, P&L ${pnl:+.2f})"
                    f"  {slug[:34]}{spread_note}")
            if self.mode != "live":
                print(f"  would sell {note}")
                continue
            try:
                self.client.place(slug, "sell", price, qty, maker=not do_cross)
                print(f"  sell       {note}")
            except Exception as e:
                print(f"  {slug[:38]:<38} sell failed: {type(e).__name__} {str(e)[:70]}")
        # shorts close by BUYING back. A naked short is the riskier leg to
        # leave open, so it crosses rather than resting.
        for slug, v in sorted(shorts.items()):
            net, cost, _ = _position_row(v)
            qty = int(abs(net))
            bid, ask = self._reference_retry(slug)
            price = ask if ask else (bid if bid else None)
            if price is None:
                print(f"  {slug[:38]:<38} NO PRICE after retries - STILL SHORT")
                skipped.append(slug)
                continue
            price = round(min(0.99, max(0.01, price)), 3)
            got = abs(cost) / qty if qty else 0.0
            pnl = (got - price) * qty
            note = (f"{qty:>5} @ {price:.3f}  (sold at {got:.3f}, P&L ${pnl:+.2f})"
                    f"  {slug[:38]}")
            if self.mode != "live":
                print(f"  would BUY BACK {note}")
                continue
            try:
                self.client.place(slug, "buy", price, qty, maker=False)
                print(f"  buy back  {note}")
            except Exception as e:
                print(f"  {slug[:38]:<38} buyback failed: {type(e).__name__} "
                      f"{str(e)[:70]}")
            pnl_total += pnl

        print(f"\n  proceeds if all fill: ${proceeds:,.2f}   P&L ${pnl_total:+,.2f}")
        if skipped:
            print(f"\n  !! {len(skipped)} POSITION(S) NOT FLATTENED - re-run to clear:")
            for sl in skipped:
                print(f"     {sl}")
            print("  A short left open here is a naked directional bet.")
        if self.mode != "live":
            print("  dry run - add --live (or use 'make flatten-live') to place these.")
        else:
            print("  Long sells REST at the ask; short buy-backs CROSS and should")
            print("  fill at once. Re-run `make account` to confirm what actually went.")


    def hunt(self):
        """Book-scan every program matching the filters; report quotable ones."""
        from concurrent.futures import ThreadPoolExecutor
        from collections import Counter

        allp = self.client.all_programs()
        cnt = Counter((p["period"] or "?") for p in allp)
        print(f"fetched {len(allp)} active program periods")
        print("period labels seen:   " + ", ".join(f"{k}({v})" for k, v in cnt.most_common()))
        print("category labels seen: " + cat_labels(allp))
        try:
            prog, tp = self.client.program_sample()
            if prog is not None:
                skip = {"timePeriods"}
                print("\n  program fields:     "
                      + json.dumps({k: v for k, v in prog.items() if k not in skip})[:320])
            if tp is not None:
                print("  timePeriod fields:  " + json.dumps(tp)[:320])
            print("  ^ anything here about MINIMUM SIZE or MAX SPREAD is a "
                  "qualification rule the bot does not yet read.")
        except Exception as e:
            print(f"  (program field dump failed: {type(e).__name__} {e})")

        cand = list(allp)
        if self.args.period and self.args.period != "any":
            cand = [p for p in cand if (p["period"] or "").lower() == self.args.period]
        if self.args.category and self.args.category != "any":
            cand = [p for p in cand if cat_matches(p, self.args.category)]
        cand = [p for p in cand if p["pool"] >= self.args.min_pool]
        if self.args.max_target:
            cand = [p for p in cand if p["target"] <= self.args.max_target]
        cand.sort(key=lambda r: r["pool"], reverse=True)
        cand = cand[: self.args.scan]
        print(f"book-scanning {len(cand)} of them (period={self.args.period}, "
              f"category={self.args.category}, min_pool=${self.args.min_pool:,.0f})...")

        with ThreadPoolExecutor(max_workers=6) as ex:
            ests = list(ex.map(self.estimate, cand))
        rows = [{**p, **e} for p, e in zip(cand, ests) if e and e.get("quotable")]
        rows.sort(key=lambda r: r["est_daily"], reverse=True)

        print(f"\nquotable markets: {len(rows)}")
        print(f"{'est$/day':>10}{'pool':>8}{'target':>9}{'share':>8}{'tgt?':>6}"
              f"{'real?':>7}  period  market")
        met = spec = 0
        for r in rows[:25]:
            ok = "OK" if r.get("meets_target") else "thin"
            met += 1 if r.get("meets_target") else 0
            sp = "GHOST" if r.get("speculative") else "-"
            spec += 1 if r.get("speculative") else 0
            print(f"{r['est_daily']:>10,.2f}{r['pool']:>8,.0f}{r['target']:>9.0f}"
                  f"{r['share']:>8.3f}{ok:>6}{sp:>7}  {r['period']:<7} {r['slug'][:36]}")
        print(f"\n  {met}/{len(rows)} meet Target Size (those are the only ones that can pay)")
        if spec:
            print(f"  {spec}/{len(rows)} are GHOST: scoring competition is ~0 because the")
            print(f"  book is empty NEAR THE TOUCH, usually a period that has not started.")
            print(f"  Their est$/day assumes you keep ~100% of the pool. You will not -")
            print(f"  the makers arrive when the event does. Do not size on those rows.")
        if not cand:
            print(f"  the FILTER matched nothing - no program has category "
                  f"~'{self.args.category}' / period '{self.args.period}'.")
            print(f"  categories the venue actually returns: {cat_labels(allp)}")
        elif not rows:
            print("  none — every matching market has an empty/one-sided book")
        return rows

    def select(self):
        """Rank candidates by est $/day for our size, not by pool size."""
        cands = self.programs()
        scored = []
        skipped = 0
        thin = 0
        for p in cands:
            e = self.estimate(p)
            if not e:
                continue
            if not e.get("quotable") and not self.args.include_empty:
                skipped += 1
                continue
            if not e.get("meets_target", True) and not self.args.include_thin:
                thin += 1
                continue
            scored.append({**p, **e})
        scored.sort(key=lambda r: r["est_daily"], reverse=True)
        if skipped:
            print(f"  (skipped {skipped} empty-book markets)")
        if thin:
            print(f"  (skipped {thin} markets whose book is below Target Size)")
        cap = self.args.max_per_period
        if cap:
            # spread across periods so a fast-settling one (daily/day_of) always
            # gets a slot instead of all of them going to the biggest pool
            out, counts = [], {}
            for r in scored:
                per = (r.get("period") or "?").lower()
                if counts.get(per, 0) >= cap:
                    continue
                counts[per] = counts.get(per, 0) + 1
                out.append(r)
                if len(out) >= self.args.max_markets:
                    break
            return out
        return scored[: self.args.max_markets]

    def run_market(self, prog):
        slug = prog["slug"]
        # reuse the quote selection just measured (avoids a second API call and
        # the rate limit that made the first iteration silently do nothing)
        if prog.get("best_bid") is not None and not self.orders.get(slug):
            ref_bid, ref_ask = prog["best_bid"], prog.get("best_ask")
            bids, asks = prog.get("bids") or [], prog.get("asks") or []
        else:
            try:
                ref_bid, ref_ask, bids, asks = self.client.reference(slug)
            except Exception as e:
                return {"ts": datetime.now(timezone.utc).isoformat(), "mode": self.mode,
                        "slug": slug, "pool": prog["pool"], "share": 0.0, "est_daily": 0.0,
                        "error": f"{type(e).__name__}: {str(e)[:100]}", "repriced": False}
        if ref_bid is None:
            return {"ts": datetime.now(timezone.utc).isoformat(), "mode": self.mode,
                    "slug": slug, "pool": prog["pool"], "share": 0.0, "est_daily": 0.0,
                    "error": "no reference price", "repriced": False}
        # empty book (common in new US programs): synthesize a spread around the
        # reference/last price rather than refusing to quote
        if ref_ask is None or ref_ask - ref_bid <= 0:
            half = self.args.max_spread / 2.0
            best_bid = round(max(0.01, ref_bid - half), 3)
            best_ask = round(min(0.99, ref_bid + half), 3)
        else:
            best_bid, best_ask = ref_bid, ref_ask
        tick = max(self.args.tick, 1e-4)
        size = self._size_for(best_bid)
        target = prog["target"]
        if size < target and not bids and not asks:
            # nobody else is quoting and we can't meet Target Size ourselves
            return {"ts": datetime.now(timezone.utc).isoformat(), "mode": self.mode,
                    "slug": slug, "pool": prog["pool"], "share": 0.0, "est_daily": 0.0,
                    "note": f"size {size} < target {target:.0f} and book empty -> cannot qualify",
                    "repriced": False, "under_target": True}

        # join the best price on each side (post-only maker)
        buy_px, sell_px = best_bid, best_ask
        held, held_cost = self._inventory(slug)
        # --buy-only means "no shorting", not "never exit". When we are long we
        # can quote the ask up to the size we actually own: that scores the ask
        # side AND unwinds inventory, instead of only ever accumulating it.
        sell_size = size if not self.args.buy_only else int(min(held, size))
        buy_size = size
        # Anything that should stop us BUYING only zeroes the bid. The exit has
        # to survive it, or inventory that drifted out of the band (the lottery
        # tickets we most want gone) could never be quoted out.
        held_off = []
        if not (self.args.min_price <= best_bid <= self.args.max_price):
            buy_size = 0
            held_off.append(f"bid {best_bid:.3f} outside band")
        if self.args.max_inventory and held_cost >= self.args.max_inventory:
            buy_size = 0
            held_off.append(f"inventory ${held_cost:,.2f} at cap "
                            f"${self.args.max_inventory:,.2f}")
        if sell_size <= 0:
            sell_px = None
        if buy_size <= 0 and sell_size <= 0:
            self._cancel(slug)
            return {"ts": datetime.now(timezone.utc).isoformat(), "mode": self.mode,
                    "slug": slug, "pool": prog["pool"], "share": 0.0, "est_daily": 0.0,
                    "note": "; ".join(held_off) or "nothing to quote",
                    "repriced": False}
        comp_b, ours_b = score_side(bids, best_bid, prog["discount"], prog["target"], tick, buy_px, buy_size)
        comp_a, ours_a = score_side(asks, best_ask, prog["discount"], prog["target"], tick,
                                    best_ask, sell_size)
        our = ours_b + ours_a
        comp = comp_b + comp_a
        share = our / (our + comp) if (our + comp) > 0 else 0.0
        dur_h = prog.get("duration_hours") or 24.0
        metric = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "mode": self.mode, "slug": slug, "pool": prog["pool"],
            "best_bid": best_bid, "best_ask": best_ask, "share": round(share, 4),
            "est_daily": round(share * prog["pool"] / max(dur_h / 24.0, 1e-6), 4),
            "est_period": round(share * prog["pool"], 4), "size": size,
            "buy_size": buy_size, "sell_size": sell_size,
            "held": held, "held_cost": round(held_cost, 2),
        }
        if held_off:
            metric["note"] = "; ".join(held_off) + " -> exit only"

        # reprice only when the touch moves
        if self.last_best.get(slug) == (best_bid, best_ask) and self.orders.get(slug):
            metric["repriced"] = False
            return metric
        self._cancel(slug)
        if self.mode == "live":
            try:
                if buy_size > 0:
                    o = self.client.place(slug, "buy", buy_px, buy_size, maker=True)
                    self.orders.setdefault(slug, []).append(_oid(o))
                if sell_px is not None and sell_size > 0:
                    o = self.client.place(slug, "sell", sell_px, sell_size, maker=True)
                    self.orders.setdefault(slug, []).append(_oid(o))
            except Exception as e:
                metric["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        else:
            self.orders[slug] = ([f"paper-buy-{slug}"] if buy_size > 0 else []
                                 ) + ([f"paper-sell-{slug}"] if sell_size > 0 else [])
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

    def cancel_all(self):
        try:
            n = len(self.client.open_orders())
        except Exception:
            n = "?"
        try:
            self.client.cancel_all()
            self.orders = {}
            print(f"cancelled {'all' if n == '?' else n} open orders")
        except Exception as e:
            print(f"cancel_all failed: {type(e).__name__} {e}")

    def loop(self):
        print(f"Polymarket US MM | mode={self.mode}")
        # reconcile: a previous run killed by SIGTERM leaves orders resting
        if self.mode == "live":
            try:
                existing = self.client.open_orders()
                if existing:
                    print(f"clearing {len(existing)} pre-existing orders from an earlier run…")
                self.client.cancel_all()
            except Exception as e:
                print(f"warn: could not clear existing orders: {type(e).__name__} {e}")
        progs = self.select() if not self.args.check else self.programs()
        print(f"selected {len(progs)} markets (ranked by est $/day for size {int(self.args.size)})")
        for p in progs[:12]:
            hl = p.get("hours_left")
            hl_s = f"{hl:5.1f}h" if hl is not None else "  now"
            dur = p.get("duration_hours")
            dur_s = f"{dur/24:5.1f}d" if dur else "   ? "
            print(f"  ${p.get('est_daily', 0):>9,.2f}/day  pool ${p['pool']:>7,.0f} "
                  f"[{p.get('period') or '?':<6} {dur_s} ends {hl_s}] "
                  f"share={p.get('share', 0):.3f}  {p['slug'][:34]}")
        it = 0
        self._last_metric = {}
        last_select = time.time()
        try:
          while True:
            it += 1
            # inventory drives both the bid size and the exit quote
            self._refresh_positions()
            # live windows are short: re-select so we roll into the next event
            # instead of quoting a period that already ended
            if (self.args.reselect_min
                    and (time.time() - last_select) / 60.0 >= self.args.reselect_min):
                # STICKY selection: rewards are time-weighted, so never swap a
                # market that is still quotable — only drop ones that became
                # unquotable, then fill empty slots with new candidates.
                keep, dropped = [], 0
                for p in progs:
                    e = self.estimate(p)
                    if e and e.get("quotable") and e.get("meets_target", True):
                        keep.append({**p, **e})
                    else:
                        self._cancel(p["slug"])
                        dropped += 1
                have = {p["slug"] for p in keep}
                slots = self.args.max_markets - len(keep)
                added = 0
                if slots > 0:
                    for p in self.select():
                        if slots <= 0:
                            break
                        if p["slug"] in have:
                            continue
                        keep.append(p)
                        have.add(p["slug"])
                        slots -= 1
                        added += 1
                progs = keep
                last_select = time.time()
                print(f"-- reselect: kept {len(keep)-added}, added {added}, "
                      f"dropped {dropped} --")
            est = 0.0
            for p in progs:
                try:
                    m = self.run_market(p)
                except Exception as e:
                    print(f"  ! {p['slug']}: {type(e).__name__} {str(e)[:60]}")
                    continue
                if m:
                    self._last_metric[m["slug"]] = m
                    if m.get("error"):
                        print(f"  ! {m['slug'][:40]}: {m['error']}")
                if m and m.get("repriced") is not False:
                    est += m.get("est_daily", 0.0)
                    _log(self.args.log_path, m)
                    print(f"  {m['slug'][:40]:<40} share={m.get('share', 0):.3f} "
                          f"est=${m.get('est_daily', 0):.2f}/day")
            real = None
            if self.mode == "live":
                try:
                    real = self.client.earnings()
                except Exception:
                    pass
            n_orders = sum(len(v) for v in self.orders.values())
            live_est = sum(m.get("est_daily", 0) for m in self._last_metric.values()
                           if not m.get("error"))
            best = (max(self._last_metric.values(), key=lambda x: x.get("est_daily", 0))
                    if self._last_metric else None)
            extra = (f"| best share={best['share']:.3f} ({best['slug'][:26]})"
                     if best else "")
            errs = sum(1 for m in self._last_metric.values() if m.get("error"))
            inv = sum(_position_row(v)[1] for v in self.pos.values())
            print(f"[iter {it}] est ${live_est:,.2f}/day | orders {n_orders} "
                  f"| inventory ${inv:,.2f} {extra} "
                  f"| errors {errs} | earnings {json.dumps(real)[:50] if real else '-'}")
            if self.args.once or (self.args.iterations and it >= self.args.iterations):
                break
            time.sleep(self.args.refresh)
        except (KeyboardInterrupt, SystemExit):
            print("\nstopping — cancelling all orders")
        finally:
            if self.mode == "live":
                self.cancel_all()
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
    import signal
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    ap = argparse.ArgumentParser(description="Polymarket US liquidity-rewards MM.")
    ap.add_argument("--check", action="store_true", help="preflight then exit")
    ap.add_argument("--live", action="store_true", help="place real post-only orders")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--iterations", type=int, default=0)
    ap.add_argument("--max-markets", type=int, default=10)
    ap.add_argument("--reselect-min", type=int, default=15,
                    help="re-pick markets every N minutes (0 = never). "
                         "Important for live/day_of windows that end soon.")
    ap.add_argument("--include-empty", action="store_true",
                    help="also quote markets with empty books (they cannot meet Target Size)")
    ap.add_argument("--include-thin", action="store_true",
                    help="also quote markets whose book is below Target Size (won't score)")
    ap.add_argument("--max-per-period", type=int, default=0,
                    help="cap markets from any one period, so a fast-settling "
                         "period (daily) still gets a slot (0 = no cap)")
    ap.add_argument("--scan", type=int, default=25,
                    help="how many candidate programs to book-scan for ranking "
                         "(each costs ~2 API calls; keep under the rate limit)")
    ap.add_argument("--min-pool", type=float, default=100.0)
    ap.add_argument("--size", type=float, default=20, help="contracts per side")
    ap.add_argument("--min-price", type=float, default=0.05,
                    help="never BUY below this price. Exits are not gated by the "
                         "band. See run_calibration.py: sub-0.60 buckets came back "
                         "neutral-to-negative on the cached tape, 0.15-0.30 "
                         "significantly negative. `make run` passes MIN_PX=0.60.")
    ap.add_argument("--max-price", type=float, default=0.90,
                    help="never buy above this price")
    ap.add_argument("--notional", type=float, default=0.0,
                    help="dollar notional per order (overrides --size), e.g. 5 = ~$5/order")
    ap.add_argument("--tick", type=float, default=0.01)
    ap.add_argument("--max-spread", type=float, default=0.05)
    ap.add_argument("--refresh", type=int, default=20)
    ap.add_argument("--log-path", default="research/us_timeseries.jsonl")
    ap.add_argument("--report", action="store_true", help="summarize a paper run")
    ap.add_argument("--cancel-all", action="store_true",
                    help="cancel every open order and exit")
    ap.add_argument("--account", action="store_true",
                    help="show cash, open orders, positions, real earnings")
    ap.add_argument("--hunt", action="store_true",
                    help="book-scan every matching program and list quotable markets")
    ap.add_argument("--max-target", type=float, default=0.0,
                    help="only programs whose Target Size is <= this (0 = any). "
                         "Small targets give a small order a bigger share.")
    ap.add_argument("--buy-only", action="store_true",
                    help="never short: bid freely, and only ever sell shares "
                         "already held (an exit, not a new short position)")
    ap.add_argument("--max-inventory", type=float, default=0.0,
                    help="stop bidding a market once its cost basis reaches $N "
                         "(0 = no cap). Keeps cash from turning into a pile of "
                         "one-sided directional bets.")
    ap.add_argument("--cross", action="store_true",
                    help="exit NOW at the bid instead of resting at the ask, for "
                         "positions whose spread is tight. Frees the capital today.")
    ap.add_argument("--cross-max-pct", type=float, default=0.10,
                    help="only cross when the spread is within this fraction of "
                         "the price (default 0.10). A 37%% spread on a longshot "
                         "costs more than the position is worth, so those rest.")
    ap.add_argument("--flatten", action="store_true",
                    help="post maker sells for every long position and exit "
                         "(dry run unless --live is also passed)")
    ap.add_argument("--ending-within", type=float, default=0.0,
                    help="only programs whose time period ends within N hours "
                         "(faster payout signal; 0 = any)")
    ap.add_argument("--category", default="any",
                    help="substring match on the venue's category/subcategory "
                         "(run `make hunt` to see the labels it actually returns)")
    ap.add_argument("--period", default="any",
                    choices=["any", "early", "day_of", "live", "daily_event", "daily"],
                    help="only this reward time period (daily pays every day)")
    args = ap.parse_args()
    if args.period == "daily":      # the API label is "daily_event"
        args.period = "daily_event"

    if args.report:
        report(args.log_path)
        return
    if args.cancel_all:
        if not os.environ.get("POLYMARKET_US_KEY_ID"):
            raise SystemExit("set POLYMARKET_US_KEY_ID / POLYMARKET_US_SECRET_KEY")
        UsMarketMaker(args).cancel_all()
        return
    if args.account:
        if not os.environ.get("POLYMARKET_US_KEY_ID"):
            raise SystemExit("set POLYMARKET_US_KEY_ID / POLYMARKET_US_SECRET_KEY")
        UsMarketMaker(args).account()
        return
    if args.hunt:
        if not os.environ.get("POLYMARKET_US_KEY_ID"):
            raise SystemExit("set POLYMARKET_US_KEY_ID / POLYMARKET_US_SECRET_KEY")
        UsMarketMaker(args).hunt()
        return
    if args.flatten:
        if not os.environ.get("POLYMARKET_US_KEY_ID"):
            raise SystemExit("set POLYMARKET_US_KEY_ID / POLYMARKET_US_SECRET_KEY")
        mm = UsMarketMaker(args)
        mm.flatten()
        mm.client.close()
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
