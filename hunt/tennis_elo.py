"""T4: tennis Elo vs the pre-match ask on the international venue (TEST_PLAN.md).

    research/.venv-data/bin/python hunt/tennis_elo.py
"""

import re
from collections import defaultdict

import duckdb
import numpy as np
import pandas as pd

D = "research/pmdata"
SPLIT = pd.Timestamp("2026-03-01", tz="UTC").timestamp()
FEE, EDGE, MIN_N = 0.0695, 0.05, 10


def key(name):
    return " ".join(re.sub(r"[^a-z ]", " ", name.lower()).split())


def load(c):
    m = c.sql(f"""
      SELECT k.market_id, k.event_id, k.winner, lower(split_part(k.slug, '-', 1)) AS lvl,
             epoch(k.end_date) - 7 * 86400 AS s, k.q
      FROM read_parquet('{D}/mk.parquet') k
      WHERE lower(split_part(k.slug, '-', 1)) IN ('atp', 'wta', 'itf')
        AND k.q LIKE '% vs %' AND k.q NOT LIKE '%/%' AND k.q NOT ILIKE '%completed match%'
        AND k.q NOT ILIKE '%total%' AND k.q NOT ILIKE '%set%' AND k.q NOT ILIKE '%o/u%'
        AND k.q NOT ILIKE '%handicap%' AND k.q NOT ILIKE '%spread%'""").df()
    pairs = m.q.str.split(": ").str[-1].str.split(" vs ", n=1)
    m["a"] = pairs.str[0].map(lambda x: key(x) if isinstance(x, str) else None)
    m["b"] = pairs.str[1].map(lambda x: key(x) if isinstance(x, str) else None)
    m = m.dropna(subset=["a", "b"])
    m = m[(m.a != "") & (m.b != "") & (m.a != m.b)].sort_values("s").reset_index(drop=True)
    c.register("mm", m[["market_id", "s"]])
    px = c.sql(f"""
      SELECT t.market_id,
             CASE WHEN t.taker_direction = 'BUY' THEN t.nonusdc_side
                  WHEN t.nonusdc_side = 'token1' THEN 'token2' ELSE 'token1' END AS tok,
             arg_max(CASE WHEN t.taker_direction = 'BUY' THEN t.price ELSE 1 - t.price END, t.timestamp) AS p
      FROM read_parquet('{D}/trades.parquet') t JOIN mm USING (market_id)
      WHERE t.timestamp >= mm.s - 6 * 3600 AND t.timestamp < mm.s AND t.price > 0 AND t.price < 1
      GROUP BY ALL""").df()
    piv = px.pivot_table(index="market_id", columns="tok", values="p", aggfunc="first")
    m = m.merge(piv, left_on="market_id", right_index=True, how="left")
    return m


def run(m, k):
    r, n = defaultdict(lambda: 1500.0), defaultdict(int)
    pa, na = [], []
    for a, b, w in zip(m.a, m.b, m.winner):
        e = 1 / (1 + 10 ** ((r[b] - r[a]) / 400))
        pa.append(e)
        na.append(min(n[a], n[b]))
        s = 1.0 if w == "token1" else 0.0
        r[a] += k * (s - e)
        r[b] -= k * (s - e)
        n[a] += 1
        n[b] += 1
    return np.array(pa), np.array(na)


def boot(ret, cost, ev, reps=2000):
    g = pd.DataFrame({"r": ret, "c": cost, "e": ev}).groupby("e").sum()
    rr, cc = g.r.to_numpy(), g.c.to_numpy()
    idx = np.random.default_rng(4).integers(0, len(rr), (reps, len(rr)))
    return rr.sum() / cc.sum(), *np.percentile(rr[idx].sum(1) / cc[idx].sum(1), [2.5, 97.5])


def main():
    c = duckdb.connect()
    c.sql("SET enable_progress_bar=false; SET threads=8; SET memory_limit='10GB'")
    m = load(c)
    y = (m.winner == "token1").astype(float).to_numpy()
    print(f"{len(m):,} tennis matches; {m.token1.notna().sum():,} with a pre-start ask on token1")
    tr = (m.s < SPLIT).to_numpy()
    best = None
    for k in (16, 24, 32, 48):
        p, n = run(m, k)
        ok = tr & (n >= MIN_N)
        b = np.mean((p[ok] - y[ok]) ** 2)
        print(f"  K={k}: train Brier {b:.4f} on {ok.sum():,}")
        if best is None or b < best[1]:
            best = (k, b)
    k = best[0]
    p, n = run(m, k)
    m["elo"], m["n"] = p, n
    rated = m[(m.n >= MIN_N)].copy()
    both = rated.dropna(subset=["token1", "token2"])
    mid = (both.token1 + (1 - both.token2)) / 2
    yy = (both.winner == "token1").astype(float)
    for lab, sel in (("train", both.s < SPLIT), ("HOLDOUT", both.s >= SPLIT)):
        print(f"  Brier {lab}: price mid {np.mean((mid[sel] - yy[sel]) ** 2):.4f} | Elo "
              f"{np.mean((both.elo[sel] - yy[sel]) ** 2):.4f} (n={sel.sum():,})")
    rows = []
    for _, r in rated.iterrows():
        opts = []
        for tok, pe in (("token1", r.elo), ("token2", 1 - r.elo)):
            c_ = r.get(tok)
            if c_ is not None and not np.isnan(c_) and 0 < c_ < 1:
                opts.append((pe - (c_ + FEE * c_ * (1 - c_)), tok, c_))
        if not opts:
            continue
        edge, tok, c_ = max(opts)
        if edge >= EDGE:
            won = float(r.winner == tok)
            rows.append({"s": r.s, "lvl": r.lvl, "ev": r.event_id, "cost": c_,
                         "ret": won - c_ - FEE * c_ * (1 - c_), "won": won})
    e = pd.DataFrame(rows)
    print(f"\nentries (Elo edge >= {EDGE}): {len(e):,} (K={k})")
    for lvl in ("itf", "atp", "wta"):
        for lab, sel in (("train", e.s < SPLIT), ("HOLDOUT", e.s >= SPLIT)):
            g = e[sel & (e.lvl == lvl)]
            if len(g) < 30:
                continue
            est, lo, hi = boot(g.ret, g.cost, g.ev)
            mid_t = g.s.median()
            h = [x.ret.sum() / x.cost.sum() for x in (g[g.s < mid_t], g[g.s >= mid_t])]
            print(f"  {lvl} {lab:<7} n={len(g):>5} win {g.won.mean():.2f} avg px {g.cost.mean():.3f} "
                  f"ret/$ {est:+.2%} [{lo:+.2%}, {hi:+.2%}] halves {h[0]:+.1%} / {h[1]:+.1%}")


if __name__ == "__main__":
    main()
