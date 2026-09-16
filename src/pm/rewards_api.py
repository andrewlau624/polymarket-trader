"""Rewards data: the cached full sampling universe + live credit reads.

The sampling universe is ~17k markets and barely changes minute to minute, so
it's cached to disk (TTL). Live credits / scoring need CLOB auth and only work
with a funded wallet.
"""

import json
import os
import time

import requests

CLOB_BASE = "https://clob.polymarket.com"
CACHE = os.path.join("data", "pm_sampling.json")


def _slim(m):
    rw = m.get("rewards") or {}
    rates = rw.get("rates") or []
    if not rates:
        return None
    pool = sum(float(x.get("rewards_daily_rate") or 0) for x in rates)
    if pool <= 0:
        return None
    toks = m.get("tokens") or []
    if len(toks) < 2:
        return None
    return {
        "condition_id": m.get("condition_id"),
        "question": m.get("question") or "",
        "daily_rate": pool,
        "min_size": float(rw.get("min_size") or 0),
        "max_spread_cents": float(rw.get("max_spread") or 0),
        "tick": float(m.get("minimum_tick_size") or 0.001),
        "min_order_size": float(m.get("minimum_order_size") or 0),
        "end_date": m.get("end_date_iso"),
        "neg_risk": bool(m.get("neg_risk")),
        "tokens": [
            {"token_id": t.get("token_id"), "outcome": t.get("outcome")}
            for t in toks[:2]
        ],
    }


def sampling_universe(ttl=1800, refresh=False, verbose=False):
    """Full list of reward markets, slimmed and cached for ``ttl`` seconds."""
    if not refresh and os.path.exists(CACHE):
        try:
            blob = json.load(open(CACHE))
            if time.time() - blob.get("ts", 0) < ttl and blob.get("markets"):
                return blob["markets"]
        except (ValueError, OSError):
            pass

    out, cursor = [], ""
    for _ in range(60):
        r = requests.get(f"{CLOB_BASE}/sampling-markets",
                         params={"next_cursor": cursor}, timeout=30)
        r.raise_for_status()
        j = r.json()
        data = j.get("data", [])
        for m in data:
            s = _slim(m)
            if s:
                out.append(s)
        cursor = j.get("next_cursor", "")
        if not data or not cursor or cursor == "LTE=":
            break
    if out:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w") as fh:
            json.dump({"ts": time.time(), "markets": out}, fh)
    if verbose:
        print(f"  sampling universe: {len(out)} reward markets")
    return out


# --- live (authenticated) reads -------------------------------------------

def _headers(client, method, path):
    from py_clob_client.clob_types import RequestArgs
    from py_clob_client.headers.headers import create_level_2_headers
    return create_level_2_headers(
        client.signer, client.creds, RequestArgs(method=method, request_path=path)
    )


def earnings(client, date=None, host=CLOB_BASE, timeout=15):
    """Real liquidity-rewards credits for the authenticated wallet.

    Returns a dict (shape is Polymarket's) or None if unavailable. Tries the
    known reward endpoints so it degrades gracefully across API versions.
    """
    paths = ["/rewards/user", "/rewards/user/total", "/rewards/user/percentages"]
    for path in paths:
        q = f"{path}?date={date}" if date else path
        try:
            h = _headers(client, "GET", q)
            r = requests.get(f"{host}{q}", headers=h, timeout=timeout)
            if r.status_code == 200:
                return {"endpoint": q, "data": r.json()}
        except Exception:
            continue
    return None


def scoring_orders(client, order_ids):
    """Which of our resting orders currently qualify for rewards."""
    from py_clob_client.clob_types import OrderScoringParams
    out = {}
    for oid in order_ids:
        try:
            out[oid] = client.is_order_scoring(OrderScoringParams(orderId=oid))
        except Exception:
            out[oid] = None
    return out
