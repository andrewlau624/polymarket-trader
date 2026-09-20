"""Does this pay for itself? Measured inputs, no optimism.

Everything here is a number this project actually measured, not a projection:

  edge/cycle      $3.42 at $100 deployed  (ladder-report, 10 sweeps)
  cycle length    ~7 days   (CFB plays Thu-Sat; capital locks to settlement)
  depth ceiling   ~$3.4/cycle  (the $50->$100 marginal return already halves)

    python run_economics.py --hosting 18 --capital 100
    python run_economics.py --hosting 4 --capital 100 --cycles-per-week 3
"""

import argparse

# measured: capital -> lockable dollars per settlement cycle
CURVE = [(5, 0.20), (9, 0.36), (20, 0.80), (50, 2.06), (100, 3.42)]


def per_cycle(capital):
    if capital <= CURVE[0][0]:
        return capital * CURVE[0][1] / CURVE[0][0]
    for (c0, v0), (c1, v1) in zip(CURVE, CURVE[1:]):
        if capital <= c1:
            t = (capital - c0) / (c1 - c0)
            return v0 + t * (v1 - v0)
    # past the measured curve, depth binds: assume no further gain
    return CURVE[-1][1]


def main():
    ap = argparse.ArgumentParser(description="Is this worth running?")
    ap.add_argument("--hosting", type=float, default=18.0, help="$/month")
    ap.add_argument("--capital", type=float, default=100.0)
    ap.add_argument("--cycles-per-week", type=float, default=1.0,
                    help="1 = weekly CFB settlement. NBA ladders would be ~5.")
    ap.add_argument("--fill-rate", type=float, default=0.6,
                    help="fraction of found violations that actually fill both legs")
    args = ap.parse_args()

    gross_cycle = per_cycle(args.capital) * args.fill_rate
    weekly = gross_cycle * args.cycles_per_week
    yearly = weekly * 52
    cost = args.hosting * 12

    print(f"capital ${args.capital:.0f} | {args.cycles_per_week:g} cycles/week | "
          f"fill rate {args.fill_rate:.0%} | hosting ${args.hosting:.0f}/mo\n")
    print(f"  per cycle      ${gross_cycle:>8.2f}")
    print(f"  per week       ${weekly:>8.2f}")
    print(f"  per year       ${yearly:>8.2f}")
    print(f"  hosting        ${-cost:>8.2f}")
    print(f"  NET            ${yearly - cost:>8.2f}   "
          f"{'VIABLE' if yearly > cost else 'LOSES MONEY'}")

    print(f"\n  break-even hosting at this setup: "
          f"${yearly / 12:.2f}/month")
    need = None
    for cap in range(10, 501, 10):
        if per_cycle(cap) * args.fill_rate * args.cycles_per_week * 52 > cost:
            need = cap
            break
    print(f"  capital needed to cover ${args.hosting:.0f}/mo: "
          + (f"${need}" if need else "IMPOSSIBLE - depth caps the edge first"))

    print("\n  the two levers, ranked by certainty:")
    for h in (18, 6, 4, 0):
        n = yearly - h * 12
        print(f"    hosting ${h:>2}/mo -> net ${n:>7.2f}/yr"
              + ("   <- current" if h == args.hosting else ""))
    print()
    for cyc, label in ((1, "CFB only, weekly"), (3, "if NBA ladders appear"),
                       (5, "NBA nightly")):
        y = per_cycle(args.capital) * args.fill_rate * cyc * 52
        print(f"    {label:<24} -> ${y:>7.2f}/yr gross")

    print("\n  Cutting hosting is CERTAIN. More cycles is not - it depends on the")
    print("  venue listing NBA ladders, which has not happened yet.")


if __name__ == "__main__":
    main()
