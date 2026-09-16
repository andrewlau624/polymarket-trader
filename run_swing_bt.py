"""Per-category swing backtest on Polymarket (LoL focus, plus comparison)."""

import argparse

from src.pm.history import CATEGORY_TAGS, market_dataset
from src.pm.swing_bt import backtest_market, calibration_obs, calibration_table, summarize

STRATEGIES = {
    "A_early": ("A", {"lookback": 5, "thr": 0.03, "flow": 0.10}),
    "B_ride": ("B", {"lookback": 5, "thr": 0.03, "retrace": 0.20}),
    "C_jump": ("C", {"jump": 0.10}),
    "D_value": ("D", {"max_px": 0.25}),
    "D_shallow": ("D", {"max_px": 0.10}),
}


def main():
    ap = argparse.ArgumentParser(description="Swing strategies A/B/C per Polymarket category.")
    ap.add_argument("--categories", default="LoL,Esports,Politics,Sports,Crypto")
    ap.add_argument("--max-events", type=int, default=40)
    ap.add_argument("--max-markets-per-event", type=int, default=3)
    ap.add_argument("--cost", type=float, default=0.005, help="per-share cost (fee+slippage)")
    args = ap.parse_args()

    cats = [c.strip() for c in args.categories.split(",") if c.strip()]
    all_trades = {k: [] for k in STRATEGIES}
    per_cat = {}
    calib = []

    for cat in cats:
        tag = CATEGORY_TAGS.get(cat)
        if tag is None:
            print(f"unknown category {cat}")
            continue
        print(f"\nfetching {cat} (tag {tag})...")
        try:
            ds = market_dataset(cat, tag, max_events=args.max_events,
                                max_markets_per_event=args.max_markets_per_event)
        except Exception as e:
            print(f"  fetch failed: {type(e).__name__} {str(e)[:60]}")
            continue
        print(f"  {len(ds)} resolved markets with usable tape")
        per_cat[cat] = {"markets": len(ds)}
        per_cat[cat]["_obs"] = calibration_obs(ds)
        calib.extend((cat, o) for o in per_cat[cat]["_obs"])
        for name, (strat, params) in STRATEGIES.items():
            trades = []
            for m in ds:
                trades.extend(backtest_market(m, strat, params, args.cost))
            all_trades[name].extend(trades)
            per_cat[cat][name] = summarize(trades)

    hdr = f"{'category':<10}{'mkt':>5} | " + "".join(f"{n:>32}" for n in STRATEGIES)
    sub = "".join(f"{'n':>6}{'hit':>6}{'capROI':>9}{'edge':>7}{'t':>4}" for _ in STRATEGIES)
    print("\n" + "=" * len(hdr))
    print(hdr)
    print(f"{'':<10}{'':>5} | " + sub)
    print("-" * len(hdr))
    for cat, row in per_cat.items():
        line = f"{cat:<10}{row['markets']:>5} | "
        for name in STRATEGIES:
            s = row.get(name)
            if s:
                line += (f"{s['n']:>6}{s['hit']:>6.2f}{s['cap_roi']:>9.3f}"
                         f"{s['edge']:>7.2f}{s['tstat']:>4.1f}")
            else:
                line += f"{'-':>32}"
        print(line)

    print("\nALL CATEGORIES (pooled):")
    for name in STRATEGIES:
        s = summarize(all_trades[name])
        if s:
            print(f"  {name:<10} n={s['n']:>5} hit={s['hit']:.3f} capROI={s['cap_roi']:+.4f} "
                  f"median_roi={s['median_roi']:+.3f} edge={s['edge']:+.3f} "
                  f"t={s['tstat']:+.1f} avg_entry={s['avg_entry']:.2f}")
    print("\ncapROI = total PnL / total capital staked (honest, skew-robust).")
    print("edge  = win rate - average entry price (calibration edge).")

    print("\n" + "=" * 60)
    print("MISPRICING CHECK: win rate vs price, by category and price bucket")
    print("edge>0 => that bucket is UNDER-priced (buy it); edge<0 => OVER-priced (fade it)")
    print(f"{'category':<10}{'bucket':>10}{'n':>6}{'price':>8}{'win':>7}{'edge':>8}")
    for cat in cats:
        obs = [o for c, o in calib if c == cat]
        for row in calibration_table(obs):
            flag = "  <-- " if abs(row["edge"]) > 0.05 and row["n"] >= 15 else ""
            print(f"{cat:<10}{row['bucket']:>10}{row['n']:>6}{row['avg_price']:>8.2f}"
                  f"{row['win_rate']:>7.2f}{row['edge']:>+8.3f}{flag}")
    allobs = [o for _, o in calib]
    print(f"\n{'ALL':<10}{'pooled':>10}{len(allobs):>6}"
          f"{sum(o[1] for o in allobs)/len(allobs):>8.2f}"
          f"{sum(o[2] for o in allobs)/len(allobs):>7.2f}"
          f"{sum(o[2]-o[1] for o in allobs)/len(allobs):>+8.3f}")


if __name__ == "__main__":
    main()
