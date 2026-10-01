"""T1: tennis favourite around match time, timed from the SCHEDULED start (TEST_PLAN.md).

    research/.venv-data/bin/python hunt/tennis.py

S = end_date - 7 days (the international venue's tennis placeholder). Entries are
aggressive buys (see build.py: taker SELL X at p == buy the other token at 1 - p).
"""

import duckdb
import numpy as np

D = "research/pmdata"
SPLIT = 1772323200
FEE = 0.0695
BANDS = {"A 0.65-0.80": (0.65, 0.80), "B 0.80-0.90": (0.80, 0.90)}


def main():
    c = duckdb.connect()
    c.sql("SET enable_progress_bar=false; SET threads=8; SET memory_limit='10GB'")
    c.sql(f"""
    CREATE TABLE b AS
    WITH m AS (
      SELECT market_id, event_id, winner, t_end, epoch(end_date) - 7 * 86400 AS s
      FROM read_parquet('{D}/mk.parquet')
      WHERE cat IN ('sport_atp', 'sport_wta', 'sport_tennis_minor') AND q ILIKE '% vs%'
        AND q NOT ILIKE '%set%' AND q NOT ILIKE '%total%' AND q NOT ILIKE '%handicap%'
        AND q NOT ILIKE '%spread%' AND q NOT ILIKE '%o/u%'
    )
    SELECT m.market_id, m.event_id, m.t_end >= {SPLIT} AS ho, tr.timestamp AS ts,
           CASE WHEN tr.taker_direction = 'BUY' THEN tr.nonusdc_side
                WHEN tr.nonusdc_side = 'token1' THEN 'token2' ELSE 'token1' END AS tok,
           CASE WHEN tr.taker_direction = 'BUY' THEN tr.price ELSE 1 - tr.price END AS p,
           m.winner
    FROM read_parquet('{D}/trades.parquet') tr JOIN m USING (market_id)
    WHERE tr.timestamp BETWEEN m.s AND m.s + 6 * 3600 AND tr.price > 0 AND tr.price < 1""")
    for name, (lo, hi) in BANDS.items():
        for slow in (False, True):
            df = c.sql(f"""
              WITH x AS (SELECT * FROM b WHERE p >= {lo} AND p < {hi}),
              f AS (SELECT *, min(ts) OVER (PARTITION BY market_id, tok) AS t0 FROM x)
              SELECT market_id, any_value(event_id) AS event_id, any_value(ho) AS ho,
                     arg_min(p, ts) AS p, (tok = any_value(winner))::INT AS won
              FROM f WHERE ts >= t0 + {60 if slow else 0} GROUP BY market_id, tok""").df()
            df["ret"] = df.won - df.p - FEE * df.p * (1 - df.p)
            for ho in (False, True):
                g = df[df.ho == ho]
                ev = g.groupby("event_id").agg(r=("ret", "sum"), c=("p", "sum"))
                r, cc = ev.r.to_numpy(), ev.c.to_numpy()
                idx = np.random.default_rng(1).integers(0, len(r), (2000, len(r)))
                l, h = np.percentile(r[idx].sum(1) / cc[idx].sum(1), [2.5, 97.5])
                print(f"{name} {'slow' if slow else 'first'} {'HOLDOUT' if ho else 'train  '} "
                      f"markets {len(g):>6} win {g.won.mean():.3f} px {g.p.mean():.3f} "
                      f"ret/$ {r.sum() / cc.sum():+.2%} [{l:+.2%}, {h:+.2%}]")


if __name__ == "__main__":
    main()
