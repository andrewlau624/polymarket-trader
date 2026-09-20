"""What does this venue actually charge? Measure it; do not assume it.

charanchivukula/polymarket-trading-infra publishes Polymarket's fee curve as

    fee = 0.07 * p * (1 - p)   per share

and reports live losses that were "mostly fees" against a backtest that
modelled none. At p ~ 0.58 that is 0.0171 a share, so a two-leg ladder pair
pays ~0.034 - against credits of 0.025 to 0.040. If it applies here, the
entire monotonicity trade is at best break-even and mostly negative.

Our own fills hint at it: selling 7 clmsn-cah at 0.550 against a 0.462 average
should have realised $0.616 and realised $0.440, a gap of 0.0251 a share. Same
order of magnitude as the model's 0.0173, which is far too close to ignore.

So probe for a fills endpoint and read the fee field, rather than inferring it
from P&L arithmetic that FIFO accounting can distort. Guessing at this venue's
semantics has been wrong six times in this project; this one decides whether
anything should trade at all.

    python run_fees.py            # find fills, report implied fee per share
    python run_fees.py --probe    # just show which endpoints exist
"""

import argparse
import json


def try_call(label, fn):
    try:
        out = fn()
        n = len(out) if hasattr(out, "__len__") else "?"
        print(f"  OK    {label:<34} -> {type(out).__name__}, n={n}")
        return out
    except Exception as e:
        print(f"  --    {label:<34} {type(e).__name__}: {str(e)[:56]}")
        return None


def main():
    ap = argparse.ArgumentParser(description="Measure the venue's fees.")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    from src.pm_us.client import UsClient
    c = UsClient()
    sdk = c.c

    print("probing for a fills / trades endpoint:")
    cands = {
        "orders.list()": lambda: sdk.orders.list(),
        "orders.list(status=filled)": lambda: sdk.orders.list({"status": "filled"}),
        "trades.list()": lambda: sdk.trades.list(),
        "portfolio.trades()": lambda: sdk.portfolio.trades(),
        "portfolio.fills()": lambda: sdk.portfolio.fills(),
        "portfolio.activity()": lambda: sdk.portfolio.activity(),
        "account.transactions()": lambda: sdk.account.transactions(),
        "GET /v1/trades": lambda: sdk.get("/v1/trades", authenticated=True),
        "GET /v1/fills": lambda: sdk.get("/v1/fills", authenticated=True),
        "GET /v1/orders/fills": lambda: sdk.get("/v1/orders/fills", authenticated=True),
        "GET /v1/portfolio/trades": lambda: sdk.get("/v1/portfolio/trades",
                                                    authenticated=True),
        "GET /v1/account/activity": lambda: sdk.get("/v1/account/activity",
                                                    authenticated=True),
    }
    found = {}
    for label, fn in cands.items():
        out = try_call(label, fn)
        if out:
            found[label] = out

    if not found:
        print("\nno fills endpoint responded. Fees can still be settled by the")
        print("Thursday settlement: a correct pair realises the credit exactly,")
        print("so any shortfall IS the fee.")
        c.close()
        return

    # look for a fee field anywhere in the payloads
    print("\nsearching the payloads for fee fields:")
    for label, out in found.items():
        rows = out
        if isinstance(out, dict):
            for k in ("trades", "fills", "orders", "activity", "data", "items"):
                if isinstance(out.get(k), list):
                    rows = out[k]
                    break
        if not isinstance(rows, list) or not rows:
            continue
        keys = sorted({k for r in rows if isinstance(r, dict) for k in r})
        feeish = [k for k in keys if any(w in k.lower()
                                         for w in ("fee", "commission", "cost", "charge"))]
        print(f"  {label}")
        print(f"    keys: {', '.join(keys)[:150]}")
        if feeish:
            print(f"    FEE FIELDS: {feeish}")
            for r in rows[:5]:
                if isinstance(r, dict):
                    px = r.get("price")
                    px = px.get("value") if isinstance(px, dict) else px
                    fees = {k: r.get(k) for k in feeish}
                    print(f"      price={px} qty={r.get('quantity')} {fees}")
        if args.probe:
            print(f"    sample: {json.dumps(rows[0])[:260]}")

    print("\nIf a fee field exists, divide it by the share count: that is the")
    print("per-share charge, and 2x it is what a ladder pair must clear.")
    c.close()


if __name__ == "__main__":
    main()
