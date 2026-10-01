"""What did RESTING orders earn, by category x price x hours-before-close? (global dump)

    research/.venv-data/bin/python hunt/maker.py

Every fill has a resting maker and an aggressive taker. The taker's effective buy
is (tok, p) as in build.py, so the maker effectively bought the OTHER token at
1 - p. Maker P&L per fill = won(other) - (1 - p), plus Polymarket US's maker
rebate 0.0125 q(1-q). Weighted by tokens filled, summed per EVENT, CI by
resampling events. TRAIN = markets closed before 2026-03-01, HOLDOUT after.

Timing uses the SCHEDULED end date (markets.end_date, known when you trade), not
the last trade: a 'will X happen by June 30' market closes early when X happens,
so hours-before-last-trade leaks the outcome and flatters the No side. Fills
after the scheduled end (in-play sport, late resolutions) get bucket 'past end'.

This is the average resting order. A newcomer joins the back of the queue and
is likelier to be the stale quote that gets picked off, so treat these numbers
as an upper bound for a small maker.
"""

import duckdb
import numpy as np

D = "research/pmdata"
SPLIT = 1772323200
BANDS = [0.0, 0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.90, 0.95, 0.98, 1.0]
HOURS = [0, 1, 6, 24, 72, 168, 720, 10 ** 6]


def main():
    c = duckdb.connect()
    c.sql("SET memory_limit='10GB'; SET threads=8; SET preserve_insertion_order=false; SET enable_progress_bar=false")
    c.sql(f"SET temp_directory='{D}/tmp'")
    band = "CASE " + " ".join(f"WHEN q < {b} THEN {i}" for i, b in enumerate(BANDS[1:])) + " ELSE 99 END"
    hrs = "CASE WHEN h < 0 THEN -1 " + " ".join(f"WHEN h < {b} THEN {i}" for i, b in enumerate(HOURS[1:])) + " ELSE 99 END"
    c.sql(f"""
    CREATE OR REPLACE TABLE mev AS
    WITH f AS (
      SELECT m.cat, m.event_id, m.t_end >= {SPLIT} AS ho, tr.token_amount AS n,
             -- maker effectively bought the token the taker did NOT buy, at 1 - taker price
             CASE WHEN tr.taker_direction = 'BUY' THEN 1 - tr.price ELSE tr.price END AS q,
             CASE WHEN tr.taker_direction = 'BUY'
                  THEN (tr.nonusdc_side <> m.winner)::INT ELSE (tr.nonusdc_side = m.winner)::INT END AS won,
             (epoch(m.end_date) - tr.timestamp) / 3600.0 AS h
      FROM read_parquet('{D}/trades.parquet') tr JOIN read_parquet('{D}/mk.parquet') m USING (market_id)
      WHERE tr.price > 0 AND tr.price < 1
    )
    SELECT cat, ho, {band} AS band, {hrs} AS hb, event_id,
           sum(n * (won - q + 0.0125 * q * (1 - q))) AS pnl, sum(n * q) AS cost, sum(n) AS tokens, count(*) AS fills
    FROM f GROUP BY ALL""")
    df = c.sql("SELECT * FROM mev").df()
    df.to_parquet(f"{D}/maker_events_sched.parquet")
    rows = []
    for (cat, band, hb), g in df.groupby(["cat", "band", "hb"]):
        out = {"cat": cat, "band": band, "hb": hb}
        ok = True
        for ho, gg in g.groupby("ho"):
            r, cc = gg.pnl.to_numpy(), gg.cost.to_numpy()
            if len(r) < 60:
                ok = False
                break
            idx = np.random.default_rng(1).integers(0, len(r), (600, len(r)))
            lo, hi = np.percentile(r[idx].sum(1) / cc[idx].sum(1), [2.5, 97.5])
            out["HO" if ho else "TR"] = (r.sum() / cc.sum(), lo, hi, len(r), cc.sum())
        if ok and "TR" in out and "HO" in out:
            rows.append(out)
    good = sorted([x for x in rows if x["TR"][1] > 0 and x["HO"][1] > 0], key=lambda x: -x["HO"][4] * x["HO"][0])
    when = lambda hb: "past sched end" if hb < 0 else f"{HOURS[hb]}-{HOURS[hb + 1]}h to sched end"
    lab = lambda x: f"{x['cat']:<18} q {BANDS[x['band']]:.2f}-{BANDS[x['band'] + 1]:.2f} {when(x['hb'])}"
    fmt = lambda t: f"{t[0]:+6.1%} [{t[1]:+.1%},{t[2]:+.1%}] ev {t[3]:>5} ${t[4] / 1e6:6.1f}M"
    print(f"{len(rows)} cells with >= 60 events in both periods; makers earned with CI > 0 in BOTH: {len(good)}")
    print(f"  {'cell':<60} {'TRAIN maker ret/$':<42} HOLDOUT maker ret/$")
    for x in good[:60]:
        print(f"  {lab(x):<60} {fmt(x['TR']):<42} {fmt(x['HO'])}")
    tot = df.groupby("ho").apply(lambda g: g.pnl.sum() / g.cost.sum(), include_groups=False)
    print("\nall resting orders, all categories: " + ", ".join(f"{'HOLDOUT' if k else 'TRAIN'} {v:+.2%}" for k, v in tot.items()))


if __name__ == "__main__":
    main()
