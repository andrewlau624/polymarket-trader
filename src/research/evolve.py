"""Genetic strategy search over composable genomes.

Honesty protocol:
  - the GA optimises a *robustness* fitness computed over k contiguous folds
    of the TRAIN slice (mean fold Sharpe minus a dispersion penalty), not the
    peak full-train Sharpe. Robustness selects against curve-fit genomes.
  - the top K train genomes are scored once on VALID; the best VALID set is
    merged (positions averaged) into an ensemble.
  - the ensemble is evaluated on the untouched TEST slice.
  - every evaluation is logged so the trial count for a deflated Sharpe is
    known rather than guessed.
"""

import json
import os
import random

import numpy as np

from src.backtest.costs import strategy_returns
from src.backtest.metrics import compute_metrics
from src.data.timeframe import periods_per_year
from src.research.deflated_sharpe import prob_of_deflated_sharpe
from src.strategy.genome import (
    DONCHIAN_WINDOWS,
    ENTRY_TYPES,
    EXIT_TYPES,
    ROC_WINDOWS,
    SLOPE_BARS,
    genome_id,
    genome_position,
)

FAST_SPANS = (8, 16, 24, 48, 96)
SLOW_SPANS = (96, 192, 480, 960)
ROC_THR = (0.0, 0.005, 0.01, 0.02)
RSI_LOW = (20, 25, 30, 35)
RSI_HIGH = (65, 70, 75, 80)
TREND_MIN = (0.0, 0.005, 0.01, 0.02)
VOL_CAP = (None, 1.0, 1.5, 2.0)
ATR_MULT = (1.5, 2.0, 3.0, 4.0, 6.0)
EXIT_BARS = (12, 24, 48, 96)
TARGET_VOL = (0.5, 0.6, 0.8, 1.0)
MAX_LEV = (1.0, 2.0, 3.0)
COOLDOWN = (0, 24, 72, 168)


DEFAULT_POOLS = {
    "entry": ENTRY_TYPES,
    "exit": EXIT_TYPES,
    "fast": FAST_SPANS,
    "slow": SLOW_SPANS,
    "donchian": DONCHIAN_WINDOWS,
    "roc": ROC_WINDOWS,
}

# Domain prior: crypto drifts up, so a long-only Donchian breakout with an
# ATR stop and a slow trend filter. Search tunes the rest inside this region.
TREND_PRESET = {
    "entry": ("donchian",),
    "exit": ("atr",),
    "fast": (8, 16, 24, 48),
    "slow": (192, 480, 960),
    "donchian": (48, 96, 168),
    "roc": ROC_WINDOWS,
}


def _pool_for(g, key, pools):
    p = pools.get(key)
    return p


def random_genome(rng, has_flow=False, long_only=False, pools=None):
    pools = pools or DEFAULT_POOLS
    entry = rng.choice(pools["entry"])
    fast = rng.choice(pools["fast"])
    slow = rng.choice([s for s in pools["slow"] if s > fast])
    window = rng.choice(pools["donchian"] if entry == "donchian" else pools["roc"])
    return {
        "trend_fast": fast,
        "trend_slow": slow,
        "entry": entry,
        "entry_window": window,
        "roc_thr": rng.choice(ROC_THR),
        "rsi_low": rng.choice(RSI_LOW),
        "rsi_high": rng.choice(RSI_HIGH),
        "slope_bars": rng.choice(SLOPE_BARS),
        "trend_min": rng.choice(TREND_MIN),
        "vol_cap": rng.choice(VOL_CAP),
        "exit": rng.choice(pools["exit"]),
        "atr_mult": rng.choice(ATR_MULT),
        "exit_bars": rng.choice(EXIT_BARS),
        "allow_short": False if long_only else rng.choice((True, False)),
        "target_vol": rng.choice(TARGET_VOL),
        "max_leverage": rng.choice(MAX_LEV),
        "cooldown": rng.choice(COOLDOWN),
        "use_flow": bool(rng.random() < 0.3) and has_flow,
    }


def repair(genome, rng, pools=None):
    pools = pools or DEFAULT_POOLS
    g = dict(genome)
    if g["trend_slow"] <= g["trend_fast"]:
        opts = [s for s in pools["slow"] if s > g["trend_fast"]]
        g["trend_slow"] = rng.choice(opts) if opts else max(pools["slow"])
    pool = pools["donchian"] if g["entry"] == "donchian" else pools["roc"]
    if g["entry_window"] not in pool:
        g["entry_window"] = rng.choice(pool)
    if g["rsi_low"] >= g["rsi_high"]:
        g["rsi_low"], g["rsi_high"] = 30, 70
    return g


def mutate(genome, rng, has_flow=False, n_genes=2, long_only=False, pools=None):
    pools = pools or DEFAULT_POOLS
    g = dict(genome)
    keys = [k for k in g if (k != "use_flow" or has_flow) and (k != "allow_short" or not long_only)]
    for _ in range(n_genes):
        k = rng.choice(keys)
        if k == "trend_fast":
            g["trend_fast"] = rng.choice(pools["fast"])
        elif k == "trend_slow":
            g["trend_slow"] = rng.choice(pools["slow"])
        elif k == "entry":
            g["entry"] = rng.choice(pools["entry"])
        elif k == "entry_window":
            g["entry_window"] = 0
        elif k == "roc_thr":
            g["roc_thr"] = rng.choice(ROC_THR)
        elif k == "rsi_low":
            g["rsi_low"] = rng.choice(RSI_LOW)
        elif k == "rsi_high":
            g["rsi_high"] = rng.choice(RSI_HIGH)
        elif k == "slope_bars":
            g["slope_bars"] = rng.choice(SLOPE_BARS)
        elif k == "trend_min":
            g["trend_min"] = rng.choice(TREND_MIN)
        elif k == "vol_cap":
            g["vol_cap"] = rng.choice(VOL_CAP)
        elif k == "exit":
            g["exit"] = rng.choice(pools["exit"])
        elif k == "atr_mult":
            g["atr_mult"] = rng.choice(ATR_MULT)
        elif k == "exit_bars":
            g["exit_bars"] = rng.choice(EXIT_BARS)
        elif k == "allow_short":
            g["allow_short"] = not g["allow_short"]
        elif k == "target_vol":
            g["target_vol"] = rng.choice(TARGET_VOL)
        elif k == "max_leverage":
            g["max_leverage"] = rng.choice(MAX_LEV)
        elif k == "cooldown":
            g["cooldown"] = rng.choice(COOLDOWN)
        elif k == "use_flow":
            g["use_flow"] = not g["use_flow"]
    return repair(g, rng, pools=pools)


def crossover(a, b, rng, pools=None):
    child = {k: (a[k] if rng.random() < 0.5 else b[k]) for k in a}
    return repair(child, rng, pools=pools)


def make_folds(sl, k=4):
    edges = np.linspace(sl.start, sl.stop, k + 1).astype(int)
    return [slice(a, b) for a, b in zip(edges[:-1], edges[1:])]


def position_and_net(df, returns, genome, config):
    pos, _ = genome_position(df, genome, config)
    return pos, strategy_returns(returns, pos, config)


def metrics_for(net, pos, folds, ppy):
    return [compute_metrics(net[f], pos[f], ppy) for f in folds]


def robust_fitness(ms):
    sh = np.array([m["sharpe"] for m in ms])
    trades = min(m["n_trades"] for m in ms)
    exposure = float(np.mean([m["exposure"] for m in ms]))
    if trades < 3 or exposure < 0.02:
        return -5.0
    # reward consistent fold performance, punish dispersion
    return float(sh.mean() - 0.75 * sh.std())


def search(df, returns, config, dev_sl, test_sl,
           generations=20, pop_size=48, seed=0, has_flow=False, ensemble_k=5,
           long_only=False, n_folds=6, pools=None, seed_genomes=None,
           ledger_path="research/strategy_ledger.jsonl", log=print):
    """``dev_sl`` is the development slice (fitness folds live inside it);
    ``test_sl`` is never used until the final single evaluation."""
    rng = random.Random(seed)
    pools = pools or DEFAULT_POOLS
    ppy = periods_per_year(config["timeframe"])
    train_folds = make_folds(dev_sl, k=n_folds)
    seen = {}
    ledger = []

    def eval_genome(genome, folds):
        pos, net = position_and_net(df, returns, genome, config)
        return metrics_for(net, pos, folds, ppy)

    def record(genome, phase, ms):
        gid = genome_id(genome)
        seen[gid] = genome
        ledger.append({"genome_id": gid, "phase": phase,
                       "sharpe": float(np.mean([m["sharpe"] for m in ms])),
                       "genome": genome})

    def fitness(genome):
        ms = eval_genome(genome, train_folds)
        record(genome, "train", ms)
        return robust_fitness(ms)

    population = []
    for g in (seed_genomes or []):
        g = repair(g, rng, pools=pools)
        if genome_id(g) not in seen:
            population.append(g)
    while len(population) < pop_size:
        g = random_genome(rng, has_flow, long_only, pools)
        if genome_id(g) not in seen:
            population.append(g)

    for gen in range(generations):
        scored = sorted(((fitness(g), genome_id(g), g) for g in population),
                        key=lambda x: x[0], reverse=True)
        elite = [g for _, _, g in scored[: max(2, pop_size // 5)]]
        log(f"  gen {gen + 1:>2}/{generations}  best robust fit={scored[0][0]:+.2f}  "
            f"median={np.median([s[0] for s in scored]):+.2f}")
        children = list(elite)
        guard = 0
        while len(children) < pop_size and guard < pop_size * 20:
            guard += 1
            a, b = rng.choice(elite), rng.choice(elite)
            child = crossover(a, b, rng, pools) if rng.random() < 0.6 else dict(a)
            child = mutate(child, rng, has_flow, n_genes=rng.choice((1, 2, 2, 3)),
                           long_only=long_only, pools=pools)
            if genome_id(child) in seen:
                continue
            children.append(child)
        population = children

    # rank the final population by robust (walk-forward) development fitness
    ranked = sorted(((fitness(g), genome_id(g), g) for g in population),
                    key=lambda x: x[0], reverse=True)
    members = [g for _, _, g in ranked[:ensemble_k]]
    winner = members[0]

    # ensemble = average of member positions (the "merge")
    ens_pos = None
    for g in members:
        pos, _ = position_and_net(df, returns, g, config)
        ens_pos = pos if ens_pos is None else ens_pos + pos
    ens_pos /= len(members)
    ens_net = strategy_returns(returns, ens_pos, config)

    ens_test = compute_metrics(ens_net[test_sl], ens_pos[test_sl], ppy)
    win_pos, win_net = position_and_net(df, returns, winner, config)
    win_test = compute_metrics(win_net[test_sl], win_pos[test_sl], ppy)
    bh = compute_metrics(returns[test_sl], np.ones(test_sl.stop - test_sl.start), ppy)

    n_trials = len(seen)
    net = ens_net[test_sl]
    psd = None
    if len(net) > 2 and net.std(ddof=1) > 0:
        mu, sd = net.mean(), net.std(ddof=1)
        skew = float(((net - mu) ** 3).mean() / sd**3)
        kurt = float(((net - mu) ** 4).mean() / sd**4)
        psd = prob_of_deflated_sharpe(ens_test["sharpe"] / np.sqrt(ppy),
                                      n_trials, len(net), skew, kurt)

    os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
    with open(ledger_path, "a") as fh:
        for row in ledger:
            fh.write(json.dumps(row) + "\n")

    return {
        "winner": winner,
        "winner_id": genome_id(winner),
        "ensemble": members,
        "ensemble_ids": [genome_id(g) for g in members],
        "test_metrics": ens_test,
        "winner_test_metrics": win_test,
        "buy_hold_test": bh,
        "deflated_sharpe_prob": psd,
        "n_trials": n_trials,
    }
