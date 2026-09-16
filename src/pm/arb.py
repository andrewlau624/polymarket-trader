"""Consistency-arbitrage detection on executable order-book prices.

Every violation is priced from what you can actually trade:
  - buying a leg costs the best *ask* (or the ask-ladder VWAP for size)
  - selling a leg earns the best *bid*
  - fees are charged per leg on $1 of payoff notional

Each opportunity is reported with its net edge and an estimated dollar
profit = edge * executable size, and results are ranked by profit so the
scanner surfaces the highest-value trades first.
"""


def _pair_tokens(market):
    tokens = market["clob_token_ids"]
    outcomes = market["outcomes"]
    yes_tok = None
    no_tok = None
    for tok, out in zip(tokens, outcomes):
        if out.lower() == "yes":
            yes_tok = tok
        elif out.lower() == "no":
            no_tok = tok
    return yes_tok, no_tok


def build_index(markets, prices):
    mid_by_token = {}
    bid_by_token = {}
    ask_by_token = {}
    bid_size_by_token = {}
    ask_size_by_token = {}
    for t in prices:
        tok = t.get("token_id")
        if tok is None:
            continue
        if t.get("price") is not None:
            mid_by_token[tok] = t["price"]
        if t.get("bid") is not None:
            bid_by_token[tok] = t["bid"]
        if t.get("ask") is not None:
            ask_by_token[tok] = t["ask"]
        if t.get("bid_size") is not None:
            bid_size_by_token[tok] = t["bid_size"]
        if t.get("ask_size") is not None:
            ask_size_by_token[tok] = t["ask_size"]

    pair_by_market = {}
    no_token_of = {}
    yes_token_of = {}
    market_by_condition = {}
    for m in markets:
        yes_tok, no_tok = _pair_tokens(m)
        if not yes_tok or not no_tok:
            continue
        market_by_condition[m["condition_id"]] = m
        yes_token_of[m["condition_id"]] = yes_tok
        no_token_of[yes_tok] = no_tok
        no_token_of[no_tok] = yes_tok
        pair_by_market[m["condition_id"]] = {
            "question": m["question"],
            "yes_token": yes_tok,
            "no_token": no_tok,
            "event_id": m.get("event_id"),
            "neg_risk": bool(m.get("neg_risk")),
            "neg_risk_group": m.get("neg_risk_market_id") or m.get("event_id"),
            "p_yes": mid_by_token.get(yes_tok),
            "p_no": mid_by_token.get(no_tok),
        }

    return {
        "mid": mid_by_token,
        "bid": bid_by_token,
        "ask": ask_by_token,
        "bid_size": bid_size_by_token,
        "ask_size": ask_size_by_token,
        "pair_by_market": pair_by_market,
        "no_token_of": no_token_of,
        "yes_token_of": yes_token_of,
        "market_by_condition": market_by_condition,
    }


def _cap(sizes):
    return min(sizes) if sizes else 0.0


def _opportunity(rel, rtype, label, kind, edge, size, legs, cost, requires, condition_id=None, event_id=None):
    size = float(size or 0.0)
    return {
        "relation": rel,
        "type": rtype,
        "kind": kind,
        "question": label,
        "condition_id": condition_id,
        "event_id": event_id,
        # edge/ROI are per $1 payoff after costs and fees
        "edge": edge,
        "roi": (edge / cost) if cost and cost > 0 else None,
        "size": size,
        "profit": edge * size,
        "capital": (cost or 0.0) * size,
        "legs": legs,
        "cost_per_unit": cost,
        "requires": requires,
    }


def check_relations(markets, prices, relations, min_edge=0.01, min_profit=0.0,
                    fee=0.0, exhaustive_tol=0.10):
    """Return executable inconsistencies ranked by estimated dollar profit.

    ``fee`` is charged per traded leg per $1 of payoff notional.

    ``exhaustive_tol`` guards the scope='all' exhaustive check: a set is only
    treated as a mutually-exclusive-and-exhaustive partition when the sum of
    its YES *midpoints* is within this tolerance of 1. A partial or
    non-exhaustive group (e.g. a subset of a big neg-risk event) has a mid-sum
    far from 1 and is skipped, which prevents bogus "buy everything at 0.001"
    trades.
    """
    idx = build_index(markets, prices)
    mid_price = idx["mid"]
    ask = idx["ask"]
    bid = idx["bid"]
    ask_size = idx["ask_size"]
    bid_size = idx["bid_size"]
    pair_by_market = idx["pair_by_market"]
    no_token_of = idx["no_token_of"]
    violations = []

    def emit(opp):
        if opp["edge"] > min_edge and opp["profit"] >= min_profit:
            violations.append(opp)

    for rel in relations:
        rtype = rel["type"]
        scope = rel.get("scope", "specific")

        if rtype == "yes_no":
            if scope == "all":
                targets = list(pair_by_market.items())
            else:
                cond = rel["condition_id"]
                pair = pair_by_market.get(cond)
                targets = [(cond, pair)] if pair else []

            for cond, pair in targets:
                if not pair:
                    continue
                y, n = pair["yes_token"], pair["no_token"]
                q = pair["question"]

                # Underpriced: buy both sides, merge the pair back into $1.
                if ask.get(y) is not None and ask.get(n) is not None:
                    cost = ask[y] + ask[n]
                    edge = 1.0 - cost - fee * 2
                    size = _cap([ask_size.get(y, 0.0), ask_size.get(n, 0.0)])
                    emit(_opportunity(
                        rel, rtype, q, "under", edge, size,
                        [{"token": y, "side": "buy", "price": ask[y]},
                         {"token": n, "side": "buy", "price": ask[n]}],
                        cost, "merge", condition_id=cond, event_id=pair.get("event_id"),
                    ))

                # Overpriced: split $1 into a pair, sell both sides.
                if bid.get(y) is not None and bid.get(n) is not None:
                    proceeds = bid[y] + bid[n]
                    edge = proceeds - 1.0 - fee * 2
                    size = _cap([bid_size.get(y, 0.0), bid_size.get(n, 0.0)])
                    emit(_opportunity(
                        rel, rtype, q, "over", edge, size,
                        [{"token": y, "side": "sell", "price": bid[y]},
                         {"token": n, "side": "sell", "price": bid[n]}],
                        proceeds, "split", condition_id=cond, event_id=pair.get("event_id"),
                    ))

        elif rtype == "implies":
            # a implies b  =>  P(a) <= P(b).
            # Risk-free executable form: BUY b (YES) + BUY NO(a). Payoff is >= 1
            # in every state, so it is an arb when the combined cost < 1.
            a = rel["antecedent_token"]
            b = rel["consequent_token"]
            no_a = no_token_of.get(a)
            if no_a is None or ask.get(b) is None or ask.get(no_a) is None:
                continue
            cost = ask[b] + ask[no_a]
            edge = 1.0 - cost - fee * 2
            size = _cap([ask_size.get(b, 0.0), ask_size.get(no_a, 0.0)])
            emit(_opportunity(
                rel, rtype, rel.get("note", ""), "under", edge, size,
                [{"token": b, "side": "buy", "price": ask[b]},
                 {"token": no_a, "side": "buy", "price": ask[no_a]}],
                cost, "implies", condition_id=rel.get("condition_id"),
            ))

        elif rtype == "event_exhaustive":
            if scope == "all":
                groups = {}
                for cond, pair in pair_by_market.items():
                    if not pair.get("neg_risk"):
                        # Only neg-risk groups are guaranteed mutually
                        # exclusive AND exhaustive; anything else risks
                        # overcounting overlapping/none-of-the-above markets.
                        continue
                    groups.setdefault(pair.get("neg_risk_group"), []).append(pair)
                members_by_group = groups.values()
            else:
                tokens = rel.get("tokens") or []
                if not tokens:
                    continue
                members_by_group = [[
                    {"question": rel.get("note", ""),
                     "yes_token": t,
                     "condition_id": None,
                     "event_id": None,
                     "neg_risk": True}
                    for t in tokens
                ]]

            for members in members_by_group:
                if len(members) < 2:
                    continue
                yes_tokens = [m["yes_token"] for m in members]
                label = f"event {members[0].get('event_id')}" if scope == "all" else rel.get("note", "")

                # Completeness / exhaustiveness gate: only a set whose midpoints
                # already sum to ~1 is a plausible exhaustive partition.
                if scope == "all":
                    mids = [mid_price.get(t) for t in yes_tokens]
                    if any(m is None for m in mids):
                        continue
                    if abs(sum(mids) - 1.0) > exhaustive_tol:
                        continue

                # Underpriced complete set: buy YES on every candidate.
                if all(ask.get(t) is not None for t in yes_tokens):
                    total_ask = sum(ask[t] for t in yes_tokens)
                    edge = 1.0 - total_ask - fee * len(yes_tokens)
                    size = _cap([ask_size.get(t, 0.0) for t in yes_tokens])
                    emit(_opportunity(
                        rel, rtype, label, "under", edge, size,
                        [{"token": t, "side": "buy", "price": ask[t]} for t in yes_tokens],
                        total_ask, "mint_set",
                        condition_id=members[0].get("condition_id"),
                        event_id=members[0].get("event_id"),
                    ))

                # Overpriced complete set: mint the set for $1, sell every YES.
                if all(bid.get(t) is not None for t in yes_tokens):
                    total_bid = sum(bid[t] for t in yes_tokens)
                    edge = total_bid - 1.0 - fee * len(yes_tokens)
                    size = _cap([bid_size.get(t, 0.0) for t in yes_tokens])
                    emit(_opportunity(
                        rel, rtype, label, "over", edge, size,
                        [{"token": t, "side": "sell", "price": bid[t]} for t in yes_tokens],
                        total_bid, "mint_set",
                        condition_id=members[0].get("condition_id"),
                        event_id=members[0].get("event_id"),
                    ))

    violations.sort(key=lambda v: v["profit"], reverse=True)
    return violations


def total_profit(violations):
    return sum(v["profit"] for v in violations)


def verify_relation_on_resolved(rel, markets, prices):
    idx = build_index(markets, prices)
    resolved = {t["token_id"]: t.get("price") for t in prices}
    pair_by_market = idx["pair_by_market"]
    ok = True
    detail = {}

    if rel["type"] == "yes_no":
        pair = pair_by_market.get(rel.get("condition_id"))
        if not pair:
            return None, "no pair"
        py = resolved.get(pair["yes_token"])
        pn = resolved.get(pair["no_token"])
        ok = (py is not None and pn is not None and abs(py + pn - 1.0) < 1e-6)
        detail = {"p_yes": py, "p_no": pn}

    elif rel["type"] == "implies":
        pa = resolved.get(rel.get("antecedent_token"))
        pb = resolved.get(rel.get("consequent_token"))
        if pa is None or pb is None:
            return None, "missing tokens"
        ok = not (pa > 0.5 and pb < 0.5)
        detail = {"antecedent": pa, "consequent": pb}

    elif rel["type"] == "event_exhaustive":
        vals = [resolved.get(t) for t in rel.get("tokens", [])]
        if any(v is None for v in vals):
            return None, "missing tokens"
        ok = abs(sum(vals) - 1.0) < 1e-6
        detail = {"values": vals}

    return ok, detail
