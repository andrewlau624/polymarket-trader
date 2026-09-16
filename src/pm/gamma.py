import json

import requests

GAMMA_BASE = "https://gamma-api.polymarket.com"
TIMEOUT = 15


def _get(path, params=None):
    r = requests.get(f"{GAMMA_BASE}{path}", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return []


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_market(m):
    outcomes = _as_list(m.get("outcomes"))
    token_ids = _as_list(m.get("clobTokenIds"))
    prices = _as_list(m.get("outcomePrices"))
    return {
        "condition_id": m.get("conditionId"),
        "event_id": m.get("eventId"),
        "slug": m.get("slug"),
        "question": m.get("question"),
        "description": m.get("description"),
        "outcomes": [str(o) for o in outcomes],
        "clob_token_ids": [str(t) for t in token_ids],
        "outcome_prices": [_to_float(p) for p in prices],
        "end_date": m.get("endDate"),
        "closed": bool(m.get("closed")),
        "active": bool(m.get("active")),
        "accepting_orders": bool(m.get("acceptingOrders")),
        "volume": _to_float(m.get("volume")),
        "liquidity": _to_float(m.get("liquidity")),
        "best_bid": _to_float(m.get("bestBid")),
        "best_ask": _to_float(m.get("bestAsk")),
        "last_trade_price": _to_float(m.get("lastTradePrice")),
        "neg_risk": bool(m.get("negRisk")),
        "neg_risk_market_id": m.get("negRiskMarketID"),
        "group_item_title": m.get("groupItemTitle"),
        "spread": _to_float(m.get("spread")),
        "order_min_size": _to_float(m.get("orderMinSize")),
        "tick_size": _to_float(m.get("orderPriceMinTickSize")),
    }


def get_markets(params=None):
    params = params or {}
    data = _get("/markets", params)
    return [normalize_market(m) for m in data]


def get_events(params=None):
    params = params or {}
    data = _get("/events", params)
    out = []
    for ev in data:
        markets = []
        for m in ev.get("markets", []):
            mk = normalize_market(m)
            if mk.get("event_id") is None:
                mk["event_id"] = ev.get("id")
            markets.append(mk)
        out.append(
            {
                "event_id": ev.get("id"),
                "title": ev.get("title"),
                "slug": ev.get("slug"),
                "description": ev.get("description"),
                "neg_risk": bool(ev.get("negRisk")),
                "markets": markets,
            }
        )
    return out


def search(q, limit=50):
    data = _get("/public-search", params={"q": q, "limit": limit})
    return [normalize_market(m) for m in data]