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


def _estimate(m, size_mult, spread_frac):
    tok = m["tokens"][0]
    try:
        b = clob.get_book(tok["token_id"])
    except Exception:
        return None
    bids = clob._levels(b.get("bids"), "bid")
    asks = clob._levels(b.get("asks"), "ask")
    if not bids or not asks:
        return None
    mid = (bids[0][0] + asks[0][0]) / 2.0
    ms = m["max_spread_cents"]
    min_size = max(m["min_size"], m["min_order_size"]) * size_mult
    if ms <= 0 or min_size <= 0:
        return None
    d = spread_frac * ms / 100.0
    bid = min(round(mid - d, 3), round(mid - m["tick"], 3))
    if not (0.0 < bid < 1.0):
        return None
    our = rewards.our_score(min_size, bid, mid, ms)
    comp = sum(rewards.book_scores(bids, asks, mid, ms, min_size))
    share = rewards.estimate_share(our, comp)
    depth = sum(s for _, s in asks[:5])
    return {
        **m,
        "mid": mid,
        "share": share,
        "est_daily_usd": share * m["daily_rate"],
        "notional": min_size * mid,
        "depth": depth,
    }


def select(universe, min_pool=5.0, min_hours=6.0, shortlist=600, max_markets=25,
           size_mult=1.0, spread_frac=0.9, workers=20, log=print):
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
        rows = [r for r in ex.map(lambda m: _estimate(m, size_mult, spread_frac), short) if r]
    rows.sort(key=lambda r: r["est_daily_usd"], reverse=True)
    return rows[:max_markets]
