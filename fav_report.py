"""Does buying favourites - or longshots - pay on Polymarket US? Paper, from favs.py.

    python fav_report.py                 # live-listing paper (research/favs.jsonl)
    python fav_report.py --dry           # the dry-run file

Per bucket: win rate vs average entry price (the calibration question) and
per-share P&L after fees for holding to settlement and for cashing out at
each target. The CI is a bootstrap over GAMES, not positions: twelve props
on one fight are one observation of that fight, not twelve.

Pre-registered, before any data, per band: worth real money only if the
best exit's per-share mean is > 0 with the whole 95% CI above 0, on at least
300 settled positions across at least 60 games. Anything less is noise.
Longshot P&L is per share of a 2c ticket: compare it to the entry price,
not to the favourites' numbers.
"""

import argparse
import random
from collections import defaultdict

from src.income.favs import LEDGER
from src.pm_us.jsonlog import iter_records

MIN_N, MIN_GAMES = 300, 60
PX_BUCKETS = {"fav": ((0.85, 0.88), (0.88, 0.91), (0.91, 0.94), (0.94, 0.961)),
              "long": ((0.001, 0.01), (0.01, 0.02), (0.02, 0.035), (0.035, 0.051))}


def boot_ci(rows, key, reps=2000, seed=7):
    """95% CI of the per-position mean, resampling whole games."""
    by_game = defaultdict(list)
    for r in rows:
        by_game[r["game"]].append(r[key])
    games = list(by_game.values())
    if len(games) < 2:
        return None, None
    rnd = random.Random(seed)
    means = []
    for _ in range(reps):
        pick = [rnd.choice(games) for _ in games]
        vals = [v for g in pick for v in g]
        means.append(sum(vals) / len(vals))
    means.sort()
    return means[int(0.025 * reps)], means[int(0.975 * reps)]


def exits_of(rows):
    names = sorted({k for r in rows for k in r if k.startswith("x")},
                   key=lambda k: float(k[1:].rstrip("x")))
    return ["hold"] + [k for k in names if all(k in r for r in rows)]


def table(title, rows, keyfn, order=None):
    EXITS = exits_of(rows)
    groups = defaultdict(list)
    for r in rows:
        groups[keyfn(r)].append(r)
    print(f"\n{title}")
    print(f"  {'bucket':<14}{'n':>6}{'games':>7}{'avg px':>8}{'win':>7}{'edge':>8}  "
          + "".join(f"{e:>9}" for e in EXITS))
    for k in (order or sorted(groups)):
        g = groups.get(k)
        if not g:
            continue
        n = len(g)
        px = sum(r["px"] for r in g) / n
        win = sum(r["payout"] for r in g) / n
        cells = "".join(f"{sum(r[e] for r in g) / n:+9.4f}" for e in EXITS)
        print(f"  {str(k):<14}{n:>6}{len({r['game'] for r in g}):>7}{px:>8.4f}"
              f"{win:>7.3f}{win - px:>+8.3f}  {cells}")


def px_bucket(r):
    for lo, hi in PX_BUCKETS[r["band"]]:
        if lo <= r["px"] < hi:
            return f"{lo:.3f}-{hi:.3f}"
    return "other"


def hours_bucket(r):
    h = r["hours"]
    return "<6h" if h < 6 else "6-24h" if h < 24 else "1-3d" if h < 72 else "3-7d"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--ledger", default=None)
    a = ap.parse_args()
    path = a.ledger or (LEDGER.replace(".jsonl", ".dry.jsonl") if a.dry else LEDGER)
    opened, rows, unresolved = 0, [], 0
    for r in iter_records(path):
        if r["kind"] == "fav_open":
            opened += 1
        elif r["kind"] == "fav_close":
            if r.get("reason") == "settled":
                rows.append(r)
            else:
                unresolved += 1
    print(f"== favourites + longshots (PAPER) | {path} ==")
    print(f"  opened {opened} | settled {len(rows)} | unresolved {unresolved} | "
          f"still open {opened - len(rows) - unresolved}")
    if not rows:
        print("  nothing settled yet - first results land as this week's games resolve")
        return
    for r in rows:
        r.setdefault("band", "fav")
    for band, title in (("fav", "FAVOURITES 0.85-0.96"), ("long", "LONGSHOTS 0.001-0.05")):
        report_band(title, [r for r in rows if r["band"] == band])


def report_band(title, rows):
    print(f"\n#### {title}: {len(rows)} settled")
    if not rows:
        return
    table("by entry price", rows, px_bucket)
    table("by time to game at entry", rows, hours_bucket, ["<6h", "6-24h", "1-3d", "3-7d"])
    table("by market type", rows, lambda r: r["type"])
    games = len({r["game"] for r in rows})
    print(f"\n  per-share P&L, 95% CI over {games} games:")
    best = None
    for e in exits_of(rows):
        m = sum(r[e] for r in rows) / len(rows)
        lo, hi = boot_ci(rows, e)
        ci = "n/a" if lo is None else f"[{lo:+.4f}, {hi:+.4f}]"
        print(f"    {e:<7} {m:+.4f}  {ci}")
        if lo is not None and (best is None or lo > best[1]):
            best = (e, lo, m)
    worst = sorted(r["hold"] for r in rows)[:3]
    print(f"  worst single holds: {', '.join(f'{x:+.3f}' for x in worst)}")
    enough = len(rows) >= MIN_N and games >= MIN_GAMES
    go = enough and best is not None and best[1] > 0
    print(f"  verdict: {'GO' if go else 'NO' if enough else 'not enough data'}  "
          f"(needs n>={MIN_N}, games>={MIN_GAMES}, best exit's CI above 0; "
          f"have n={len(rows)}, games={games}"
          + (f", best {best[0]} CI low {best[1]:+.4f})" if best else ")"))


if __name__ == "__main__":
    main()
