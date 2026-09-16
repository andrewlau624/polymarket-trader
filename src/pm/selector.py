"""Two-stage market selection across the full reward universe.

Stage 1 (cheap, no book calls): filter the ~17k universe by pool size, time
to resolution and having a sane reward config, then take the biggest pools as
a shortlist.

Stage 2 (book calls, concurrent): for the shortlist, measure the *competing*
qualifying liquidity and your share, and rank by expected $/day. This is what
finds the uncontested tail instead of the crowded headline markets.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from src.pm import clob, rewards


def _hours_to_end(m):
    ed = m.get("end_date")
    if not ed:
        return None
    try:
        dt = datetime.fromisoformat(str(ed).replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).total_seconds() / 3600.0
    except ValueError:
        return None


def _estimate(m, size_mult, spread_frac, spread_cap_cents=3.0):
    """Estimate reward share *and* quote safely behind the best bid.

    The trap: in a wide book the midpoint is fiction, so quoting at mid-d puts
    you ABOVE the real bid -> you get filled and immediately mark at a loss.
    So we (a) refuse books wider than spread_cap, and (b) never bid above the
    existing best bid, while staying inside the reward band.
    """
    tok = m["tokens"][0]
    try:
        b = clob.get_book(tok["token_id"])
    except Exception:
        return None
    bids = clob._levels(b.get("bids"), "bid")
    asks = clob._levels(b.get("asks"), "ask")
    if not bids or not asks:
        return None
    best_bid, best_ask = bids[0][0], asks[0][0]
    spread = best_ask - best_bid
    if spread <= 0 or spread * 100.0 > spread_cap_cents:
        return None
    mid = (best_bid + best_ask) / 2.0
    ms = m["max_spread_cents"]
    min_size = max(m["min_size"], m["min_order_size"]) * size_mult
    if ms <= 0 or min_size <= 0:
        return None
    band = ms / 100.0
    tick = m["tick"]
    d = spread_frac * band
    # quote behind the book; must still sit inside the reward band
    quote_bid = min(round(mid - d, 3), round(best_bid - tick, 3))
    if quote_bid < mid - band or not (0.0 < quote_bid < 1.0):
        return None
    our = rewards.our_score(min_size, quote_bid, mid, ms)
    comp = sum(rewards.book_scores(bids, asks, mid, ms, min_size))
    share = rewards.estimate_share(our, comp)
    return {
        **m,
        "mid": mid,
        "quote_bid": quote_bid,
        "book_spread": spread,
        "share": share,
        "est_daily_usd": share * m["daily_rate"],
        "notional": min_size * mid,
        "depth": sum(s for _, s in asks[:5]),
    }


def select(universe, min_pool=5.0, min_hours=6.0, shortlist=600, max_markets=25,
           size_mult=1.0, spread_frac=0.9, workers=20, spread_cap_cents=3.0, log=print):
    stage1 = []
    for m in universe:
        if m["daily_rate"] < min_pool:
            continue
        if m["min_size"] <= 0 or m["max_spread_cents"] <= 0:
            continue
        h = _hours_to_end(m)
        if h is not None and h < min_hours:
            continue
        stage1.append(m)
    stage1.sort(key=lambda m: m["daily_rate"], reverse=True)
    short = stage1[:shortlist]
    log(f"universe {len(universe)} -> {len(stage1)} eligible -> book-scanning {len(short)}")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        rows = [r for r in ex.map(
            lambda m: _estimate(m, size_mult, spread_frac, spread_cap_cents), short) if r]
    rows.sort(key=lambda r: r["est_daily_usd"], reverse=True)
    return rows[:max_markets]
