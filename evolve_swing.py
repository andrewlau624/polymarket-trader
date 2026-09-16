import argparse
import json
import os

import numpy as np
import yaml

from src.data import flows as flows_mod
from src.data import loader
from src.features.genome_features import add_genome_features
from src.research import evolve


def splits(n, warmup=0.12, train_frac=0.55, valid_frac=0.18):
    start = int(n * warmup)
    train_end = int(n * train_frac)
    valid_end = int(n * (train_frac + valid_frac))
    return (
        slice(start, train_end),
        slice(train_end, valid_end),
        slice(valid_end, n),
    )


def seed_genomes(config):
    """Warm-start the search from the validated design and near neighbours."""
    s = config["strategy"]
    base = {
        "trend_fast": int(s["trend_fast"]),
        "trend_slow": int(s["trend_slow"]),
        "entry": "donchian",
        "entry_window": int(s["entry_window"]),
        "roc_thr": 0.0,
        "rsi_low": 30,
        "rsi_high": 70,
        "slope_bars": int(s.get("slope_bars", 0)),
        "trend_min": float(s.get("trend_min", 0.0)),
        "vol_cap": None,
        "exit": "atr",
        "atr_mult": 3.0,
        "exit_bars": 48,
        "allow_short": bool(s.get("allow_short", False)),
        "target_vol": float(s["target_vol"]),
        "max_leverage": float(s["max_leverage"]),
        "cooldown": int(s.get("cooldown", 72)),
        "use_flow": False,
    }
    seeds = [base]
    for key, vals in (("trend_slow", (480, 960)), ("entry_window", (96, 168, 240)),
                      ("atr_mult", (2.0, 3.0, 4.0)), ("target_vol", (0.6, 0.8, 1.0)),
                      ("trend_fast", (8, 24, 48))):
        for v in vals:
            seeds.append({**base, key: v})
    return seeds


def gate_passed(res):
    t = res["test_metrics"]
    bh = res["buy_hold_test"]
    psd = res["deflated_sharpe_prob"] or 0.0
    return (
        t["cagr"] > 0.05
        and t["sharpe"] > max(bh["sharpe"], 0.5)
        and t["max_drawdown"] > -0.40
        and psd > 0.95
    )


def main():
    ap = argparse.ArgumentParser(description="Genetic search over swing strategy genomes.")
    ap.add_argument("--config", default="config_swing.yaml")
    ap.add_argument("--generations", type=int, default=25)
    ap.add_argument("--pop", type=int, default=48)
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--until-pass", action="store_true", help="Re-seed and continue until the test gate passes")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--long-only", action="store_true",
                    help="Restrict the search to long-biased genomes (crypto drift prior).")
    ap.add_argument("--preset", choices=["full", "trend"], default="full",
                    help="trend = long Donchian breakout + ATR stop + slow trend filter.")
    ap.add_argument("--seed-design", action="store_true",
                    help="Warm-start the population from the validated config design.")
    ap.add_argument("--out", default="research/best_genome.json")
    args = ap.parse_args()

    config = yaml.safe_load(open(args.config))
    df = None
    flows = None
    if config["strategy"].get("use_flow"):
        flows = flows_mod.load_etf_flows(config)
    print(f"Loading data for genome search ({config['symbol']} {config['timeframe']})...")
    df = add_genome_features(loader.load_ohlcv(config), config, flows=flows)
    returns = df["close"].pct_change().fillna(0.0).to_numpy()
    n = len(df)
    train_sl, valid_sl, test_sl = splits(n)
    print(f"  {n} bars | train {df['timestamp'].iloc[train_sl.start].date()}->"
          f"{df['timestamp'].iloc[train_sl.stop - 1].date()} | valid ->"
          f"{df['timestamp'].iloc[valid_sl.stop - 1].date()} | test ->"
          f"{df['timestamp'].iloc[test_sl.stop - 1].date()}")

    best = None
    rounds = args.rounds if not args.until_pass else max(args.rounds, 20)
    for r in range(rounds):
        seed = args.seed + r
        print(f"\n=== round {r + 1}/{rounds} (seed {seed}) ===")
        dev_sl = slice(train_sl.start, test_sl.start)
        pools = evolve.TREND_PRESET if args.preset == "trend" else evolve.DEFAULT_POOLS
        seeds = seed_genomes(config) if args.seed_design else None
        res = evolve.search(
            df, returns, config, dev_sl, test_sl,
            generations=args.generations, pop_size=args.pop, seed=seed,
            has_flow=flows is not None, long_only=args.long_only, pools=pools,
            seed_genomes=seeds,
        )
        t = res["test_metrics"]
        wt = res["winner_test_metrics"]
        bh = res["buy_hold_test"]
        psd = res["deflated_sharpe_prob"]
        print(f"  ensemble {res['ensemble_ids']} | trials={res['n_trials']}")
        print(f"  TEST ensemble : cagr={t['cagr']:+.3f} sharpe={t['sharpe']:+.2f} "
              f"dd={t['max_drawdown']:+.3f} pf={t['profit_factor']:.2f} "
              f"trades={t['n_trades']}")
        print(f"  TEST best-solo: cagr={wt['cagr']:+.3f} sharpe={wt['sharpe']:+.2f} "
              f"dd={wt['max_drawdown']:+.3f}")
        print(f"  TEST buy&hold : cagr={bh['cagr']:+.3f} sharpe={bh['sharpe']:+.2f} "
              f"dd={bh['max_drawdown']:+.3f}")
        print(f"  deflated Sharpe prob={psd if psd is None else round(psd, 3)}")
        if best is None or t["sharpe"] > best["test_metrics"]["sharpe"]:
            best = res
        if gate_passed(res):
            print("  GATE PASSED")
            break
        if args.until_pass:
            print("  gate not passed, re-seeding...")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(
            {
                "genome": best["winner"],
                "genome_id": best["winner_id"],
                "ensemble": best["ensemble"],
                "ensemble_ids": best["ensemble_ids"],
                "test_metrics": best["test_metrics"],
                "buy_hold_test": best["buy_hold_test"],
                "deflated_sharpe_prob": best["deflated_sharpe_prob"],
                "n_trials": best["n_trials"],
            },
            fh,
            indent=2,
        )
    print(f"\nBest genome saved -> {args.out}")
    print(json.dumps(best["winner"], indent=2))
    if not gate_passed(best):
        print("\nNOTE: gate NOT passed. The best test result is not statistically "
              "significant after multiple-testing correction.")


if __name__ == "__main__":
    main()
