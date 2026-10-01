"""Do skilled takers stay skilled, and can you follow them? (global Polymarket)

    research/.venv-data/bin/python hunt/wallets.py --select      # 2025 -> hunt/skilled.json
    research/.venv-data/bin/python hunt/wallets.py --follow      # grade on the holdout

Written before either step was run:

SELECT   taker trades in markets that resolved 2025-01-01 .. 2026-02-28 (the
         holdout starts 2026-03-01 and is not read). Per wallet, P&L to
         resolution of every taker trade (BUY X at p earns won - p per token,
         SELL X at p earns p - won), summed per EVENT. A wallet is skilled if:
           >= 30 events, mean event P&L / its standard error >= 3.0,
           not crypto up/down (5-15 min markets are a different, bot game),
           and it is not an exchange/operator address (taker == contract).
FOLLOW   on markets resolving 2026-03-01 or later: each time a skilled wallet
         taker-BUYS token X, we buy X at the first taker BUY price by ANYONE at
         least DELAY seconds later (a real ask we could have hit), hold to
         resolution, pay the Polymarket US taker fee. One entry per (market, token).
PLACEBO  the same markets and tokens entered at a random taker BUY of that token
         (same count). The follow edge is only real if it beats the placebo.
PASS     >= 300 entries on >= 60 events, follow return per $ CI above 0 AND
         above the placebo's point estimate, at DELAY = 60 s.
"""

import argparse
import json

import duckdb
import numpy as np

D = "research/pmdata"
SEL_FROM, SPLIT = 1735689600, 1772323200          # 2025-01-01, 2026-03-01 UTC
FEE = 0.0695


def con():
    c = duckdb.connect()
    c.sql("SET memory_limit='10GB'; SET threads=8; SET preserve_insertion_order=false")
    c.sql(f"SET temp_directory='{D}/tmp'")
    return c


def select(c):
    c.sql(f"""
    CREATE OR REPLACE TABLE tk AS
    SELECT tr.taker AS w, m.event_id, m.cat,
           CASE WHEN tr.taker_direction = 'BUY'
                THEN tr.token_amount * ((tr.nonusdc_side = m.winner)::INT - tr.price)
                ELSE tr.token_amount * (tr.price - (tr.nonusdc_side = m.winner)::INT) END AS pnl,
           tr.usd_amount AS usd
    FROM read_parquet('{D}/trades.parquet') tr JOIN read_parquet('{D}/mk.parquet') m USING (market_id)
    WHERE m.t_end >= {SEL_FROM} AND m.t_end < {SPLIT} AND m.cat <> 'crypto_updown'
      AND tr.taker <> tr.contract""")
    c.sql("""
    CREATE OR REPLACE TABLE ev AS
    SELECT w, event_id, sum(pnl) AS pnl, sum(usd) AS usd FROM tk GROUP BY ALL""")
    df = c.sql("""
    SELECT w, count(*) AS events, sum(pnl) AS pnl, sum(usd) AS usd,
           avg(pnl) / (stddev_samp(pnl) / sqrt(count(*))) AS t
    FROM ev GROUP BY w HAVING count(*) >= 30""").df()
    sk = df[df.t >= 3.0].sort_values("t", ascending=False)
    print(f"wallets with >= 30 events: {len(df):,}; skilled (t >= 3): {len(sk):,} "
          f"({len(sk) / max(len(df), 1):.1%}); their P&L ${sk.pnl.sum() / 1e6:.1f}M on ${sk.usd.sum() / 1e6:.0f}M")
    print(sk.head(15).to_string())
    json.dump({"rule": "taker, >=30 events, t>=3, 2025-01-01..2026-02-28, no crypto up/down",
               "wallets": sk.w.tolist()}, open("hunt/skilled.json", "w"))
    print("wrote hunt/skilled.json - commit it before --follow")


def follow(c, delay):
    sk = json.load(open("hunt/skilled.json"))["wallets"]
    c.sql("CREATE OR REPLACE TABLE sk AS SELECT unnest($w) AS w", params={"w": sk})
    c.sql(f"""
    CREATE OR REPLACE TABLE ho AS
    SELECT tr.market_id, tr.nonusdc_side AS tok, tr.taker AS w, tr.taker_direction AS dir,
           tr.price, tr.timestamp AS ts, m.event_id, m.cat, (tr.nonusdc_side = m.winner)::INT AS won
    FROM read_parquet('{D}/trades.parquet') tr JOIN read_parquet('{D}/mk.parquet') m USING (market_id)
    WHERE m.t_end >= {SPLIT} AND m.cat <> 'crypto_updown' AND tr.price > 0 AND tr.price < 1""")
    sig = c.sql("""
    SELECT market_id, tok, min(ts) AS ts FROM ho
    WHERE dir = 'BUY' AND w IN (SELECT w FROM sk) GROUP BY ALL""")
    c.sql("CREATE OR REPLACE TABLE sig AS SELECT * FROM sig")
    fol = c.sql(f"""
    SELECT s.market_id, s.tok, arg_min(h.price, h.ts) AS price, any_value(h.event_id) AS event_id,
           any_value(h.cat) AS cat, any_value(h.won) AS won
    FROM sig s JOIN ho h ON h.market_id = s.market_id AND h.tok = s.tok AND h.dir = 'BUY'
         AND h.ts >= s.ts + {delay}
    GROUP BY ALL""").df()
    plc = c.sql("""
    SELECT h.market_id, h.tok, arg_max(h.price, hash(h.ts, h.price)) AS price,
           any_value(h.event_id) AS event_id, any_value(h.cat) AS cat, any_value(h.won) AS won
    FROM ho h JOIN sig s USING (market_id, tok) WHERE h.dir = 'BUY' GROUP BY ALL""").df()
    for name, df in (("FOLLOW", fol), ("PLACEBO", plc)):
        report(name, df)
    print("\nby category (follow):")
    for cat, g in fol.groupby("cat"):
        if g.event_id.nunique() >= 20:
            report(f"  {cat}", g)


def report(name, df, reps=1000):
    df = df.assign(ret=df.won - df.price - FEE * df.price * (1 - df.price))
    ev = df.groupby("event_id").agg(r=("ret", "sum"), c=("price", "sum"))
    r, cc = ev.r.to_numpy(), ev.c.to_numpy()
    idx = np.random.default_rng(1).integers(0, len(r), (reps, len(r)))
    lo, hi = np.percentile(r[idx].sum(1) / cc[idx].sum(1), [2.5, 97.5])
    print(f"{name:<22} n={len(df):>6} events={len(ev):>5} win {df.won.mean():.2f} avg px {df.price.mean():.3f} "
          f"ret/$ {r.sum() / cc.sum():+.2%} [{lo:+.2%}, {hi:+.2%}]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--select", action="store_true")
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--delay", type=int, default=60)
    a = ap.parse_args()
    c = con()
    if a.select:
        select(c)
    if a.follow:
        follow(c, a.delay)


if __name__ == "__main__":
    main()
