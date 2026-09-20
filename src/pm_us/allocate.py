"""Spread capital across every opportunity, instead of filling the best few.

The bot allocated greedily: sort by edge, take the best until capital ran out.
That is right when capacity per opportunity is unlimited and wrong when it is
not. Here each resting position only absorbs so much flow, so greedy allocation
piles into three positions, leaves 247 untouched, and the return stops scaling
the moment the first few are full.

Measured: taking the touch caps at ~20 shares a violation and fees make every
deeper level negative, which is exactly the $50 -> $100 halving in the capacity
curve. Resting has no depth limit, so the binding constraint becomes how many
PLACES you are resting - breadth, not size.

So: allocate a base size everywhere that clears the bar, then give the surplus
to the best ones. Return then grows with the number of opportunities, which
grows with the number of ladders, which is 50 and rising rather than 25 by
configuration.
"""


def plan(candidates, capital, base_shares=5, max_shares=60, min_shares=1,
         reserve=0.05):
    """Size every candidate. candidates: dicts with 'capital' and 'ev' per share.

    Two passes. First a base allocation to everything worth doing, so breadth
    is guaranteed rather than whatever is left over. Then the surplus goes to
    the best edges, capped so no single position dominates.
    """
    if not candidates:
        return []
    budget = max(capital * (1.0 - reserve), 0.0)
    ranked = sorted(candidates, key=lambda c: -c.get("ev", 0.0))

    out, spent = [], 0.0
    # pass 1: breadth
    for c in ranked:
        per = max(c.get("capital", 1.0), 0.01)
        want = min(base_shares, int(max(budget - spent, 0) / per))
        if want < min_shares:
            continue
        out.append({**c, "shares": want})
        spent += want * per

    # pass 2: depth on the best, in EV order
    for row in out:
        if spent >= budget:
            break
        per = max(row.get("capital", 1.0), 0.01)
        room = int((budget - spent) / per)
        add = min(room, max_shares - row["shares"])
        if add > 0:
            row["shares"] += add
            spent += add * per

    for row in out:
        row["dollars"] = row["shares"] * max(row.get("capital", 1.0), 0.01)
        row["expected"] = row["shares"] * row.get("ev", 0.0)
    return out


def summarise(plan_rows, capital):
    n = len(plan_rows)
    spent = sum(r["dollars"] for r in plan_rows)
    exp = sum(r["expected"] for r in plan_rows)
    return {
        "positions": n,
        "deployed": spent,
        "expected": exp,
        "utilisation": spent / capital if capital else 0.0,
        "return_pct": exp / spent if spent else 0.0,
    }
