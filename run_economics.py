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


def fee_per_share(price, rate):
    """Polymarket's published curve: rate * p * (1-p) per share, per leg.

    charanchivukula reports live losses that were "mostly fees" against a
    backtest that modelled none. At rate=0.07 and p=0.58 this is 0.0171 a
    share, so a two-leg pair pays 0.034 against credits of 0.025-0.040 - which
    would make the whole trade negative. Whether it applies on Polymarket US is
    unsettled; run_fees.py measures it.
    """
    return rate * price * (1.0 - price)


def per_cycle(capital):
    if capital <= CURVE[0][0]:
        return capital * CURVE[0][1] / CURVE[0][0]
    for (c0, v0), (c1, v1) in zip(CURVE, CURVE[1:]):
        if capital <= c1:
            t = (capital - c0) / (c1 - c0)
            return v0 + t * (v1 - v0)
    # past the measured curve, depth binds: assume no further gain
    return CURVE[-1][1]


def per_cycle_maker(capital, ladders=50, verticals=4, ev=0.05, base=5,
                    per_share=0.98):
    """Resting has NO depth limit, so capacity is breadth x size, not the book.

    The measured CURVE flattens past $50 because TAKING the touch caps at ~20
    shares a violation and fees make every deeper level negative. That
    flattening is a property of taking, not of the edge. A maker is adding
    liquidity rather than consuming it, so what binds is how many PLACES you
    rest - and that is the number of ladders scanned.

    Linear in capital until every slot is funded at the base size, then the
    surplus deepens the best positions at a discounted rate.
    """
    slots = max(ladders * verticals, 1)
    fundable = capital / per_share
    shares = min(fundable, slots * base)
    surplus = max(fundable - slots * base, 0.0)
    return shares * ev + surplus * ev * 0.5


def main():
    ap = argparse.ArgumentParser(description="Is this worth running?")
    ap.add_argument("--hosting", type=float, default=18.0, help="$/month")
    ap.add_argument("--capital", type=float, default=100.0)
    ap.add_argument("--cycles-per-week", type=float, default=1.0,
                    help="1 = weekly CFB settlement. NBA ladders would be ~5.")
    ap.add_argument("--ladders", type=int, default=50,
                    help="ladders scanned per sweep. Breadth is the scaling "
                         "lever; --max-games 0 scans all of them.")
    ap.add_argument("--verticals-per-ladder", dest="verticals", type=int,
                    default=4, help="tradeable verticals per ladder (5 were "
                                    "found on the one real ladder examined)")
    ap.add_argument("--maker", action="store_true",
                    help="model RESTING both legs: captures the full spread on "
                         "each and earns the 0.0125 rebate instead of paying "
                         "the 0.0695 taker fee. Lower fill rate, far higher net.")
    ap.add_argument("--spread", type=float, default=0.008,
                    help="combined width captured by resting inside both books")
    ap.add_argument("--fee-rate", type=float, default=0.0,
                    help="fee curve coefficient: fee = rate*p*(1-p) per share "
                         "per leg. 0 assumes none (current belief); 0.07 is the "
                         "published Polymarket figure. Charged on BOTH legs.")
    ap.add_argument("--avg-price", type=float, default=0.58,
                    help="typical leg price, for the fee calculation")
    ap.add_argument("--avg-credit", type=float, default=0.03,
                    help="typical credit per share, for the fee comparison")
    ap.add_argument("--fill-rate", type=float, default=0.6,
                    help="fraction of found violations that actually fill both legs")
    args = ap.parse_args()

    from src.pm_us.fees import pair_cost
    if args.maker:
        # resting inside the spread captures the full width on BOTH legs and
        # earns the rebate rather than paying the taker fee
        eff_credit = args.avg_credit + args.spread - 2 * 0.001
        fee2 = pair_cost(args.avg_price, args.avg_price - args.avg_credit,
                         1, True, True)
    else:
        eff_credit = args.avg_credit
        fee2 = pair_cost(args.avg_price, args.avg_price - args.avg_credit,
                         1, False, False)
    net_per_share = eff_credit - fee2
    survive = max(net_per_share, 0.0) / max(args.avg_credit, 1e-9)
    base_cycle = (per_cycle_maker(args.capital, args.ladders, args.verticals,
                                  max(net_per_share, 0.0))
                  if args.maker else per_cycle(args.capital) * survive)
    gross_cycle = base_cycle * args.fill_rate
    weekly = gross_cycle * args.cycles_per_week
    yearly = weekly * 52
    cost = args.hosting * 12

    print(f"capital ${args.capital:.0f} | {args.cycles_per_week:g} cycles/week | "
          f"fill rate {args.fill_rate:.0%} | hosting ${args.hosting:.0f}/mo")
    mode = "REST both legs (maker)" if args.maker else "TAKE both legs"
    print(f"execution: {mode}")
    print(f"  credit {args.avg_credit:.3f}" +
          (f" + spread {args.spread:.3f} captured = {eff_credit:.3f}"
           if args.maker else "") +
          f"   fees {fee2:+.4f}   NET {net_per_share:+.4f}/share")
    if net_per_share <= 0:
        print("  NEGATIVE AFTER FEES at these prices.")
    print()
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

    if args.maker:
        print("\n  MODELLED, NOT MEASURED: the maker fill rate is a guess. Everything")
        print("  above scales off it linearly, so treat these figures as a shape")
        print("  (linear then saturating) rather than as a forecast. The first")
        print("  cron runs measure the real rate.")
    print("\n  Cutting hosting is CERTAIN. More cycles is not - it depends on the")
    print("  venue listing NBA ladders, which has not happened yet.")
    print("\n  NOTE: the curve above was measured scanning 12 strikes per game.")
    print("  Cron scans 24 on 25 games for 77% fewer API calls, so the real")
    print("  number may be higher - the ladder wings have never been looked at.")


if __name__ == "__main__":
    main()
