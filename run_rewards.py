#!/usr/bin/env python3
"""Why did every liquidity reward come back SKIPPED?

Pools of $8,500-32,000 sit on markets this account already quotes, and four
rewards have been earned gross and credited $0.0000, every one SKIPPED. That
is the largest unexplained number in the project: one period of one pool is
worth more than a year of the arbitrage.

The bot reads rewardPool, discountFactor and targetSize. If the venue also
publishes a qualification rule - a minimum size, a maximum spread, a two-sided
requirement, a minimum time in book - we have never read it, and would fail it
silently. This dumps every field the venue actually returns so the rule can be
read rather than guessed.

    python3 run_rewards.py              # program + period + earnings fields
    python3 run_rewards.py --earnings   # per-reward detail, incl. skip reasons
"""

import argparse
import json
import sys

sys.path.insert(0, ".")
from src.pm_us.client import UsClient  # noqa: E402

# Fields whose names suggest a qualification rule we are not honouring.
SUSPECT = ("min", "max", "spread", "size", "qual", "eligib", "require",
           "side", "depth", "tick", "distance", "threshold", "status", "reason")


def flag(key):
    k = key.lower()
    return "  <-- QUALIFICATION?" if any(s in k for s in SUSPECT) else ""


def dump(label, obj, indent="  "):
    print(f"\n{label}")
    if not obj:
        print(f"{indent}(nothing returned)")
        return
    if not isinstance(obj, dict):
        print(f"{indent}{obj!r}")
        return
    for k, v in sorted(obj.items()):
        if isinstance(v, (dict, list)):
            n = len(v)
            print(f"{indent}{k:<28} <{type(v).__name__} len={n}>{flag(k)}")
            if isinstance(v, list) and v and isinstance(v[0], dict):
                for kk, vv in sorted(v[0].items()):
                    print(f"{indent}    {kk:<24} {str(vv)[:40]:<40}{flag(kk)}")
        else:
            print(f"{indent}{k:<28} {str(v)[:44]:<44}{flag(k)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--earnings", action="store_true",
                    help="per-reward rows, looking for a skip reason")
    ap.add_argument("--raw", action="store_true", help="full JSON, no filtering")
    a = ap.parse_args()

    c = UsClient()
    try:
        prog, period = c.program_sample()
        if a.raw:
            print(json.dumps({"program": prog, "period": period}, indent=2)[:6000])
        else:
            dump("PROGRAM fields", prog)
            dump("TIME PERIOD fields", period)

        print("\nfields the bot currently reads:")
        print("  rewardPool  discountFactor  targetSize  period  start  end  status")
        if isinstance(period, dict):
            unread = [k for k in period
                      if k not in ("rewardPool", "discountFactor", "targetSize",
                                   "period", "start", "end", "status")]
            print(f"\nfields it IGNORES ({len(unread)}): {', '.join(unread) or 'none'}")

        if a.earnings:
            e = c.earnings()
            if a.raw:
                print(json.dumps(e, indent=2)[:6000])
            else:
                rows = e.get("earnings", []) if isinstance(e, dict) else []
                dump("EARNINGS envelope", e if isinstance(e, dict) else {})
                print(f"\n{len(rows)} reward row(s):")
                for r in rows[:10]:
                    print(f"\n  --- {r.get('marketSlug', '?')}")
                    for k, v in sorted(r.items()):
                        print(f"      {k:<26} {str(v)[:44]:<44}{flag(k)}")
    finally:
        c.close()

    print("""
READ THE OUTPUT FOR:
  * any min/max size or spread on the period  -> our quotes may be too small
    or too wide to qualify at all
  * a two-sided or both-outcomes requirement  -> --buy-only quoted ONE side,
    which is the most likely single explanation for SKIPPED
  * a minimum time-in-book                    -> orders cancelled each cycle
    never mature
  * an explicit skip reason on an earnings row -> stop guessing, read it
""")


if __name__ == "__main__":
    main()
