"""Reconstruct an esports series (Bo3/Bo5) from the cached Polymarket tape.

An event is not one market - it is a linked SYSTEM: a match winner, a winner
market per map, and a maps-total over/under. Those are bound together by
probability axioms, with no model and no independence assumption:

  PRE-SERIES, with a = P(A wins map1), b = P(A wins map2):
      P(total > 2.5) = P(maps 1 and 2 split)
      |a - b|  <=  P(total > 2.5)  <=  min(a + b, 2 - a - b)

  AFTER MAP 1 RESOLVES, it tightens to an EQUALITY. If A took map 1, the
  series reaches map 3 iff B takes map 2:
      P(total > 2.5) + P(A wins map 2) = 1

That second one is the interesting one. It holds exactly, it holds during the
match, and it is at its most fragile in the seconds after a map ends - which is
precisely when esports prices move hardest.
"""

import os
import re

TAPE = os.path.join("data", "pm_trades")

MAP_RE = re.compile(r"\b(?:map|game)\s*(\d+)\s*winner", re.I)
TOTAL_RE = re.compile(r"games?\s*total|o/u|over/under", re.I)
BO_RE = re.compile(r"\(bo(\d)\)", re.I)


def role_of(question):
    """'map1' | 'map2' | ... | 'total' | 'match' for one market question."""
    q = question or ""
    m = MAP_RE.search(q)
    if m:
        return f"map{int(m.group(1))}"
    if TOTAL_RE.search(q):
        return "total"
    return "match"


def series_format(questions):
    """Bo3 / Bo5 from any '(BO3)' tag, else inferred from the highest map."""
    for q in questions:
        m = BO_RE.search(q or "")
        if m:
            return int(m.group(1))
    highest = 0
    for q in questions:
        m = MAP_RE.search(q or "")
        if m:
            highest = max(highest, int(m.group(1)))
    if highest >= 4:
        return 5
    return 3 if highest else 0


def split_bounds(a, b):
    """(low, high) for P(maps 1 and 2 split), given P(A wins) on each map."""
    return abs(a - b), min(a + b, 2.0 - a - b)


def load_tape(condition_id, tape=TAPE):
    import pandas as pd
    path = os.path.join(tape, f"{condition_id}.csv")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    if len(df) < 10 or "outcome" not in df.columns:
        return None
    return df.sort_values("timestamp")


TEAMS_RE = re.compile(r":\s*(.+?)\s+vs\.?\s+(.+?)\s*(?:\(|-|$)", re.I)


def teams_of(question):
    """('Shifters', 'Karmine Corp') from 'LoL: Shifters vs Karmine Corp - ...'."""
    m = TEAMS_RE.search(question or "")
    if not m:
        return None, None
    return m.group(1).strip(), m.group(2).strip()


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def pick_leg(outcomes, want):
    """The outcome naming `want`, matched loosely. None if it is not there.

    Esports markets label their legs with TEAM NAMES, not Yes/No, and the order
    is NOT consistent across markets in the same event: one event had map1 as
    ['Shifters','Karmine Corp'] and map2 as ['Karmine Corp','Shifters']. Taking
    outcomes[0] therefore silently compares two different teams, which is what
    manufactured a batch of impossible 'violations'.
    """
    if not want:
        return None
    w = _norm(want)
    for o in outcomes:
        if _norm(o) == w:
            return o
    for o in outcomes:
        n = _norm(o)
        if n and (n in w or w in n):
            return o
    return None


def leg_series(df, leg, bar_s=60):
    """Size-weighted price of one named leg, per bar. Index is epoch seconds."""
    import pandas as pd
    if leg is None:
        return None
    s = df[df["outcome"] == leg].copy()
    if len(s) < 8:
        return None
    s["bucket"] = (s["timestamp"] // bar_s) * bar_s
    s["w"] = s["size"].abs().clip(lower=1e-9)
    s["pw"] = s["price"] * s["w"]
    g = s.groupby("bucket").agg({"pw": "sum", "w": "sum"})
    g = g[g.w > 0]
    return (g.pw / g.w) if len(g) else None


def resolution(df, leg=None):
    """1.0 / 0.0 for the named leg if the tape ends decisively, else None."""
    outs = list(df["outcome"].dropna().unique())
    if len(outs) != 2:
        return None
    term = {}
    for o in outs:
        s = df[df["outcome"] == o]
        if len(s) < 3:
            return None
        term[o] = float(s["price"].tail(3).mean())
    hi = max(term, key=term.get)
    lo = min(term, key=term.get)
    if term[hi] < 0.95 or term[lo] > 0.05:
        return None
    ref = leg if leg is not None else ("Yes" if "Yes" in outs else outs[0])
    return 1.0 if hi == ref else 0.0


def grid(series_map, bar_s=60, require=None, max_stale_s=300):
    """Align several leg-series onto one time grid, forward-filled.

    Requiring a trade in every market in the SAME bar left 17 usable
    observations out of 257 events - these books are thin and do not print
    simultaneously. A price holds until the next trade, so forward-filling onto
    a shared grid is both correct and the difference between no sample and a
    real one.
    """
    import pandas as pd
    live = {k: v for k, v in series_map.items() if v is not None and len(v)}
    # a dropped column used to surface as a KeyError in the caller; say no
    # instead, so the caller can count WHY an event was unusable
    if require and any(k not in live for k in require):
        return None
    if len(live) < 2:
        return None
    lo = max(min(v.index) for v in live.values())
    hi = min(max(v.index) for v in live.values())
    if hi <= lo:
        return None
    idx = range(int(lo), int(hi) + 1, bar_s)
    out = pd.DataFrame(index=list(idx))
    limit = max(1, int(max_stale_s // bar_s))
    for k, v in live.items():
        # Cap the carry. An unlimited ffill turned one two-hour-old print on an
        # illiquid amateur match into 60 identical "violations". A price that
        # old is not a price.
        out[k] = (v.reindex(out.index.union(v.index)).sort_index()
                   .ffill(limit=limit).reindex(out.index))
    return out.dropna()
