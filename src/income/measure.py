"""Did we have an edge? Answered from the ledger, in days, not seasons.

Settled P&L on a few dozen binary bets is mostly noise. Closing line value is
not: if the fills beat the last pre-game line, the bets were +EV whether or
not they won. It is the metric sharp bettors use, and the kill rule for the
value strategy is written in it.

  CLV (value)        closing_fair - px - fee   (buy)
                     px - closing_fair - fee   (sell)
  pair credit        what a rest-hedge actually locked, after the hedge's
                     real price - the number the "arb" should have been
                     judged on from the start
  markout (mm)       mid one cycle after the fill vs the fill price, plus
                     the rebate: negative means we are the ones picked off
"""

from src.income.model import MarginModel
from src.pm_us.fees import maker_rebate, taker_fee
from src.pm_us.jsonlog import iter_records


def closing_marks(records):
    """{(game, line): last pre-game book mid} - the market's own close."""
    last = {}
    for r in records:
        if r.get("kind") == "mark" and r.get("state", "pre") == "pre":
            last[(r["game"], float(r["line"]))] = r["mid"]
    return last


def closing_models(records):
    """{game: MarginModel} from the last pre-game fair snapshot of each game."""
    last = {}
    for r in records:
        if r.get("kind") == "fair" and r.get("state") == "pre":
            last[r["game"]] = r
    return {g: MarginModel(r["mu"], r["sigma"], r.get("league", "cfb"))
            for g, r in last.items()}


def closed_games(records):
    return {r["game"] for r in records
            if (r.get("kind") == "fair" and r.get("state") in ("in", "post"))
            or r.get("kind") == "settle"}


def metrics(path):
    """Two streaming passes; the ledger is never held in memory whole (an
    unbounded slurp is what OOM'd the droplet once)."""
    close = closing_marks(iter_records(path))
    done = closed_games(iter_records(path))
    marks = {}
    for r in iter_records(path):
        if r.get("kind") == "mark":
            marks.setdefault((r["game"], float(r["line"])), []).append((r["ts"], r["mid"]))
    out = {"value": [], "rest_hedge": [], "mm": [], "taker_arb": [], "inplay_arb": []}
    for r in iter_records(path):
        k = r.get("kind")
        # CLV against the LADDER's closing mid, not our own ESPN model: judging
        # the model by itself cannot catch a stale line - CLV would just echo
        # the entry edge back. One entry per share, so size is weighted.
        key = (r.get("game"), float(r["line"])) if "line" in r else None
        if k == "fill" and r.get("strat") == "value" and r["game"] in done \
                and key in close:
            p = close[key]
            fee = taker_fee(r["px"])
            clv = (p - r["px"] - fee) if r["side"] == "buy" else (r["px"] - p - fee)
            out["value"].extend([clv] * int(r.get("qty", 1)))
        elif k == "pair_done" and r.get("shares") and r.get("pair_kind") in out:
            out[r["pair_kind"]].append(r["credit_net"])
        elif k == "fill" and r.get("strat") == "mm":
            later = [m for ts, m in marks.get((r["game"], float(r["line"])), [])
                     if ts > r["ts"]]
            if later:
                mid = later[0]
                mo = (mid - r["px"]) if r["side"] == "buy" else (r["px"] - mid)
                out["mm"].append(mo + maker_rebate(r["px"]))
    return out


def summary(path):
    from src.income.risk import tstat
    recs = list(iter_records(path))
    m = metrics(path)
    fills = [r for r in recs if r.get("kind") == "fill"]
    settles = [r for r in recs if r.get("kind") == "settle"]
    orders = [r for r in recs if r.get("kind") == "order"]
    rest_orders = [r for r in orders if r.get("maker")]
    rest_filled = {r["oid"] for r in fills if r.get("maker")}
    out = {
        "orders": len(orders), "fills": len(fills),
        "rest_fill_rate": (len(rest_filled) / len(rest_orders)) if rest_orders else None,
        "fees_paid": sum(r.get("fee", 0) for r in fills),
        "settled_games": len(settles),
        "settled_pnl": sum(r.get("pnl", 0) for r in settles),
        "pulls": sum(1 for r in recs if r.get("kind") == "pull"),
        "orphans": sum(1 for r in recs if r.get("kind") == "orphan_intent"),
    }
    for k, xs in m.items():
        mu, t = tstat(xs)
        out[k] = {"n": len(xs), "mean": mu, "t": t}
    return out
