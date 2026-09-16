"""Automatic discovery of structural no-arbitrage relations.

The LLM research loop kept proposing correct mechanisms (threshold
monotonicity, spreads implying moneylines) but supplied placeholder token
ids, so `check_relations` could never resolve them. These are the relations
that can be derived mechanically from the market metadata itself, so they
are discovered here instead of asked for.

Currently implemented: numeric threshold monotonicity inside one event.

    "BTC above $100k"  implies  "BTC above $90k"     (over/above ladders)
    "BTC below $90k"   implies  "BTC below $100k"    (under/below ladders)
"""

import re

_OVER_WORDS = ("greater than or equal to", "at least", "greater than", "higher than",
               "more than", "above", "over", "exceeds", "exceed")
_UNDER_WORDS = ("less than or equal to", "at most", "less than", "lower than",
                "below", "under")

# Words must be whole words (\b) so "Governor"/"Thunder" never match.
_KEYWORD = re.compile(
    r"\b(?P<word>"
    + "|".join(sorted(_OVER_WORDS + _UNDER_WORDS, key=len, reverse=True))
    + r")\b|(?P<sym>>=|<=|>|<)",
    re.I,
)
_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|k|m|b)?", re.I)
_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}


def _parse_threshold(question):
    """Return (kind, value, stem) or None.

    kind is 'over' or 'under'; value is the numeric threshold; stem is the
    question with the comparator+number removed, used to confirm two markets
    are about the same underlying quantity.
    """
    if not question:
        return None
    low = question.lower()
    best = None
    for kw in _KEYWORD.finditer(question):
        text = (kw.group("word") or kw.group("sym")).lower()
        kind = "over" if (kw.group("sym") in (">", ">=") or text in _OVER_WORDS) else "under"
        # the threshold must immediately follow the comparator
        tail = question[kw.end(): kw.end() + 24]
        m = _NUM.search(tail)
        if not m or m.start() > 6:
            continue
        value = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").lower()
        if unit in _MULT:
            value *= _MULT[unit]
        stem = question[: kw.start()] + " " + question[kw.end() + m.end():]
        stem = re.sub(r"\s+", " ", stem).strip().lower()
        best = (kind, value, stem)
        break
    return best


def discover_threshold_relations(markets, max_group=12):
    """Find monotonic threshold ladders within the same event.

    Emits 'implies' relations using real clob token ids.
    """
    by_event = {}
    for m in markets:
        if m.get("event_id") is None:
            continue
        parsed = _parse_threshold(m.get("question"))
        if not parsed:
            continue
        kind, value, stem = parsed
        by_event.setdefault((m["event_id"], kind, stem), []).append((value, m))

    relations = []
    for (event_id, kind, stem), members in by_event.items():
        if len(members) < 2 or len(members) > max_group:
            continue
        # dedupe thresholds (keep the most liquid market per threshold)
        members.sort(key=lambda x: (x[1].get("liquidity") or 0), reverse=True)
        uniq = {}
        for value, m in members:
            uniq.setdefault(value, m)
        if len(uniq) < 2:
            continue
        members = sorted(uniq.items())

        # Ascending thresholds v0 < v1 < v2 ...
        #   over ladder : "stat > v[i+1]" implies "stat > v[i]"
        #   under ladder: "stat < v[i]"   implies "stat < v[i+1]"
        for i in range(len(members) - 1):
            (lo_val, lo_m), (hi_val, hi_m) = members[i], members[i + 1]
            if kind == "over":
                antecedent, consequent = hi_m, lo_m
            else:
                antecedent, consequent = lo_m, hi_m
            a_yes = _yes_token(antecedent)
            b_yes = _yes_token(consequent)
            if not a_yes or not b_yes:
                continue
            relations.append({
                "type": "implies",
                "antecedent_token": a_yes,
                "consequent_token": b_yes,
                "condition_id": antecedent["condition_id"],
                "scope": "discovered",
                "note": f"{kind}: {antecedent['question'][:48]} => {consequent['question'][:48]}",
            })
    return relations


def _yes_token(market):
    for tok, out in zip(market["clob_token_ids"], market["outcomes"]):
        if out.lower() == "yes":
            return tok
    return None


def default_relations(markets):
    """All relation types the scanner can evaluate without an LLM."""
    rels = [
        {"type": "yes_no", "scope": "all", "note": "yes+no must sum to 1"},
        {"type": "event_exhaustive", "scope": "all", "note": "neg-risk set must sum to 1"},
    ]
    rels.extend(discover_threshold_relations(markets))
    return rels
