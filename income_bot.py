"""Sharp-anchored ladder trader. Replaces ladder_bot.py; see INCOME.md.

    python income_bot.py                         # dry run, one full cycle
    python income_bot.py --manage-only           # refresh fills, hedge, settle
    python income_bot.py --live --capital 10     # real orders
    python income_bot.py --review                # CLV, fill rate, kill status

One cycle, in order:
  1. reconcile  every tracked order is re-read BY ID; fills are the change in
                its cumQuantity. Nothing is inferred from aggregate positions.
  2. repair     any group whose legs differ is levelled: a filled resting leg
                is hedged, a short IOC leg is topped up or the excess flattened.
  3. settle     finished games are settled from ESPN's final score.
  4. govern     pre-registered kill rules and loss halts are evaluated.
  5. scan       (skipped with --manage-only) each game's books are read and
                acted on IMMEDIATELY, so no order is priced off a stale sweep.

Run --manage-only every few minutes and a full cycle a few times a day:
hedge latency is then minutes, not the 12 hours ladder_bot allowed.
"""

import argparse
import fcntl
import os
import sys

from src.income import state as st
from src.income.execution import Executor
from src.income.risk import (KILL_RULES, Limits, allowed, evaluate_kills,
                             halt_reason, with_trade, worst_case)
from src.income.signals import (mm_quotes, model_agrees, rest_hedge, taker_arbs,
                                value_takes)
from src.income.sizing import arb_shares, value_shares
from src.pm_us.fees import maker_rebate, taker_fee

STATE = os.path.join("research", "income_state.json")
LEDGER = os.path.join("research", "income_ledger.jsonl")
LOCK = os.path.join("research", "income.lock")
STRATS = ("taker_arb", "rest_hedge", "value", "mm", "inplay_arb", "divergence")
INPLAY_OBS = os.path.join("research", "inplay_obs.jsonl")


def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--manage-only", action="store_true")
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--capital", type=float, default=5.0,
                    help="max $ of collateral this bot may tie up")
    ap.add_argument("--strategies", default="taker_arb,rest_hedge,value",
                    help=f"comma list from {','.join(STRATS)}. mm is experimental")
    ap.add_argument("--edge-min", type=float, default=0.03,
                    help="value take: edge over fair AFTER the taker fee")
    ap.add_argument("--kelly", type=float, default=0.25, help="fraction of Kelly")
    ap.add_argument("--max-order", type=float, default=3.0, help="$ cap per value take")
    ap.add_argument("--min-credit", type=float, default=0.005,
                    help="arb credit per share after the fees actually paid")
    ap.add_argument("--max-pair-shares", type=int, default=20)
    ap.add_argument("--rest-budget", type=float, default=0.4,
                    help="max fraction of --capital tied up in resting orders")
    ap.add_argument("--rest-max-gap", type=float, default=0.08,
                    help="skip a resting leg priced further than this from fair "
                         "against the counterparty: nobody rational fills it")
    ap.add_argument("--mm-half-spread", type=float, default=0.025)
    ap.add_argument("--mm-size", type=int, default=5)
    ap.add_argument("--mm-max-inv", type=int, default=15)
    ap.add_argument("--near", type=int, default=24, help="strikes per ladder")
    ap.add_argument("--max-games", type=int, default=0, help="0 = all")
    ap.add_argument("--max-days", type=int, default=7)
    ap.add_argument("--scan-minutes", type=float, default=18.0,
                    help="time budget for a full scan, soonest games first")
    ap.add_argument("--tick", type=float, default=0.001)
    ap.add_argument("--max-game-loss", type=float, default=3.0)
    ap.add_argument("--max-total-loss", type=float, default=8.0)
    ap.add_argument("--daily-loss", type=float, default=3.0)
    ap.add_argument("--max-naked-min", type=float, default=20.0)
    ap.add_argument("--max-rest-hours", type=float, default=6.0)
    ap.add_argument("--hedge-slip", type=float, default=0.02)
    ap.add_argument("--inplay-minutes", type=float, default=0.0,
                    help="after managing, day-trade live games for N minutes "
                         "(inplay_arb live, divergence on paper)")
    ap.add_argument("--poll", type=float, default=5.0, help="in-play poll seconds")
    ap.add_argument("--reset-kill", default="", help="clear a tripped kill rule")
    ap.add_argument("--clear-suspect", default="",
                    help="unfreeze a game after checking venue positions by hand")
    ap.add_argument("--settle", default="",
                    help="GAME:MARGIN - settle a book ESPN cannot resolve")
    ap.add_argument("--state", default=None)
    ap.add_argument("--ledger", default=None)
    a = ap.parse_args(argv)
    # a dry run keeps its own books: simulated fills and settlements must
    # never mix into the state that live trading sizes and halts against
    sfx = "" if (a.live or a.review) else ".dry"     # --review reads the live books
    a.state = a.state or STATE.replace(".json", f"{sfx}.json")
    a.ledger = a.ledger or LEDGER.replace(".jsonl", f"{sfx}.jsonl")
    a.strats = {x.strip() for x in a.strategies.split(",") if x.strip()}
    bad = a.strats - set(STRATS)
    if bad:
        ap.error(f"unknown strategies {bad}")
    return a


# ---- inventory -------------------------------------------------------------

INV_CACHE = os.path.join("research", "inventory_cache.json")


def all_slugs(c, say=print, page=500, max_pages=200, ttl=1800):
    """Every active market slug, from BOTH listings, with the failures SAID.

    The first version swallowed every exception and returned 0 ladders on the
    live box with no hint why - the exact silent-failure pattern this rewrite
    exists to remove. Each source now reports its count or its error.
    """
    if getattr(c, "_slug_cache", None) is not None:
        return c._slug_cache
    # the full listing is ~150 pages; a 5-minute cron run must not pay that
    # every time. The market list changes slowly - new games are listed days out.
    import json as _j
    import time as _t
    try:
        with open(INV_CACHE) as fh:
            cached = _j.load(fh)
        if _t.time() - cached["ts"] < ttl and cached["slugs"]:
            c._slug_cache = set(cached["slugs"])
            return c._slug_cache
    except (OSError, ValueError, KeyError):
        pass
    slugs, notes = set(), []
    got = 0
    try:
        for i in range(max_pages):
            rows = c.markets(limit=page, offset=i * page, active=True, closed=False)
            batch = {m.get("marketSlug") or m.get("slug") for m in rows or []} - {None}
            got += len(rows or [])
            slugs |= batch
            if len(rows or []) < page:
                break
        else:
            notes.append(f"!! stopped at the {max_pages}-page cap - listing TRUNCATED")
        notes.append(f"markets() {got} rows")
    except Exception as e:
        notes.append(f"markets() FAILED {type(e).__name__}: {str(e)[:100]}")
    try:
        progs = c.all_programs()
        n0 = len(slugs)
        slugs |= {p["slug"] for p in progs if p.get("slug")}
        notes.append(f"all_programs() {len(progs)} (+{len(slugs) - n0} new)")
    except Exception as e:
        notes.append(f"all_programs() FAILED {type(e).__name__}: {str(e)[:100]}")
    say(f"  inventory: {len(slugs)} slugs | " + " | ".join(notes))
    if slugs:
        tmp = INV_CACHE + ".tmp"
        with open(tmp, "w") as fh:
            _j.dump({"ts": _t.time(), "slugs": sorted(slugs)}, fh)
        os.replace(tmp, INV_CACHE)
    kinds = {}
    for sl in slugs:
        kinds[sl.split("-", 1)[0]] = kinds.get(sl.split("-", 1)[0], 0) + 1
    say("  by prefix: " + ", ".join(f"{k}={v}" for k, v in
                                   sorted(kinds.items(), key=lambda kv: -kv[1])[:8])
        + f" | ladder-shaped (-pos-/-neg-): "
        f"{sum(1 for sl in slugs if '-pos-' in sl or '-neg-' in sl)}")
    c._slug_cache = slugs
    return slugs


def inventory(c, say=print):
    """{base: {line: slug}} for every spread ladder with 2+ strikes."""
    from run_ladder import parse_strike
    ladders = {}
    for sl in all_slugs(c, say):
        base, k = parse_strike(sl)
        if base is not None:
            ladders.setdefault(base, {})[k] = sl
    out = {b: v for b, v in ladders.items() if len(v) >= 2}
    if not out:
        sample = sorted(all_slugs(c, say))[:5]
        say(f"  no ladders parsed. sample slugs: {sample}")
    return out


def moneyline_inventory(c):
    """Single-winner game markets (aec-<sport>-<a>-<b>-<date>), assumed to pay
    on the FIRST team token like the ladders do. Paper-only, and the model is
    refused on any market it disagrees with by 25c, which is what a flipped
    side looks like."""
    from src.pm_us.feed import parse_slug
    slugs = all_slugs(c)
    return sorted(sl for sl in slugs
                  if sl.startswith("aec-") and parse_slug(sl)
                  and parse_slug(sl)[0] in ("cfb", "nfl"))


def _now_s():
    import time as _t
    return _t.time()


def _load_tracker(tr, d):
    """Tracker state survives between 5-minute cron runs: without it every
    run would restart the post-score cooldown and the persistence clock."""
    from src.income.inplay import Divergence
    if not d:
        return
    tr.last_score = {g: (tuple(v[0]), v[1]) for g, v in d.get("last_score", {}).items()}
    for k, v in d.get("div", {}).items():
        g, L = k.rsplit("|", 1)
        tr.state[(g, float(L))] = Divergence(*v)


def _dump_tracker(tr):
    return {"last_score": {g: [list(v[0]), v[1]] for g, v in tr.last_score.items()},
            "div": {f"{g}|{L}": [d.sign, d.since, d.mid0]
                    for (g, L), d in tr.state.items()}}


def game_date(base):
    import re
    m = re.search(r"(\d{4}-\d{2}-\d{2})", base)
    return m.group(1) if m else "9999-99-99"


def within(base, days):
    """Dated today .. today+days. Past-dated ladders are never new risk."""
    from datetime import date, timedelta
    d = game_date(base)
    if d.startswith("9999"):
        return False
    y, m, dd = (int(x) for x in d.split("-"))
    return date.today() <= date(y, m, dd) <= date.today() + timedelta(days=days)


def _today_minus(n):
    from datetime import date, timedelta
    return (date.today() - timedelta(days=n)).isoformat()


# ---- capital ---------------------------------------------------------------

def locked_capital(s):
    """Collateral tied up by our books and resting orders. The venue does not
    net: a long ties up its price, a short (1 - price)."""
    tot = 0.0
    for g, b in s["books"].items():
        if g in s["settled"]:
            continue
        shorts = sum(-v for v in b["pos"].values() if v < 0)
        tot += max(0.0, -b["cash"] + shorts)
    for o in s["orders"].values():
        if o.get("status") == "open" and o.get("maker"):
            left = o["qty"] - o.get("filled", 0)
            tot += left * (o["px"] if o["side"] == "buy" else 1.0 - o["px"])
    return tot


def resting_capital(s):
    return sum((o["qty"] - o.get("filled", 0)) *
               (o["px"] if o["side"] == "buy" else 1.0 - o["px"])
               for o in s["orders"].values()
               if o.get("status") == "open" and o.get("maker"))


def fillable(sig, fair, max_gap):
    """Would a rational counterparty ever take our resting price? Selling at
    18.9c a strike worth 0.3c is a great trade that will never happen, and it
    ties up 81c of collateral per share while it doesn't."""
    f = fair.get(sig["rest_line"])
    if f is None:
        return True
    gap = (sig["rest_px"] - f) if sig["rest_side"] == "sell" else (f - sig["rest_px"])
    return gap <= max_gap


def venue_buying_power(c):
    def find(d):
        if isinstance(d, dict):
            for k, v in d.items():
                if k == "buyingPower":
                    try:
                        return float(v.get("value") if isinstance(v, dict) else v)
                    except (TypeError, ValueError):
                        return None
                r = find(v)
                if r is not None:
                    return r
        elif isinstance(d, list):
            for v in d:
                r = find(v)
                if r is not None:
                    return r
        return None
    try:
        return find(c.balances())
    except Exception:
        return None


# ---- the cycle -------------------------------------------------------------

class Bot:
    def __init__(self, args, client, store, s, lines, say=print):
        self.a, self.c, self.store, self.s, self.lines, self.say = \
            args, client, store, s, lines, say
        self.limits = Limits(args.max_game_loss, args.max_total_loss,
                             args.daily_loss, 0.25, args.max_naked_min)
        from src.pm_us.throttle import Throttle
        self.ex = Executor(client, store, s, live=args.live, throttle=Throttle(0.4),
                           log=say, tick=args.tick, max_naked_min=args.max_naked_min,
                           max_rest_hours=args.max_rest_hours,
                           hedge_slip=args.hedge_slip)
        self.halt = None
        self.slugs = s.setdefault("slugs", {})     # game -> {line: slug}

    def enabled(self, strat):
        return strat in self.a.strats and strat not in self.s["kills"] \
            and self.halt is None

    def manage(self):
        """Reconcile, hedge, pull at kickoff, settle. Runs every few minutes.

        Order matters: game states are read FIRST so that the repair loop
        knows which games are past kickoff (hedge now, no waiting) and which
        are decided (never trade again - the result is known and a stale book
        would happily sell you the loser).
        """
        self.ex.reconcile()
        active = {g for g, b in self.s["books"].items() if g not in self.s["settled"]}
        active |= {o["game"] for o in self.ex.open_orders()}
        active |= {g["game"] for g in self.s["groups"].values()}
        infos = {g: self.lines.game(g) for g in sorted(active)}
        started = {g for g, i in infos.items() if i and i["state"] == "in"}
        decided = {g for g, i in infos.items()
                   if i and i["state"] == "post" and i.get("margin") is not None}
        # "post" without a completed result: postponed or cancelled. The venue
        # will likely void it; trade nothing and settle nothing automatically.
        voided = {g for g, i in infos.items()
                  if i and i["state"] == "post" and i.get("margin") is None}
        for game in sorted(started | decided | voided):
            self.ex.pull_game(game)
        for game in sorted(voided):
            self.say(f"  !! {game[:44]} is over with no result (postponed?). "
                     f"Check the venue; settle by hand with --settle if needed.")
        self.ex.manage_groups(force_games=started, skip_games=decided | voided)
        for game, info in infos.items():
            if info is None:
                if game_date(game) < _today_minus(2) and game not in self.s["settled"]:
                    self.say(f"  !! {game[:44]} cannot be settled automatically "
                             f"(no ESPN match). Settle by hand: --settle {game}:MARGIN")
                continue
            if info["state"] == "pre":
                self.mark(game, info)          # closing line and marks for CLV
            else:
                self.store.log("fair", game=game, state=info["state"])
            if game in decided:
                self.ex.settle(game, info["margin"])
        self.govern()
        self.store.save(self.s)

    def mark(self, game, info):
        """Log fair and the book mid for every strike we hold, pre-game. The
        last marks before kickoff are the CLOSING prices CLV is judged on."""
        m = info.get("model")
        if m:
            self.store.log("fair", game=game, state="pre", mu=m.mu, sigma=m.sigma,
                           league=m.league, provider=info.get("provider"))
        held = [float(k) for k, v in st.book(self.s, game)["pos"].items() if v]
        slugs = self.slugs.get(game, {})
        for L in held:
            if str(L) not in slugs:
                continue
            try:
                q = self.ex.quote(slugs[str(L)])
            except Exception:
                continue
            if q["bid"] is not None and q["ask"] is not None:
                self.store.log("mark", game=game, line=L, state="pre",
                               mid=(q["bid"] + q["ask"]) / 2,
                               fair=m.p_cover(L) if m else None)

    def govern(self):
        from src.income.measure import metrics
        before = dict(self.s["kills"])
        self.s["kills"] = evaluate_kills(metrics(self.store.ledger), before)
        for k in set(self.s["kills"]) - set(before):
            self.say(f"  !! KILL RULE TRIPPED: {k}: {self.s['kills'][k]}")
            self.store.log("kill", strategy=k, reason=self.s["kills"][k])
            for o in self.ex.open_orders(strat=k):
                self.ex.cancel(o)
        self.halt = halt_reason(self.s["realized"], st.realized_today(self.s),
                                self.s.get("start_equity", 0.0), self.limits)
        if self.halt:
            self.say(f"  !! HALT (no new risk): {self.halt}")

    def free_capital(self):
        free = self.a.capital - locked_capital(self.s)
        bp = venue_buying_power(self.c) if self.a.live else None
        if bp is not None:
            free = min(free, bp)
        return max(free, 0.0)

    def risk_ok(self, game, fills, quiet=False):
        """fills: [(line, side, px, shares, fee)] applied together.

        Resting orders count: the book is checked with every open buy filled,
        and again with every open sell filled, and must pass both. Checking
        each resting order alone let two 5-lot sells through a $2 cap.
        """
        b = st.book(self.s, game)
        resting = [(o["line"], o["side"], o["px"], o["qty"] - o.get("filled", 0), 0.0)
                   for o in self.ex.open_orders(game) if o["qty"] > o.get("filled", 0)]
        ok, why = True, ""
        for side in ("buy", "sell"):
            pos, cash = st.book_pos(b), b["cash"]
            for line, sd, px, sh, fee in [r for r in resting if r[1] == side] + list(fills):
                pos, cash = with_trade(pos, cash, line, sd, px, sh, fee)
            ok, why = allowed(pos, cash, st.worst_others(self.s, game), self.limits)
            if not ok:
                break
        if not ok and not quiet:
            self.say(f"    risk: skip ({why})")
        return ok

    def fit(self, game, n, fills_for):
        """Largest size <= n the risk limits allow: shrink, don't skip.
        fills_for(k) -> the fills a size-k trade would produce."""
        while n >= 1 and not self.risk_ok(game, fills_for(n), quiet=True):
            n = n - 1 if n <= 8 else int(n * 0.7)
        if n < 1:
            self.risk_ok(game, fills_for(1))        # say why once
        return max(n, 0)

    def scan(self):
        ladders = inventory(self.c, self.say)
        todo = [(b, ks) for b, ks in ladders.items()
                if within(b, self.a.max_days) and b not in self.s["settled"]
                and b not in self.s.get("suspect_games", [])]
        todo.sort(key=lambda bk: (game_date(bk[0]), -len(bk[1])))
        if self.a.max_games:
            todo = todo[: self.a.max_games]
        self.say(f"  {len(ladders)} ladders, {len(todo)} in window | "
                 f"free ${self.free_capital():.2f} | strategies "
                 f"{','.join(s for s in STRATS if self.enabled(s)) or 'NONE'}")
        import time as _t
        t_end = _t.time() + self.a.scan_minutes * 60
        done = 0
        for base, ks in todo:
            if _t.time() > t_end:
                self.say(f"  scan budget spent: {done}/{len(todo)} games scanned, "
                         f"soonest first; the rest wait for a later cycle")
                break
            self.game(base, ks)
            self.store.save(self.s)
            done += 1

    def game(self, base, ks):
        info = self.lines.game(base)
        # Only a CONFIRMED pre-game ladder is traded. No ESPN match means no
        # kickoff pulls and no automatic settlement, so it gets no new risk.
        if not info or info["state"] != "pre":
            return
        model, state = info["model"], info["state"]
        center = -model.mu if model else 0.0
        lines = sorted(sorted(ks, key=lambda k: abs(k - center))[: self.a.near])
        q = self.ex.quotes(ks, lines)               # read NOW, act NOW
        self.slugs[base] = {str(k): v for k, v in ks.items()}
        if len(q) < 2:
            return
        fair = model.ladder(q) if model else {}
        if model:
            self.store.log("fair", game=base, state=state, mu=model.mu,
                           sigma=model.sigma, league=model.league,
                           provider=info.get("provider"))
            held = {float(k) for k, v in st.book(self.s, base)["pos"].items() if v}
            for L in held & set(q):
                if q[L]["bid"] is not None and q[L]["ask"] is not None:
                    self.store.log("mark", game=base, line=L,
                                   mid=(q[L]["bid"] + q[L]["ask"]) / 2, fair=fair[L])
        trust = False
        if model:
            trust, med = model_agrees(q, fair)
            if not trust:
                self.store.log("model_reject", game=base, median_dev=med)
        tag = (f"N({model.mu:+.1f},{model.sigma:.1f}) {info.get('provider')}"
               + ("" if trust else " [model disagrees with book: no value/mm]")
               if model else "no line")
        self.say(f"  {base[4:40]:<36} {len(q):>2} strikes  {tag}")

        if self.enabled("taker_arb"):
            for sig in taker_arbs(q, self.a.min_credit):
                self.do_taker_arb(base, ks, sig)
        if self.enabled("rest_hedge"):
            busy = {o["line"] for o in self.ex.open_orders(base)}
            for sig in rest_hedge(q, self.a.tick, self.a.min_credit):
                if sig["rest_line"] in busy or sig["hedge_line"] in busy:
                    continue
                if trust and not fillable(sig, fair, self.a.rest_max_gap):
                    continue
                self.do_rest_hedge(base, ks, sig)
        if trust and self.enabled("value"):
            for sig in value_takes(q, fair, self.a.edge_min):
                self.do_value(base, ks, sig)
        if trust and self.enabled("mm"):
            self.do_mm(base, ks, q, fair, model)

    # ---- strategies ----------------------------------------------------
    def do_taker_arb(self, base, ks, sig, kind="taker_arb", live_game=False):
        n = arb_shares(sig, self.free_capital(), self.a.max_pair_shares)
        n = self.fit(base, n, lambda k: [
            (sig["sell"], "sell", sig["sell_px"], k, taker_fee(sig["sell_px"], k)),
            (sig["buy"], "buy", sig["buy_px"], k, taker_fee(sig["buy_px"], k))])
        if n < 1:
            return
        self.say(f"    {kind.upper()} {sig['sell']:+.1f}/{sig['buy']:+.1f} credit "
                 f"{sig['credit']:+.4f} x{n}")
        if not self.a.live:
            self.ex.place(base, sig["sell"], ks[sig["sell"]], "sell", sig["sell_px"], n,
                          False, kind)
            self.ex.place(base, sig["buy"], ks[sig["buy"]], "buy", sig["buy_px"], n,
                          False, kind)
            return
        gid = self.ex.new_group(kind, base,
                                {"line": sig["sell"], "slug": ks[sig["sell"]],
                                 "side": "sell", "oids": []},
                                {"line": sig["buy"], "slug": ks[sig["buy"]],
                                 "side": "buy", "oids": []},
                                {"credit": sig["credit"], "hedge_px": sig["buy_px"]})
        g = self.s["groups"][gid]
        r = self.ex.place(base, sig["sell"], ks[sig["sell"]], "sell", sig["sell_px"],
                          n, False, kind, group=gid, leg="leader")
        if r:
            g["leader"]["oids"].append(r["oid"])
        # buys exactly what the sell filled; in-play there is no waiting for
        # a better hedge price - the book moves faster than a retry cycle
        self.ex.manage_groups(force_games={base} if live_game else ())

    def do_rest_hedge(self, base, ks, sig):
        # resting orders lock collateral whether or not they fill; cap them so
        # a slate of stale books cannot starve the trades that execute now
        room = self.a.rest_budget * self.a.capital - resting_capital(self.s)
        n = arb_shares(sig, min(self.free_capital(), room), self.a.max_pair_shares)
        if n < 1:
            return
        rs, rp, hl, hs, hp = (sig["rest_side"], sig["rest_px"], sig["hedge_line"],
                              sig["hedge_side"], sig["hedge_px"])
        def naked(k):
            return [(sig["rest_line"], rs, rp, k, -maker_rebate(rp, k))]
        n = self.fit(base, n, naked)                 # the leg can sit unhedged
        n = self.fit(base, n, lambda k: naked(k) + [(hl, hs, hp, k, taker_fee(hp, k))])
        if n < 1:
            return
        self.say(f"    REST-HEDGE rest {rs} {sig['rest_line']:+.1f} @ {rp:.3f}, "
                 f"hedge {hs} {hl:+.1f} @ ~{hp:.3f}  credit {sig['credit']:+.4f} x{n}")
        if not self.a.live:
            self.ex.place(base, sig["rest_line"], ks[sig["rest_line"]], rs, rp, n,
                          True, "rest_hedge")
            return
        gid = self.ex.new_group("rest_hedge", base,
                                {"line": sig["rest_line"], "slug": ks[sig["rest_line"]],
                                 "side": rs, "oids": []},
                                {"line": hl, "slug": ks[hl], "side": hs, "oids": []},
                                {"credit": sig["credit"], "hedge_px": hp,
                                 "min_credit": 0.0})
        r = self.ex.place(base, sig["rest_line"], ks[sig["rest_line"]], rs, rp, n,
                          True, "rest_hedge", group=gid, leg="leader")
        if r:
            self.s["groups"][gid]["leader"]["oids"].append(r["oid"])
        elif not self.ex.last_uncertain:
            self.s["groups"].pop(gid)      # cleanly rejected; uncertain -> reconcile

    def do_value(self, base, ks, sig):
        held = st.book_pos(st.book(self.s, base)).get(sig["line"], 0)
        if (held > 0 and sig["side"] == "buy") or (held < 0 and sig["side"] == "sell"):
            return            # already on; re-buying the same view each cycle compounds it
        bankroll = min(self.a.capital, self.free_capital() + locked_capital(self.s))
        n = value_shares(sig, bankroll, self.a.kelly, self.a.max_order, sig["depth"])
        per = sig["px"] if sig["side"] == "buy" else 1.0 - sig["px"]
        n = min(n, int(self.free_capital() / max(per, 0.01)))
        n = self.fit(base, n, lambda k: [(sig["line"], sig["side"], sig["px"], k,
                                          taker_fee(sig["px"], k))])
        if n < 1:
            return
        self.say(f"    VALUE {sig['side']} {sig['line']:+.1f} @ {sig['px']:.3f} "
                 f"fair {sig['fair']:.3f} edge {sig['edge']:+.3f} x{n}")
        self.ex.place(base, sig["line"], ks[sig["line"]], sig["side"], sig["px"], n,
                      False, "value", fair=round(sig["fair"], 4))

    def do_mm(self, base, ks, q, fair, model):
        from src.pm_us.greeks import book_risk
        pos = st.book_pos(st.book(self.s, base))
        nd = book_risk(pos, model.mu, model.sigma)[0]["delta"] if pos else 0.0
        want = mm_quotes(q, fair, self.a.mm_half_spread, skew_per_delta=0.002,
                         net_delta=nd, tick=self.a.tick)
        mine = self.s.setdefault("mm", {}).setdefault(base, {})
        for w in want:
            L = w["line"]
            slot = mine.setdefault(str(L), {})
            inv = pos.get(L, 0)
            for side, px in (("buy", w["bid"]), ("sell", w["ask"])):
                if (side == "buy" and inv >= self.a.mm_max_inv) or \
                        (side == "sell" and inv <= -self.a.mm_max_inv):
                    px = None
                rec = self.s["orders"].get(slot.get(side, ""))
                if rec and rec.get("status") == "open":
                    if px is not None and abs(rec["px"] - px) < 2 * self.a.tick:
                        continue
                    self.ex.cancel(rec)
                if px is None:
                    continue
                n = self.fit(base, self.a.mm_size,
                             lambda k: [(L, side, px, k, -maker_rebate(px, k))])
                if n < 1:
                    continue
                r = self.ex.place(base, L, ks[L], side, px, n, True, "mm",
                                  fair=round(w["fair"], 4))
                if r:
                    slot[side] = r["oid"]


    # ---- in-play ---------------------------------------------------------
    def inplay_loop(self, minutes):
        """Day-trade live games until `minutes` elapse or none are live."""
        import time as _t
        from src.income.inplay import Tracker
        self.lines.max_age = max(self.a.poll - 0.5, 1.0)
        self.tracker = Tracker()
        _load_tracker(self.tracker, self.s.get("tracker"))
        ladders, moneylines = inventory(self.c, self.say), moneyline_inventory(self.c)
        self.say(f"  in-play universe: {len(ladders)} ladders, {len(moneylines)} moneylines")
        end = _t.time() + minutes * 60.0
        polls = 0
        while _t.time() < end:
            t0 = _t.time()
            live = self.live_games(ladders, moneylines)
            if not live and not self.s.get("paper"):
                if polls == 0:
                    self.say("  in-play: no live games")
                break
            for base, (kind, ks, info) in live.items():
                self.inplay_game(base, kind, ks, info, t0)
            self.paper_sweep_dead(live, t0)
            self.ex.manage_groups(force_games=set(live))
            self.s["tracker"] = _dump_tracker(self.tracker)
            self.store.save(self.s)
            polls += 1
            _t.sleep(max(0.0, self.a.poll - (_t.time() - t0)))
        if polls:
            self.say(f"  in-play: {polls} polls, {len(self.s.get('paper', {}))} "
                     f"paper positions open")

    def live_games(self, ladders, moneylines):
        """Scoreboard-only pass to find what is live, then the pre-game line
        for just those. Only games dated around today can be live."""
        near = {_today_minus(1), _today_minus(0), _today_minus(-1)}
        out = {}
        for base, ks in ladders.items():
            if game_date(base) in near:
                info = self.lines.game(base, need_line=False)
                if info and info["state"] == "in":
                    out[base] = ("ladder", ks, self.lines.game(base))
        for slug in moneylines:
            if game_date(slug) in near:
                info = self.lines.game(slug, need_line=False)
                if info and info["state"] == "in":
                    out[slug] = ("moneyline", {0.0: slug}, self.lines.game(slug))
        return out

    def pregame(self, game, info):
        """The last PRE-game (mu, sigma): in-play, ESPN's pickcenter may show
        a live line, which would make the prior chase the market."""
        pg = self.s.setdefault("pregame", {})
        m = info.get("model")
        if info["state"] == "pre" and m:
            pg[game] = {"mu": m.mu, "sigma": m.sigma, "league": m.league}
        if game not in pg and m:
            pg[game] = {"mu": m.mu, "sigma": m.sigma, "league": m.league,
                        "from_live_pickcenter": True}
        return pg.get(game)

    def inplay_game(self, base, kind, ks, info, now):
        from src.income.inplay import live_model, tau_remaining
        league = info.get("league", "cfb")
        tau = tau_remaining(league, info.get("period"), info.get("clock"))
        pre = self.pregame(base, info)
        lm = None
        if pre and tau is not None and info.get("margin_now") is not None:
            lm = live_model(pre["mu"], pre["sigma"], info["margin_now"], tau,
                            pre.get("league", league))
        center = -lm.mu if lm else 0.0
        lines = sorted(sorted(ks, key=lambda k: abs(k - center))[: min(self.a.near, 14)])
        q = self.ex.quotes(ks, lines)
        if not q:
            return
        self.slugs[base] = {str(k): v for k, v in ks.items()}
        if kind == "ladder" and self.enabled("inplay_arb") and base not in \
                self.s.get("suspect_games", []):
            for sig in taker_arbs(q, self.a.min_credit):
                self.do_taker_arb(base, ks, sig, kind="inplay_arb", live_game=True)
        if lm is None or "divergence" not in self.a.strats or "divergence" in self.s["kills"]:
            return
        fair = {L: (lm.p_cover(L) if kind == "ladder" else lm.p_cover(0.0)) for L in q}
        self.tracker.observe_score(base, tuple(info.get("score") or ()), now)
        agree = model_agrees(q, fair, max_median_dev=0.15)[0] if len(q) >= 3 else \
            all(abs(fair[L] - (x["bid"] + x["ask"]) / 2) < 0.25 for L, x in q.items()
                if x["bid"] is not None and x["ask"] is not None)
        self.observe(base, q, fair, info, tau, now, agree)
        self.paper_exits(base, q, fair, tau, info["state"], now)
        if not agree:
            return                      # our score/clock is stale, or the model is wrong
        paper = self.s.setdefault("paper", {})
        for L, x in q.items():
            side = self.tracker.signal(base, L, fair[L], x["bid"], x["ask"], now)
            if side:
                from src.income.inplay import paper_open
                pos = paper_open(paper, base, L, side, x["bid"], x["ask"], fair[L], now)
                if pos:
                    self.say(f"    PAPER {side} {base[4:30]} {L:+.1f} @ {pos['px']:.3f} "
                             f"fair {fair[L]:.3f} | D {info['margin_now']:+d} tau {tau:.2f}")
                    self.store.log("paper_open", game=base, line=L, side=side,
                                   px=pos["px"], fair=fair[L], tau=tau,
                                   margin_now=info["margin_now"])

    def paper_exits(self, base, q, fair, tau, state, now):
        from src.income.inplay import paper_close, paper_exit_reason
        paper = self.s.get("paper", {})
        for key in [k for k, p in paper.items() if p["game"] == base]:
            pos = paper[key]
            x = q.get(pos["line"]) or {}
            pos["last_bid"], pos["last_ask"] = x.get("bid"), x.get("ask")
            why = paper_exit_reason(pos, x.get("bid"), x.get("ask"),
                                    fair.get(pos["line"], pos["fair"]), tau, now, state)
            if why:
                self._paper_close(key, pos, x.get("bid"), x.get("ask"), why)

    def paper_sweep_dead(self, live, now):
        """Close paper positions whose game is no longer live, at the last
        seen price. Flat by the whistle, even on paper."""
        for key, pos in list(self.s.get("paper", {}).items()):
            if pos["game"] not in live:
                self._paper_close(key, pos, pos.get("last_bid"), pos.get("last_ask"),
                                  "game_not_live")

    def _paper_close(self, key, pos, bid, ask, why):
        from src.income.inplay import paper_close
        pnl = paper_close(pos, bid, ask)
        if pnl is None and why not in ("game_not_live", "end_of_game"):
            return
        self.s["paper"].pop(key, None)
        self.store.log("paper_close", game=pos["game"], line=pos["line"],
                       side=pos["side"], entry=pos["px"], pnl=pnl, reason=why,
                       held_s=round(_now_s() - pos["t"], 1))
        self.say(f"    PAPER close {pos['game'][4:30]} {pos['line']:+.1f} {why} "
                 f"{'n/a' if pnl is None else f'{pnl:+.4f}'}/share")

    def observe(self, base, q, fair, info, tau, now, agree):
        """Raw material for the divergence study, sampled every 30s per game
        into its own file so it never crowds the trading ledger."""
        last = self._obs_t.get(base, 0.0) if hasattr(self, "_obs_t") else 0.0
        if not hasattr(self, "_obs_t"):
            self._obs_t = {}
        if now - last < 30.0:
            return
        self._obs_t[base] = now
        from src.pm_us.jsonlog import append
        append(INPLAY_OBS, {"ts": st.now_iso(), "game": base, "tau": tau,
                            "margin_now": info.get("margin_now"), "agree": agree,
                            "q": {str(L): [x["bid"], x["ask"], round(fair[L], 4)]
                                  for L, x in q.items()}})


def review(args):
    from src.income.measure import summary
    sm = summary(args.ledger)
    s = st.Store(args.state, args.ledger).load() if os.path.exists(args.state) else st.fresh()
    print("== income review ==")
    for k in ("orders", "fills", "rest_fill_rate", "fees_paid", "settled_games",
              "settled_pnl", "pulls", "orphans"):
        v = sm[k]
        print(f"  {k:<16} {v:.4f}" if isinstance(v, float) else f"  {k:<16} {v}")
    print("\n  edge metrics vs pre-registered kill rules:")
    for k, (min_n, desc) in KILL_RULES.items():
        m = sm[k]
        state = s["kills"].get(k, "live")
        print(f"  {k:<11} n={m['n']:>4}/{min_n:<4} mean {m['mean']:+.4f} "
              f"t {m['t']:+.2f}  [{state}]  rule: {desc}")
    from src.income.inplay import go_status
    from src.pm_us.jsonlog import iter_records
    trips = [(r["game"], r["pnl"], r["ts"]) for r in iter_records(args.ledger)
             if r.get("kind") == "paper_close" and r.get("pnl") is not None]
    ok, detail = go_status(trips)
    print(f"\n  divergence (PAPER) go/no-go: {'GO' if ok else 'not yet'}  {detail}")
    print(f"\n  realized ${s['realized']:+.2f} | open books "
          f"{len([g for g in s['books'] if g not in s['settled']])} | "
          f"groups {len(s['groups'])} | worst case "
          f"${sum(worst_case(st.book_pos(b), b['cash']) for g, b in s['books'].items() if g not in s['settled']):+.2f}")


def main(argv=None):
    a = parse(argv)
    if a.review:
        return review(a)
    os.makedirs("research", exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)   # dies with the process
    except BlockingIOError:
        print("another income_bot holds the lock; exiting")
        return 0
    if not os.environ.get("POLYMARKET_US_KEY_ID"):
        raise SystemExit("set POLYMARKET_US_KEY_ID / POLYMARKET_US_SECRET_KEY")
    store = st.Store(a.state, a.ledger)
    try:
        s = store.load(start_equity=a.capital)
    except st.CorruptState as e:
        print(f"!! {e}")
        return 2
    if a.reset_kill:
        s["kills"].pop(a.reset_kill, None)
        store.log("kill_reset", strategy=a.reset_kill)
    if a.clear_suspect:
        s["suspect_games"] = [g for g in s.get("suspect_games", []) if g != a.clear_suspect]
        store.log("suspect_cleared", game=a.clear_suspect)
    store.save(s)
    from src.income.fairvalue import LineSource
    from src.pm_us.client import UsClient
    c = UsClient()
    bot = Bot(a, c, store, s, LineSource())
    if a.settle:
        game, margin = a.settle.rsplit(":", 1)
        bot.ex.settle(game, int(margin))
    mode = "LIVE" if a.live else "dry"
    print(f"income_bot | {mode} | cap ${a.capital:.2f} | {st.now_iso()[:19]}")
    try:
        bot.manage()
        if not a.manage_only:
            bot.scan()
            bot.ex.manage_groups()
        if a.inplay_minutes > 0:
            bot.inplay_loop(a.inplay_minutes)
    finally:
        store.save(s)
        c.close()
    b = [bb for g, bb in s["books"].items() if g not in s["settled"]]
    print(f"  done | realized ${s['realized']:+.2f} | open games {len(b)} | "
          f"worst case ${sum(worst_case(st.book_pos(x), x['cash']) for x in b):+.2f} | "
          f"locked ${locked_capital(s):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
