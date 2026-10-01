"""Scan every (category, price band, hours-before-close) cell for a buy-and-hold edge.

    research/.venv-data/bin/python hunt/scan.py                 # explore: TRAIN only
    research/.venv-data/bin/python hunt/scan.py --freeze        # write hunt/frozen.json
    research/.venv-data/bin/python hunt/scan.py --holdout       # grade the frozen list ONCE

Protocol (written before any cell was looked at):
  TRAIN    markets whose last trade is before 2026-03-01
  HOLDOUT  markets whose last trade is 2026-03-01 or later; never read by the
           explore pass, and only the cells in hunt/frozen.json are graded on it
  Entry    the first taker BUY in the cell (a price someone paid at the ask),
           held to resolution, charged Polymarket US's taker fee 0.0695 p(1-p)
  Unit     return per $ staked; CI by resampling whole EVENTS (one game's markets
           share an outcome)
  Shortlist  >= 60 events in TRAIN, 95% CI lower bound > 0, and positive in both
           halves of TRAIN. Hundreds of cells are scanned, so some shortlisted
           cells will be noise: that is what the holdout is for.
  PASS     on HOLDOUT: >= 30 events and 95% CI lower bound > 0.
"""

import argparse
import json
import os

import duckdb
import numpy as np

D = "research/pmdata"
SPLIT = 1772323200            # 2026-03-01T00:00:00Z
FROZEN = "hunt/frozen.json"
BANDS = [0.0, 0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.90, 0.95, 0.98, 1.0]
HOURS = [0, 1, 6, 24, 72, 168, 720, 10 ** 6]
FEE = 0.0695


def load(c, holdout):
    cmp = ">=" if holdout else "<"
    return c.sql(f"""
      SELECT e.market_id, e.tok, e.band, e.hb, e.price, e.usd, m.cat, m.event_id, m.t_end,
             (e.tok = m.winner)::INT AS won
      FROM read_parquet('{D}/entries.parquet') e JOIN read_parquet('{D}/mk.parquet') m USING (market_id)
      WHERE m.t_end {cmp} {SPLIT}""").df()


def cell_stats(g, reps=1000, seed=3):
    """ret per $ with an event-cluster bootstrap CI."""
    g = g.assign(ret=g.won - g.price - FEE * g.price * (1 - g.price))
    ev = g.groupby("event_id").agg(r=("ret", "sum"), c=("price", "sum"), n=("ret", "size"))
    r, c = ev.r.to_numpy(), ev.c.to_numpy()
    est = r.sum() / c.sum()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(r), size=(reps, len(r)))
    boots = r[idx].sum(1) / c[idx].sum(1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    t_mid = np.median(g.t_end)
    h1, h2 = g[g.t_end < t_mid], g[g.t_end >= t_mid]
    half = lambda h: (h.ret.sum() / h.price.sum()) if len(h) else float("nan")
    return {"n": int(len(g)), "events": int(len(ev)), "ret_per_usd": float(est),
            "lo": float(lo), "hi": float(hi), "win": float(g.won.mean()),
            "avg_px": float(g.price.mean()), "h1": float(half(h1)), "h2": float(half(h2)),
            "med_usd": float(g.usd.median())}


def label(cat, band, hb):
    return f"{cat:<18} px {BANDS[band]:.2f}-{BANDS[band + 1]:.2f}  close in {HOURS[hb]}-{HOURS[hb + 1]}h"


def explore(df, min_events=60):
    rows = []
    for (cat, band, hb), g in df.groupby(["cat", "band", "hb"]):
        if g.event_id.nunique() < min_events:
            continue
        s = cell_stats(g)
        s.update(cat=cat, band=int(band), hb=int(hb))
        rows.append(s)
    return rows


def show(rows, title, k=40):
    print(f"\n{title}")
    print(f"  {'cell':<58} {'events':>6} {'win':>5} {'avgpx':>6} {'ret/$':>7} {'95% CI':>17} {'half1':>7} {'half2':>7}")
    for s in rows[:k]:
        print(f"  {label(s['cat'], s['band'], s['hb']):<58} {s['events']:>6} {s['win']:>5.2f} {s['avg_px']:>6.3f} "
              f"{s['ret_per_usd']:>+7.1%} [{s['lo']:>+6.1%},{s['hi']:>+6.1%}] {s['h1']:>+7.1%} {s['h2']:>+7.1%}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--holdout", action="store_true")
    a = ap.parse_args()
    c = duckdb.connect()
    if a.holdout:
        frozen = json.load(open(FROZEN))
        df = load(c, holdout=True)
        out = []
        for f in frozen["cells"]:
            g = df[(df.cat == f["cat"]) & (df.band == f["band"]) & (df.hb == f["hb"])]
            if g.event_id.nunique() < 5:
                print(f"  {label(f['cat'], f['band'], f['hb'])}: too few holdout events ({g.event_id.nunique()})")
                continue
            s = cell_stats(g)
            s.update(cat=f["cat"], band=f["band"], hb=f["hb"], train=f["ret_per_usd"],
                     passed=bool(s["events"] >= 30 and s["lo"] > 0))
            out.append(s)
        out.sort(key=lambda s: -s["lo"])
        show(out, f"HOLDOUT ({len(out)} frozen cells graded)", k=len(out))
        print("\n  PASSED: " + (", ".join(label(s['cat'], s['band'], s['hb']) for s in out if s["passed"]) or "none"))
        json.dump(out, open("research/pmdata/holdout_results.json", "w"), indent=1)
        return
    df = load(c, holdout=False)
    print(f"TRAIN: {len(df):,} entries on {df.market_id.nunique():,} markets, {df.event_id.nunique():,} events")
    rows = explore(df)
    print(f"{len(rows)} cells with >= 60 events")
    short = [s for s in rows if s["lo"] > 0 and s["h1"] > 0 and s["h2"] > 0]
    short.sort(key=lambda s: -s["lo"])
    show(short, f"SHORTLIST: CI above 0 and both halves positive ({len(short)} of {len(rows)})")
    swings = sorted([s for s in rows if s["band"] <= 3], key=lambda s: -s["ret_per_usd"])
    show(swings, "BIG SWINGS: entries under 20c, best by ret/$ (any CI)", k=25)
    worst = sorted(rows, key=lambda s: s["hi"])[:15]
    show(worst, "MOST OVERPRICED (CI entirely below 0): the other side of these is the trade")
    json.dump(rows, open("research/pmdata/train_cells.json", "w"))
    if a.freeze:
        json.dump({"frozen_at": "see git log", "rule": "lo > 0 and both train halves > 0, >= 60 events",
                   "cells": [{k: s[k] for k in ("cat", "band", "hb", "ret_per_usd", "lo", "events")}
                             for s in short]}, open(FROZEN, "w"), indent=1)
        print(f"\nfroze {len(short)} cells to {FROZEN}; commit it BEFORE running --holdout")


if __name__ == "__main__":
    main()
