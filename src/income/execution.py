"""Orders, fills, hedges. Every price is taken from a book read just before
the order is sent - never from a sweep snapshot that may be 25 minutes old.

A "group" is two legs that must end up equal: a LEADER (the resting leg of a
rest-hedge, or the sell leg of a taker arb) and a FOLLOWER that is traded to
match whatever the leader actually filled. One repair loop handles every
imbalance: a rest leg that filled, an IOC pair where one side came up short,
a hedge that only partly filled last time.
"""

import time

from src.income import state as st
from src.income.signals import hedge_credit

# A book can list offers while the market is not matching (suspended or halted
# in-play): an IOC at the listed ask then gets nothing. On Liberty-Coastal six
# hedge IOCs at a displayed 0.53 missed over 8 minutes. None = venue sent no state.
TRADABLE = {None, "MARKET_STATE_OPEN"}
TERMINAL = {"ORDER_STATE_FILLED", "ORDER_STATE_CANCELED", "ORDER_STATE_REJECTED",
            "ORDER_STATE_EXPIRED", "ORDER_STATE_REPLACED"}


def _num(v, default=0.0):
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _oid(resp):
    if isinstance(resp, dict):
        return resp.get("id") or resp.get("orderId") or (resp.get("order") or {}).get("id")
    return None


PAY_UP_STEP = 0.03       # forced hedge: limit this much past the display per miss
PAY_UP_MAX = 0.12
MAX_REPAIRS = 8          # repair orders per group before the game is frozen


class Executor:
    def __init__(self, client, store, s, live=False, throttle=None, log=print,
                 tick=0.001, max_naked_min=20.0, max_rest_hours=6.0,
                 hedge_slip=0.02):
        self.c, self.store, self.s = client, store, s
        self.live, self.throttle, self.say = live, throttle, log
        self.tick, self.max_naked_min = tick, max_naked_min
        self.max_rest_hours, self.hedge_slip = max_rest_hours, hedge_slip
        self.last_uncertain = False

    def suspect(self, game, why):
        """Freeze a game: no scanning, no repairing, until a human clears it
        with --clear-suspect. Used whenever our book may not match the venue."""
        sus = self.s.setdefault("suspect_games", [])
        if game not in sus:
            sus.append(game)
            self.store.log("suspect", game=game, reason=why)
            self.say(f"  !! {game[:40]} FROZEN: {why}")

    def is_suspect(self, game):
        return game in self.s.get("suspect_games", [])

    # ---- market data ---------------------------------------------------
    def _call(self, fn):
        if self.throttle is None:
            return fn()
        from src.pm_us.throttle import paced_call
        return paced_call(fn, self.throttle)

    def quote(self, slug):
        bids, asks, state = self._call(lambda: self.c.book_levels(slug))
        return {"state": state,
                "bid": bids[0][0] if bids else None,
                "bid_sz": bids[0][1] if bids else 0,
                "ask": asks[0][0] if asks else None,
                "ask_sz": asks[0][1] if asks else 0,
                "bids": bids[:10], "asks": asks[:10]}

    def quotes(self, ks, lines):
        out = {}
        for L in lines:
            try:
                q = self.quote(ks[L])
            except Exception:
                continue
            if q["state"] in TRADABLE:
                out[L] = q                  # a non-matching book is no price at all
        return out

    # ---- orders --------------------------------------------------------
    def place(self, game, line, slug, side, px, qty, maker, strat, group=None,
              fair=None, leg=None):
        """Write-ahead, send, resolve. Returns the order record or None.

        None with self.last_uncertain set means the venue MAY have the order
        (timeout, no id): the intent is kept for reconcile() and the game is
        frozen, because re-sending would double the position.
        """
        self.last_uncertain = False
        qty = int(qty)
        if qty < 1 or px is None or not 0.0 < px < 1.0:
            return None
        tag = "rest" if maker else "IOC"
        if not self.live:
            self.say(f"    [dry] {strat:<10} {side:<4} {qty:>3} {slug[-22:]} @ {px:.3f} {tag}")
            self.store.log("dry_order", game=game, line=line, side=side, px=px,
                           qty=qty, maker=maker, strat=strat, fair=fair)
            return None
        iid = st.new_intent(self.s, game=game, line=line, slug=slug, side=side,
                            px=round(px, 4), qty=qty, maker=maker, strat=strat,
                            group=group, leg=leg, fair=fair)
        self.store.save(self.s)
        try:
            resp = self.c.place(slug, side, px, qty, maker=maker,
                                tif=None if maker else "ioc")
        except Exception as e:
            msg = f"{type(e).__name__}: {str(e)[:160]}"
            self.store.log("order_error", slug=slug, side=side, px=px, qty=qty,
                           strat=strat, error=msg)
            if _definitely_rejected(e):
                self.s["intents"].pop(iid, None)
                self.say(f"    ! {strat} {side} {slug[-22:]} rejected: {msg[:80]}")
            else:
                self.last_uncertain = True
                self.suspect(game, f"order outcome unknown ({msg[:60]})")
            self.store.save(self.s)
            return None
        oid = _oid(resp)
        if oid is None:
            self.last_uncertain = True
            self.store.log("order_no_id", slug=slug, resp=str(resp)[:200])
            self.suspect(game, "venue returned no order id")
            self.store.save(self.s)
            return None
        rec = st.resolve_intent(self.s, iid, oid)
        self.store.save(self.s)
        self.store.log("order", **{k: rec[k] for k in
                                   ("oid", "game", "line", "side", "px", "qty",
                                    "maker", "strat", "group")})
        self.say(f"    {strat:<10} {side:<4} {qty:>3} {slug[-22:]} @ {px:.3f} {tag} "
                 f"-> {rec['oid']}")
        if not maker:
            self.refresh(rec)         # IOC should be terminal by the time we ask
        return rec

    def refresh(self, rec):
        """Read the order by id and book any new fills. Never infers."""
        try:
            o = self.c.order(rec["oid"])
        except Exception as e:
            self.say(f"    order {rec['oid']} unreadable ({type(e).__name__}); unchanged")
            return rec
        o = o.get("order", o) if isinstance(o, dict) else {}
        cum = o.get("cumQuantity")
        if cum is not None:
            st.apply_fill(self.s, self.store, rec, int(_num(cum)),
                          _num(o.get("avgPx"), None) or None)
        state = o.get("state")
        if state:
            rec["venue_state"] = state
        if state in TERMINAL and rec.get("status") != "done":
            rec["status"] = "done"
            # state drops finished orders; this is the only record of HOW one ended
            self.store.log("order_done", oid=rec["oid"], game=rec["game"],
                           strat=rec.get("strat"), state=state, filled=rec.get("filled", 0),
                           qty=rec["qty"], px=rec["px"])
        return rec

    def cancel(self, rec):
        if not self.live or rec.get("status") == "done":
            return
        try:
            self.c.cancel(rec["oid"], rec["slug"])
        except Exception as e:
            self.say(f"    cancel {rec['oid']} failed: {type(e).__name__}")
        self.refresh(rec)             # book anything that filled before the cancel

    def open_orders(self, game=None, strat=None):
        return [o for o in self.s["orders"].values()
                if o.get("status") == "open"
                and (game is None or o["game"] == game)
                and (strat is None or o.get("strat") == strat)]

    # ---- startup reconciliation ----------------------------------------
    def reconcile(self):
        """Refresh every open order; resolve intents a crash or timeout left."""
        for rec in list(self.open_orders()):
            self.refresh(rec)
        if self.s["intents"]:
            try:
                live = self.c.open_orders() or []
            except Exception:
                live = None
            if live is None:
                self.say("  open orders unreadable; intents left for next cycle")
            for iid, it in list(self.s["intents"].items()):
                if live is None:
                    break
                match = [o for o in live if o.get("marketSlug") == it["slug"]
                         and str(_oid(o)) not in self.s["orders"]
                         and abs(_num(o.get("price")) - it["px"]) < 1e-6
                         and int(_num(o.get("quantity"))) == it["qty"]]
                if len(match) == 1:
                    rec = st.resolve_intent(self.s, iid, _oid(match[0]))
                    g = self.s["groups"].get(it.get("group") or "")
                    if g and it.get("leg") in ("leader", "follower"):
                        g[it["leg"]]["oids"].append(rec["oid"])
                    self.say(f"  recovered order {rec['oid']} for {it['slug'][-22:]}")
                    self.refresh(rec)
                else:
                    # an IOC may have filled and vanished: the game stays
                    # frozen until venue positions are checked by a human
                    self.s["intents"].pop(iid)
                    self.store.log("orphan_intent", **it)
                    self.suspect(it["game"], f"intent {iid} unresolved on {it['slug'][-22:]}")
        # drop finished orders from state; the ledger keeps the history
        for oid in [k for k, o in self.s["orders"].items() if o.get("status") == "done"
                    and not self._in_live_group(k)]:
            self.s["orders"].pop(oid)
        self.store.save(self.s)

    def _in_live_group(self, oid):
        return any(oid in g["leader"]["oids"] or oid in g["follower"]["oids"]
                   for g in self.s["groups"].values())

    # ---- groups --------------------------------------------------------
    def _orders(self, leg):
        return [self.s["orders"][o] for o in leg["oids"] if o in self.s["orders"]]

    def _leg_net(self, leg):
        """Shares on the leg's side, net: a flatten order on the opposite side
        SUBTRACTS. Summing unsigned fills is what let one flatten feed the next."""
        return sum(o.get("filled", 0) * (1 if o["side"] == leg["side"] else -1)
                   for o in self._orders(leg))

    def new_group(self, kind, game, leader, follower, meta):
        import uuid
        gid = uuid.uuid4().hex[:10]
        self.s["groups"][gid] = {"gid": gid, "kind": kind, "game": game,
                                 "created": st.now_iso(), "leader": leader,
                                 "follower": follower, "meta": meta, "repairs": 0}
        return gid

    def manage_groups(self, force_games=(), skip_games=()):
        """Bring every group's legs level; pull resting leaders gone bad.

        force_games: games past kickoff - hedge now, no slippage wait.
        skip_games:  games whose outcome is known - never trade them again.
        """
        for gid, g in list(self.s["groups"].items()):
            game = g["game"]
            if game in skip_games or self.is_suspect(game):
                continue
            lead, fol = g["leader"], g["follower"]
            for rec in self._orders(lead) + self._orders(fol):
                if rec.get("status") == "open":
                    self.refresh(rec)
            # a follower order still working (an IOC the venue let rest) is
            # cancelled and re-read before anything else is sent
            pending = [r for r in self._orders(fol) if r.get("status") == "open"]
            for r in pending:
                self.cancel(r)
            if any(r.get("status") == "open" for r in self._orders(fol)):
                continue                    # outcome still unknown: send nothing
            need = self._leg_net(lead) - self._leg_net(fol)
            if need != 0:
                g.setdefault("naked_since", st.now_iso())
                self._repair(g, need, game in force_games)
                need = self._leg_net(lead) - self._leg_net(fol)
            if need == 0:
                g.pop("naked_since", None)
            self._maybe_pull(g)
            leader_open = any(r.get("status") == "open" for r in self._orders(lead))
            if need == 0 and not leader_open and \
                    not any(r.get("status") == "open" for r in self._orders(fol)):
                self._close_group(gid, g)
        self.store.save(self.s)

    def _repair(self, g, need, force):
        fol = g["follower"]
        if g.get("repairs", 0) >= MAX_REPAIRS:
            self.suspect(g["game"], f"group {g['gid']} needed {MAX_REPAIRS}+ repairs")
            return
        # never trade more than the leader could have filled
        cap = sum(r["qty"] for r in self._orders(g["leader"]))
        qty = min(abs(need), cap)
        side = fol["side"] if need > 0 else ("sell" if fol["side"] == "buy" else "buy")
        if qty < 1:
            return
        try:
            q = self.quote(fol["slug"])
        except Exception:
            self.say(f"    hedge book unreadable for {fol['slug'][-22:]}; retry next cycle")
            return
        if q.get("state") not in TRADABLE:
            self.say(f"    hedge {fol['slug'][-22:]}: market {q['state']}, waiting")
            return
        px = q["ask"] if side == "buy" else q["bid"]
        if px is None:
            return
        age = _minutes_since(g.get("naked_since"))
        if need > 0:                        # hedging: compare to the expected price
            ref = g["meta"].get("hedge_px", px)
            slipped = (px - ref) if side == "buy" else (ref - px)
        else:                               # flattening excess: pay at most the spread
            mid = (q["bid"] + q["ask"]) / 2 if q["bid"] and q["ask"] else px
            slipped = abs(px - mid) - (self.hedge_slip / 2)
        if slipped > self.hedge_slip + 1e-9 and age < self.max_naked_min and not force:
            self.say(f"    hedge {fol['slug'][-22:]} slipped {slipped:+.3f}; "
                     f"waiting ({age:.0f}/{self.max_naked_min:.0f} min)")
            return
        # A displayed price an IOC has already missed is not a price. Liberty-
        # Coastal resent a buy at a listed 0.53 six times (all EXPIRED, 0 filled)
        # while the market traded 0.645. An IOC fills at the best real offer, so
        # a higher limit costs nothing when the display is true.
        misses = sum(1 for r in self._orders(fol)
                     if r.get("status") == "done" and not r.get("filled"))
        if misses and (force or age >= self.max_naked_min):
            step = min(misses * PAY_UP_STEP, PAY_UP_MAX)
            px = round(min(px + step, 0.999) if side == "buy" else max(px - step, 0.001), 4)
            self.say(f"    hedge {fol['slug'][-22:]}: {misses} misses at the displayed "
                     f"price, limit {px:.3f}")
        g["repairs"] = g.get("repairs", 0) + 1
        rec = self.place(g["game"], fol["line"], fol["slug"], side, px, qty,
                         maker=False, strat="hedge", group=g["gid"], leg="follower")
        if rec:
            fol["oids"].append(rec["oid"])

    def _maybe_pull(self, g):
        """Cancel a resting leader whose pair no longer pays, or that is old."""
        if g["kind"] != "rest_hedge":
            return
        for rec in self._orders(g["leader"]):
            if rec.get("status") != "open":
                continue
            age_h = _minutes_since(rec["ts"]) / 60.0
            reason = None
            if age_h >= self.max_rest_hours:
                reason = f"aged {age_h:.1f}h"
            else:
                try:
                    hq = self.quote(g["follower"]["slug"])
                    c = hedge_credit(rec["side"], rec["px"], hq)
                    if c is None or c < g["meta"].get("min_credit", 0.0):
                        reason = f"hedge moved, credit now {c}"
                except Exception:
                    pass
            if reason:
                self.say(f"    pull {rec['slug'][-22:]}: {reason}")
                self.store.log("pull", oid=rec["oid"], group=g["gid"], reason=reason)
                self.cancel(rec)

    def pull_game(self, game, strats=("mm", "rest_hedge")):
        for o in self.open_orders(game):
            if o.get("strat") in strats:
                self.cancel(o)

    def _close_group(self, gid, g):
        """Log what the pair ACTUALLY locked: the cash its fills produced,
        fees and rebates included, per leader share."""
        orders = self._orders(g["leader"]) + self._orders(g["follower"])
        n = self._leg_net(g["leader"])
        if n > 0:
            cash = sum(o.get("cash", 0.0) for o in orders)
            self.store.log("pair_done", group=gid, pair_kind=g["kind"], game=g["game"],
                           shares=n, credit_net=round(cash / n, 5),
                           expected=g["meta"].get("credit"))
        self.s["groups"].pop(gid)

    def abandon_groups(self, game, why):
        for gid, g in list(self.s["groups"].items()):
            if g["game"] == game:
                self.store.log("pair_abandoned", group=gid, game=game, reason=why,
                               leader=self._leg_net(g["leader"]),
                               follower=self._leg_net(g["follower"]))
                self.s["groups"].pop(gid)

    # ---- settlement ----------------------------------------------------
    def settle(self, game, margin):
        from src.income.risk import scenario_pnl
        for rec in self.open_orders(game):
            self.cancel(rec)
        self.abandon_groups(game, "game settled")
        b = st.book(self.s, game)
        pnl = scenario_pnl(st.book_pos(b), b["cash"])[max(min(int(margin), 90), -90)]
        st.record_realized(self.s, pnl)
        self.s["settled"][game] = {"margin": margin, "pnl": round(pnl, 4),
                                   "ts": st.now_iso()}
        self.store.log("settle", game=game, margin=margin, pnl=round(pnl, 4))
        self.say(f"  SETTLED {game}: margin {margin:+d} -> ${pnl:+.2f}")
        self.store.save(self.s)
        return pnl


def _definitely_rejected(e):
    """Only a venue answer counts as 'not placed'. Timeouts and connection
    errors mean the order may be live."""
    s = f"{type(e).__name__} {e}".lower()
    if any(w in s for w in ("timeout", "timed out", "connection", "reset", "502",
                            "503", "504", "temporarily")):
        return False
    return any(w in s for w in ("reject", "invalid", "insufficient", "cross",
                                "400", "422", "not allowed", "post-only", "post only"))


def _minutes_since(iso):
    if not iso:
        return 0.0
    from datetime import datetime, timezone
    try:
        t0 = datetime.fromisoformat(iso)
    except ValueError:
        return 0.0
    return (datetime.now(timezone.utc) - t0).total_seconds() / 60.0
