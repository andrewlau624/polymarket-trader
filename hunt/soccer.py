"""S5: Polymarket soccer prices against Pinnacle (TEST_PLAN.md).

    research/.venv-data/bin/python hunt/soccer.py

Football-Data.co.uk CSVs (research/fd) give Pinnacle pre-closing (PSH/D/A) and
closing (PSCH/D/A) odds, the UK-time kickoff and the result. Polymarket lists
each match as three yes/no markets, '<event>-<home>', '<event>-<away>',
'<event>-draw', ending at kickoff, with event_title 'Home FC vs. Away FC'.
"""

import glob
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd

D = "research/pmdata"
FD = "research/fd"
SPLIT = pd.Timestamp("2026-03-01", tz="UTC")
FEE = 0.0695
EDGE = 0.02
UK = ZoneInfo("Europe/London")
LEAGUE = {"epl": "E0", "elc": "E1", "lal": "SP1", "es2": "SP2", "bun": "D1", "bl2": "D2",
          "sea": "I1", "it2": "I2", "fl1": "F1", "fr2": "F2", "ere": "N1", "por": "P1",
          "tur": "T1", "sco": "SC0", "bel": "B1", "gre": "G1",
          "mls": "USA", "mex": "MEX", "bra": "BRA", "arg": "ARG", "jap": "JPN"}
STOP = {"fc", "cf", "afc", "sc", "ac", "club", "de", "the", "cd", "ud", "sd", "sv", "fk", "sk", "bk",
        "if", "ss", "as", "us", "rc", "ca", "united", "city", "town", "real"}


def toks(s):
    s = str(s).lower().replace("&", " ").replace("nott'm", "nottingham")
    s = re.sub(r"^man ", "manchester ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return {t for t in s.split() if t not in STOP and len(t) > 1}


def sim(a, b):
    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return 0.0
    hit = sum(1 for x in ta if any(x == y or (len(x) >= 4 and (y.startswith(x) or x.startswith(y))) for y in tb))
    return hit / min(len(ta), len(tb))


def load_fd():
    rows = []
    for fp in glob.glob(f"{FD}/*.csv"):
        df = pd.read_csv(fp, encoding="utf-8-sig", on_bad_lines="skip")
        new = "Home" in df.columns
        code = os.path.basename(fp).split("_", 1)[1][:-4]
        if new:
            df = df[df["Season"].astype(str).str.contains("2025|2026")]
            df = df.rename(columns={"Home": "HomeTeam", "Away": "AwayTeam", "Res": "FTR"})
        for _, r in df.iterrows():
            try:
                dt = datetime.strptime(f"{r['Date']} {r.get('Time', '15:00')}", "%d/%m/%Y %H:%M")
            except (ValueError, TypeError):
                continue
            ko = pd.Timestamp(dt.replace(tzinfo=UK)).tz_convert("UTC")
            rows.append({"code": code, "ko": ko, "home": r["HomeTeam"], "away": r["AwayTeam"],
                         "res": r["FTR"], **{k: r.get(k) for k in
                                             ("PSH", "PSD", "PSA", "PSCH", "PSCD", "PSCA")}})
    fd = pd.DataFrame(rows)
    return fd[fd.ko >= pd.Timestamp("2025-07-01", tz="UTC")]


def fair(o):
    inv = [1 / x if x and x > 1 else np.nan for x in o]
    s = np.nansum(inv)
    return [x / s for x in inv] if not np.isnan(inv).any() else None


def pm_events(c):
    leagues = "', '".join(LEAGUE)
    return c.sql(f"""
      SELECT id AS market_id, event_slug, slug, event_title, end_date, question, token1, token2,
             lower(split_part(slug, '-', 1)) AS lg, outcome_prices
      FROM read_parquet('{D}/markets.parquet')
      WHERE lower(split_part(slug, '-', 1)) IN ('{leagues}') AND created_at >= '2025-07-01'
        AND (question ILIKE 'will % win on %' OR question ILIKE '%end in a draw%')
        AND event_slug NOT LIKE '%more-markets%'""").df()


def match(fd, ev):
    """[(fd row index, {'H': market_id, 'D': ..., 'A': ...})]"""
    ev["end_date"] = pd.to_datetime(ev.end_date, utc=True)
    groups = {}
    for es, g in ev.groupby("event_slug"):
        title = g.event_title.iloc[0] or ""
        if " vs. " not in title:
            continue
        home, away = title.split(" vs. ", 1)
        legs = {}
        for _, r in g.iterrows():
            q = r.question.lower()
            if "draw" in q:
                legs["D"] = r
            elif sim(q, home) > sim(q, away):
                legs["H"] = r
            else:
                legs["A"] = r
        if len(legs) == 3:
            groups[es] = (LEAGUE[g.lg.iloc[0]], g.end_date.iloc[0], home, away, legs)
    by_code = {}
    for es, v in groups.items():
        by_code.setdefault(v[0], []).append((es, v))
    out = []
    for i, r in fd.iterrows():
        cands = []
        for es, (code, ko, home, away, legs) in by_code.get(r.code, []):
            if abs((ko - r.ko).total_seconds()) > 3 * 3600:
                continue
            s = min(sim(r.home, home), sim(r.away, away))
            if s >= 0.5:
                cands.append((s, legs))
        if len(cands) == 1 or (len(cands) > 1 and sorted(c[0] for c in cands)[-1] >
                               sorted(c[0] for c in cands)[-2]):
            out.append((i, max(cands, key=lambda c: c[0])[1]))
    return out


def main():
    c = duckdb.connect()
    c.sql("SET memory_limit='10GB'; SET threads=8")
    fd = load_fd()
    ev = pm_events(c)
    pairs = match(fd, ev)
    print(f"Football-Data matches since 2025-07: {len(fd):,} | Polymarket 3-way events: "
          f"{ev.event_slug.nunique():,} | paired: {len(pairs):,}")
    legs = []
    for i, L in pairs:
        r = fd.loc[i]
        for side, odds_pre, odds_cl in (("H", r.PSH, r.PSCH), ("D", r.PSD, r.PSCD), ("A", r.PSA, r.PSCA)):
            legs.append({"fd": i, "side": side, "market_id": L[side].market_id, "ko": r.ko,
                         "won": int(r.res == side), "code": r.code})
    lg = pd.DataFrame(legs)
    for name, cols in (("cl", ("PSCH", "PSCD", "PSCA")), ("pre", ("PSH", "PSD", "PSA"))):
        f = fd.loc[lg.fd.unique(), list(cols)].apply(lambda r: fair(list(r)), axis=1)
        mp = {k: v for k, v in f.items()}
        lg[f"fair_{name}"] = [None if mp.get(i) is None else mp[i]["HDA".index(s)] for i, s in zip(lg.fd, lg.side)]
    c.register("lg", lg[["market_id", "ko", "fd", "side", "won", "fair_cl", "fair_pre", "code"]])
    # every fill is one aggressive buy: BUY X at p, or SELL X at p == buy the other token at 1 - p
    tr = c.sql(f"""
      SELECT t.market_id,
             CASE WHEN t.taker_direction = 'BUY' THEN t.nonusdc_side
                  WHEN t.nonusdc_side = 'token1' THEN 'token2' ELSE 'token1' END AS tok,
             CASE WHEN t.taker_direction = 'BUY' THEN t.price ELSE 1 - t.price END AS price,
             t.timestamp AS ts, t.usd_amount AS usd,
             lg.fd, lg.side, lg.won, lg.fair_cl, lg.fair_pre, lg.code,
             epoch(lg.ko) AS ko
      FROM read_parquet('{D}/trades.parquet') t JOIN lg USING (market_id)
      WHERE t.timestamp BETWEEN epoch(lg.ko) - 43200 AND epoch(lg.ko) - 60
        AND t.price > 0 AND t.price < 1""").df()
    # a Yes token pays when the leg's outcome happened; a No token when it did not
    tr["yes"] = tr.tok == "token1"
    tr["pay"] = np.where(tr.yes, tr.won, 1 - tr.won)
    tr["koT"] = pd.to_datetime(tr.ko, unit="s", utc=True)
    print(f"aggressive buys in the 12 h before kickoff on paired legs: {len(tr):,}")

    # diagnostic: last Yes price before kickoff vs Pinnacle close
    last = tr[tr.yes & (tr.ts >= tr.ko - 1800)].sort_values("ts").groupby(["fd", "side"]).tail(1)
    last = last.dropna(subset=["fair_cl"])
    if len(last):
        bpm = ((last.price - last.won) ** 2).mean()
        bpin = ((last.fair_cl - last.won) ** 2).mean()
        print(f"Brier on {len(last):,} legs: Polymarket last price {bpm:.4f} | Pinnacle close {bpin:.4f} "
              f"| mean |gap| {np.abs(last.price - last.fair_cl).mean():.4f}")

    for name, fcol, win in (("PRIMARY  close, last 30 min", "fair_cl", 1800),
                            ("SECONDARY pre-close, last 12 h", "fair_pre", 43200)):
        t = tr[(tr.ts >= tr.ko - win)].dropna(subset=[fcol]).copy()
        t["fv"] = np.where(t.yes, t[fcol], 1 - t[fcol])
        t = t[t.price + FEE * t.price * (1 - t.price) <= t.fv - EDGE]
        e = t.sort_values("ts").groupby(["fd", "side", "tok"]).head(1)
        e = e.assign(ret=e.pay - e.price - FEE * e.price * (1 - e.price))
        print(f"\n{name}: {len(e):,} entries on {e.fd.nunique():,} matches")
        for lab, part in (("before 2026-03-01 (reported)", e[e.koT < SPLIT]),
                          ("2026-03-01 on (VERDICT)", e[e.koT >= SPLIT])):
            if len(part) < 5:
                print(f"  {lab}: too few")
                continue
            g = part.groupby("fd").agg(r=("ret", "sum"), c=("price", "sum"))
            rr, cc = g.r.to_numpy(), g.c.to_numpy()
            idx = np.random.default_rng(2).integers(0, len(rr), (2000, len(rr)))
            lo, hi = np.percentile(rr[idx].sum(1) / cc[idx].sum(1), [2.5, 97.5])
            mid = part.koT.median()
            h = [p.ret.sum() / p.price.sum() for p in (part[part.koT < mid], part[part.koT >= mid])]
            print(f"  {lab}: n={len(part)} matches={len(g)} win {part.pay.mean():.2f} avg px "
                  f"{part.price.mean():.3f} edge-at-entry {(part.fv - part.price).mean():+.3f} | "
                  f"ret/$ {rr.sum() / cc.sum():+.2%} [{lo:+.2%}, {hi:+.2%}] halves {h[0]:+.1%} / {h[1]:+.1%}")
        by = e.groupby("code").apply(lambda p: pd.Series({"n": len(p), "ret": p.ret.sum() / p.price.sum()}),
                                     include_groups=False)
        print("  by league:", ", ".join(f"{k} {int(v.n)}:{v.ret:+.0%}" for k, v in by.iterrows() if v.n >= 20))


if __name__ == "__main__":
    main()
