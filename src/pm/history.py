"""Historical Polymarket data for event backtesting.

Price history is reconstructed from the public trade tape (data-api /trades),
which is the only reliable per-minute source for resolved markets. Each
market's resolution comes from gamma's outcomePrices once closed.
"""

import json
import os
import re
import time

import numpy as np
import pandas as pd
import requests

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
TRADE_CACHE = os.path.join("data", "pm_trades")

CATEGORY_TAGS = {
    "LoL": 65,
    "CS2": 100677,
    "Dota2": 100443,
    "Valorant": 101672,
    "Esports": 64,
    "Politics": 2,
    "Crypto": 21,
    "Sports": 1,
}

# single-match events end with an ISO date in the slug, e.g. lol-t1-kt-2025-11-09
_MATCH_SLUG = re.compile(r".+-\d{4}-\d{2}-\d{2}$")


def is_match_event(event):
    slug = event.get("slug") or ""
    return bool(_MATCH_SLUG.match(slug))


def match_markets(tag_id, max_events=200, max_markets_per_event=4):
    """Only per-match events (fast resolving), not season futures."""
    out = []
    for ev in events_by_tag(tag_id, closed=True, max_events=max_events):
        if not is_match_event(ev):
            continue
        for m in binary_markets(ev)[:max_markets_per_event]:
            out.append((ev, m))
    return out


def build_market(category, m, event=None):
    res = _resolution(m)
    if not res:
        return None
    try:
        trades = fetch_trades(m["conditionId"])
    except Exception:
        return None
    if trades is None:
        return None
    bars = {}
    for tok in res["tokens"]:
        b = price_frame(trades, tok)
        if b is not None and len(b) >= 20:
            bars[tok] = b
    if len(bars) < 2:
        return None
    end = (m.get("endDate") or (event or {}).get("endDate") or "")[:10]
    return {
        "category": category,
        "condition_id": m["conditionId"],
        "question": (m.get("question") or "")[:70],
        "end_date": end,
        "win_token": res["win_token"],
        "tokens": res["tokens"],
        "outcomes": res["outcomes"],
        "bars": bars,
    }


def _get(url, params, timeout=25, retries=2):
    last = None
    for _ in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(0.5)
    raise last


def events_by_tag(tag_id, closed=True, max_events=60):
    out, offset = [], 0
    while len(out) < max_events:
        evs = _get(
            f"{GAMMA}/events",
            {
                "tag_id": tag_id,
                "closed": str(closed).lower(),
                "limit": min(100, max_events - len(out)),
                "offset": offset,
                "order": "endDate",
                "ascending": "false",
            },
        )
        if not evs:
            break
        out.extend(evs)
        offset += len(evs)
        if len(evs) < 100:
            break
    return out[:max_events]


def binary_markets(event):
    out = []
    for m in event.get("markets", []):
        try:
            outs = json.loads(m.get("outcomes") or "[]")
        except (TypeError, ValueError):
            continue
        if len(outs) == 2 and m.get("conditionId") and not m.get("closed") is False:
            out.append(m)
    return out


def _resolution(m):
    try:
        prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
        toks = json.loads(m.get("clobTokenIds") or "[]")
        outs = json.loads(m.get("outcomes") or "[]")
    except (TypeError, ValueError):
        return None
    if len(prices) != len(toks) or 1.0 not in prices:
        return None
    idx = prices.index(1.0)
    return {"win_token": toks[idx], "win_outcome": outs[idx], "tokens": toks, "outcomes": outs}


def fetch_trades(condition_id, max_trades=8000, refresh=False):
    os.makedirs(TRADE_CACHE, exist_ok=True)
    path = os.path.join(TRADE_CACHE, f"{condition_id}.csv")
    if os.path.exists(path) and not refresh:
        return pd.read_csv(path)

    rows, offset = [], 0
    while len(rows) < max_trades:
        batch = _get(f"{DATA}/trades", {"market": condition_id, "limit": 500, "offset": offset})
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        if len(batch) < 500:
            break
    if not rows:
        return None
    df = pd.DataFrame(rows)
    keep = df[["timestamp", "price", "size", "side", "asset", "outcome"]].copy()
    keep.to_csv(path, index=False)
    return keep


def price_frame(trades, token_id, freq="1min"):
    """Resample one token's trades into OHLC-ish bars with signed flow."""
    if trades is None or len(trades) == 0:
        return None
    sub = trades[trades["asset"].astype(str) == str(token_id)]
    if len(sub) < 20:
        return None
    ts = pd.to_datetime(sub["timestamp"].to_numpy(), unit="s", utc=True).tz_localize(None)
    df = pd.DataFrame(
        {
            "ts": ts,
            "price": sub["price"].to_numpy(dtype=float),
            "size": sub["size"].to_numpy(dtype=float),
            "sign": np.where(sub["side"].to_numpy() == "BUY", 1.0, -1.0),
        }
    ).sort_values("ts")
    df["signed"] = df["size"] * df["sign"]
    g = df.set_index("ts").resample(freq)
    bars = pd.DataFrame(
        {
            "price": g["price"].last(),
            "volume": g["size"].sum(),
            "signed": g["signed"].sum(),
        }
    )
    bars["price"] = bars["price"].ffill()
    bars = bars.dropna(subset=["price"])
    bars["ret1"] = bars["price"].pct_change()
    for w in (3, 5, 15, 30, 60):
        bars[f"ret{w}"] = bars["price"].pct_change(w)
        vol = bars["volume"].rolling(w).sum().replace(0, np.nan)
        bars[f"flow{w}"] = bars["signed"].rolling(w).sum() / vol
    return bars


def market_dataset(category, tag_id, max_events=40, max_markets_per_event=3):
    """Yield dicts of {category, condition_id, question, bars_by_token, win_token}."""
    out = []
    for ev in events_by_tag(tag_id, closed=True, max_events=max_events):
        for m in binary_markets(ev)[:max_markets_per_event]:
            res = _resolution(m)
            if not res:
                continue
            trades = None
            try:
                trades = fetch_trades(m["conditionId"])
            except Exception:
                continue
            if trades is None:
                continue
            bars = {}
            for tok in res["tokens"]:
                b = price_frame(trades, tok)
                if b is not None and len(b) >= 30:
                    bars[tok] = b
            if len(bars) < 2:
                continue
            out.append(
                {
                    "category": category,
                    "condition_id": m["conditionId"],
                    "question": (m.get("question") or "")[:70],
                    "win_token": res["win_token"],
                    "tokens": res["tokens"],
                    "outcomes": res["outcomes"],
                    "bars": bars,
                }
            )
    return out
