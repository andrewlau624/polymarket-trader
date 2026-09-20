"""Read research/ladder_trades.jsonl and answer: do the violations persist?

A violation that vanishes between sweeps was never tradeable - it was a stale
quote. One that shows up sweep after sweep is a standing inefficiency. That
distinction is the whole question, and a single sweep cannot answer it.

    python ladder_report.py
"""

import collections
import json
import os
import sys

LOG = os.path.join("research", "ladder_trades.jsonl")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else LOG
    if not os.path.exists(path):
        raise SystemExit(f"no log at {path} - run `make ladder-dry` first")
    recs = []
    for line in open(path):
        line = line.strip()
        if line:
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    sweeps = [r for r in recs if r.get("kind") == "sweep"]
    opps = [r for r in recs if r.get("kind") == "opportunity"]
    probes = [r for r in recs if r.get("kind") == "probe"]
    paired = [r for r in recs if r.get("kind") == "paired"]

    for p in probes:
        print(f"PROBE {p['result'].upper()} at {p['ts'][:19]} on {p.get('slug','?')}")
        if p["result"] == "rejected":
            print(f"  {str(p.get('error'))[:160]}")
    if probes:
        print()

    if not sweeps:
        print("no completed sweeps yet")
        return
    print(f"{len(sweeps)} sweeps | {len(opps)} opportunities logged | "
          f"{len(paired)} pairs actually placed")
    print(f"\n{'sweep':>6} {'when':>17} {'ladders':>8} {'$ opportunity':>14} {'min':>6}")
    for s in sweeps[-12:]:
        print(f"{s['sweep']:>6} {s['ts'][11:19]:>17} {s['ladders']:>8} "
              f"{s['opportunity']:>14.2f} {s['minutes']:>6.1f}")

    if sweeps:
        vals = [s["opportunity"] for s in sweeps]
        print(f"\n  opportunity per sweep: min ${min(vals):.2f}  "
              f"median ${sorted(vals)[len(vals) // 2]:.2f}  max ${max(vals):.2f}")
        print(f"  sports seen: {sweeps[-1].get('sports')}")

    if not opps:
        return
    # the real question: does the same violation come back?
    key = lambda r: (r["game"], r["sell"], r["buy"])
    seen = collections.Counter(key(r) for r in opps)
    n_sweeps = len(sweeps)
    print(f"\n== PERSISTENCE (out of {n_sweeps} sweeps) ==")
    print(f"  {'sweeps':>7} {'credit':>8} {'sz':>4}  violation")
    for (game, l1, l2), cnt in seen.most_common(15):
        last = [r for r in opps if key(r) == (game, l1, l2)][-1]
        print(f"  {cnt:>3}/{n_sweeps:<3} {last['credit']:>8.3f} {last['size']:>4} "
              f"  {game[:30]} sell {l1:+.1f} buy {l2:+.1f}")
    once = sum(1 for v in seen.values() if v == 1)
    print(f"\n  {once}/{len(seen)} violations appeared in only ONE sweep.")
    print("  Those are stale quotes, not inefficiencies - do not count them.")
    durable = [k for k, v in seen.items() if v >= max(2, n_sweeps // 2)]
    # the same violation recurring is ONE opportunity that keeps standing there,
    # not a new one each sweep. Value it once, at its latest size and credit.
    latest = {}
    for r in opps:
        if key(r) in durable:
            latest[key(r)] = r
    stock = sum(r["credit"] * r["size"] for r in latest.values())
    print(f"\n  {len(durable)} violations stand in at least half the sweeps.")
    print(f"  Their combined value is ${stock:.2f} - and that is a STOCK, not a")
    print(f"  per-sweep flow. The same violations recur because nobody has taken")
    print(f"  them; you capture each one ONCE, then hold until the game settles.")

    # what the capital cap actually allows, best credits first
    print(f"\n  what a given capital cap can actually take (~$0.96 a share):")
    ranked = sorted(latest.values(), key=lambda r: -r["credit"])
    for cap in (5, 9, 20, 50, 100):
        shares, got = int(cap / 0.96), 0.0
        for r in ranked:
            take = min(r["size"], shares)
            if take <= 0:
                break
            got += r["credit"] * take
            shares -= take
        print(f"    ${cap:>3} -> ${got:.2f}")
    print("  Sizes above are themselves capped by --max-capital, so raising the")
    print("  cap may reveal more depth than these rows show.")


if __name__ == "__main__":
    main()
