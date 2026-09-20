"""Buy underpriced exact-margin verticals: small premium, large payoff.

Two adjacent strikes isolate one margin. Long the lower line and short the
higher one pays $1 if the margin lands exactly between them, and $0 otherwise,
so the most you can lose is the premium. On `clmsn-cah` the margin-3 vertical
costs 0.010 on executable prices and a 3-point margin happens ~5.7% of the
time: 99:1 reward to risk, EV +0.047 per share, 4.7x the amount risked.

This is NOT the arbitrage the bot runs. You can lose here. What you get for
that is a payoff shape the arbitrage cannot produce.

ESTIMATING THE TRUE PROBABILITY, properly. Unconditional P(|margin| = k) is
wrong: a 3-point margin is far likelier in a pick'em than in a 20-point
mismatch. So this fits a smooth normal to the game's OWN ladder to get its
centre and width, then multiplies by a key-number "lumpiness ratio" measured
from history:

    R(k) = P_empirical(|margin| = k) / P_smooth(|margin| = k)

R is the part that transfers between games - football scores cluster on 3 and 7
regardless of who is favoured - while the ladder supplies everything
game-specific. NFL R(3) is about 5.0 and R(7) about 2.2.

    python run_keyvertical.py --slug-prefix asc-cfb-clmsn-cah-2026-09-25

CAVEATS that decide whether this is worth doing:
  * Margining is unknown. If the venue nets the two legs, capital is the 0.010
    premium and the return is enormous. If it does not, capital is ~$1.01 a
    share and the expected return is ~5% - no better than the arbitrage, for
    real risk. The trial's collateral usage answers this.
  * The settlement semantics are still inferred. On the arbitrage a sign error
    costs the credit; here it means betting on the wrong margins entirely.
  * R is measured against a normal, so it inherits that model's shape.
"""

import argparse
import math
import os
import time

SCORES = os.path.join("data", "scores_{league}.csv")


def ncdf(x, mu, sd):
    return 0.5 * (1.0 + math.erf((x - mu) / (sd * math.sqrt(2.0))))


def fit_ladder(quotes):
    """(mu, sd) of the normal that best matches the ladder's own prices.

    The contract at line L pays iff margin > -L, so its price should be
    1 - N(-L; mu, sd). Grid search: cheap, no scipy, and robust to the
    monotonicity violations that would break an analytic fit.
    """
    pts = [(k, (q["bid"] + q["ask"]) / 2.0) for k, q in quotes.items()
           if q.get("bid") is not None and q.get("ask") is not None]
    pts = [(k, p) for k, p in pts if 0.01 < p < 0.99]
    if len(pts) < 4:
        return None, None
    best, bmu, bsd = None, None, None
    for mu in [x * 0.5 for x in range(-80, 81)]:
        for sd in [x * 0.5 for x in range(6, 81)]:
            err = sum((1.0 - ncdf(-k, mu, sd) - p) ** 2 for k, p in pts)
            if best is None or err < best:
                best, bmu, bsd = err, mu, sd
    return bmu, bsd


def lumpiness(league):
    """R(k) = how much margin k spikes above its NEIGHBOURS.

    An earlier version divided the empirical frequency by a global normal fitted
    to |margin|, which gave R(3) = 6.0 - nonsense, because it was measuring
    "3 is far below the mean absolute margin" rather than "3 is a spike". A
    folded margin distribution is nothing like a normal, so that denominator
    was meaningless.

    Comparing each margin to the average of k+-1 and k+-2 isolates the local
    spike, which is what a key number IS, and is independent of the global
    shape. NFL gives R(3) ~ 3.2 and R(7) ~ 2.1.
    """
    path = SCORES.format(league=league)
    if not os.path.exists(path):
        return None
    import pandas as pd
    m = pd.read_csv(path)["margin"].abs()
    m = m[m > 0]
    n = len(m)
    counts = m.value_counts()
    p = {k: int(counts.get(k, 0)) / n for k in range(1, 61)}
    out = {}
    for k in range(2, 45):
        nb = [p.get(j, 0.0) for j in (k - 2, k - 1, k + 1, k + 2) if j >= 1]
        base = sum(nb) / len(nb) if nb else 0.0
        if base > 1e-5:
            out[k] = p.get(k, 0.0) / base
    out[1] = p.get(1, 0.0) / max((p.get(2, 0.0) + p.get(3, 0.0)) / 2, 1e-5)
    return out, n


def main():
    ap = argparse.ArgumentParser(description="Underpriced exact-margin verticals.")
    ap.add_argument("--slug-prefix", required=True)
    ap.add_argument("--league", default="cfb", choices=("cfb", "nfl"))
    ap.add_argument("--pause", type=float, default=0.6)
    ap.add_argument("--min-ev", type=float, default=0.005)
    ap.add_argument("--near", type=int, default=16)
    args = ap.parse_args()

    from run_ladder import parse_strike
    from src.pm_us.client import UsClient
    c = UsClient()
    ks = {}
    for p in c.all_programs():
        base, k = parse_strike(p.get("slug"))
        if base == args.slug_prefix:
            ks[k] = p["slug"]
    if len(ks) < 4:
        raise SystemExit(f"no ladder for {args.slug_prefix!r}")

    want = sorted(sorted(ks, key=lambda k: abs(k))[: args.near])
    quotes = {}
    for k in want:
        for attempt in range(3):
            try:
                b, a, _s = c.book_levels(ks[k])
                quotes[k] = {"bid": b[0][0] if b else None, "bid_sz": b[0][1] if b else 0,
                             "ask": a[0][0] if a else None, "ask_sz": a[0][1] if a else 0}
                break
            except Exception:
                time.sleep(1.5 * (2 ** attempt))
        time.sleep(args.pause)
    c.close()

    mu, sd = fit_ladder(quotes)
    if mu is None:
        raise SystemExit("not enough two-sided strikes to fit the ladder")
    lump = lumpiness(args.league)
    if lump is None:
        raise SystemExit(f"run fetch_scores.py --league {args.league} first")
    R, n_games = lump
    print(f"{args.slug_prefix}")
    print(f"  ladder implies margin ~ Normal({mu:+.1f}, {sd:.1f})  "
          f"(fit to {len(quotes)} strikes)")
    print(f"  lumpiness R(k) from {n_games:,} {args.league.upper()} finals: "
          + ", ".join(f"{k}:{R[k]:.1f}x" for k in (1, 3, 6, 7, 10, 14) if k in R))

    print(f"\n  {'margin':>7} {'cost':>7} {'P(smooth)':>10} {'EV smooth':>10} "
          f"{'R':>6} {'EV +key':>8} {'R:R':>7} {'sh':>4}")
    order = sorted(quotes)
    rows = []
    for l1, l2 in zip(order[:-1], order[1:]):
        span = [m for m in range(int(-l2) - 2, int(-l1) + 3) if -l2 < m <= -l1]
        if len(span) != 1:
            continue
        m = span[0]
        q1, q2 = quotes[l1], quotes[l2]
        if None in (q1["bid"], q2["ask"]):
            continue
        cost = q2["ask"] - q1["bid"]        # buy the easier line, sell the harder
        if cost <= 0:
            continue                        # that is a monotonicity arb, not this
        smooth = ncdf(-l1, mu, sd) - ncdf(-l2, mu, sd)
        r = R.get(abs(m), 1.0)
        p_true = max(min(smooth * r, 1.0), 0.0)
        # EV on the smooth estimate alone assumes NOTHING about key numbers -
        # it only says the ladder disagrees with its own fitted shape. That is
        # the robust number; the R kicker is a bonus that can be wrong.
        ev_smooth = smooth - cost
        ev = p_true - cost
        sz = min(q2["ask_sz"], q1["bid_sz"])
        if ev_smooth < args.min_ev or sz < 1:
            continue       # gate on the ROBUST estimate, not the R-boosted one
        rows.append((ev, m, cost, smooth, r, p_true, sz, ev_smooth))
    for ev, m, cost, smooth, r, p_true, sz, ev_s in sorted(rows, reverse=True):
        rr = (1 - cost) / cost if cost > 0 else float("inf")
        print(f"  {m:>7} {cost:>7.3f} {smooth:>10.4f} {ev_s:>+10.3f} "
              f"{r:>6.1f}x {ev:>+8.3f} {rr:>6.0f}:1 {sz:>4.0f}")
    if not rows:
        print("  nothing with EV over the threshold.")
        return
    prem = sum(r[2] * r[6] for r in rows)
    ev_t = sum(r[7] * r[6] for r in rows)      # robust EV, no key-number kicker
    hit = sum(r[3] for r in rows)
    print(f"\n  BASKET of all {len(rows)}: premium ${prem:.2f} at risk, "
          f"EV ${ev_t:+.2f}, ~{hit:.0%} chance one pays")
    print(f"  Max loss is the premium. That is the 2:1-or-better shape - but it")
    print(f"  is a real bet, and P(true) leans on R transferring between games.")


if __name__ == "__main__":
    main()
