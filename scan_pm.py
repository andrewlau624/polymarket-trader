import argparse
import os

import yaml

from src.pm import arb, relations
from src.research import env as env_mod
from src.research import llm as llm_mod
from src.research import runner


def _fmt_legs(legs):
    return " + ".join(f"{l['side'].upper()} {l['price']:.3f}@{l['token'][:8]}" for l in legs)


def allocate(violations, bankroll):
    """Fractional-knapsack allocation: maximize profit under a capital cap.

    Each opportunity is independent and divisible, so greedily filling by
    ROI (edge / capital) is optimal. Returns (chosen, total_capital, total_profit).
    """
    ranked = sorted(
        [v for v in violations if v["capital"] > 0 and v["roi"] and v["roi"] > 0],
        key=lambda v: v["roi"],
        reverse=True,
    )
    remaining = float(bankroll)
    chosen = []
    total_profit = 0.0
    capital = 0.0
    for v in ranked:
        if remaining <= 0:
            break
        scale = min(1.0, remaining / v["capital"])
        size = v["size"] * scale
        profit = v["edge"] * size
        cap = v["cost_per_unit"] * size
        chosen.append({**v, "size": size, "profit": profit, "capital": cap, "scale": scale})
        remaining -= cap
        capital += cap
        total_profit += profit
    return chosen, capital, total_profit


def _mid_map(markets):
    """token -> midpoint straight from Gamma (free; no CLOB call)."""
    mids = {}
    for m in markets:
        for tok, price in zip(m.get("clob_token_ids", []), m.get("outcome_prices", [])):
            if price is not None:
                mids[tok] = price
    return mids


def _select_tokens(markets, rels, budget, exhaustive_tol=0.10, edge_proxy=0.005):
    """Choose which tokens to price, using free Gamma mids as a pre-screen.

    CLOB books are the expensive part, so only the tokens that look like they
    belong to a real relation get priced:
      1. complete neg-risk groups whose mid-sum is near 1 (exhaustive-set arb)
      2. implies relations whose mids already show the wrong ordering
      3. both sides of the most liquid remaining markets (yes/no arb)
    """
    mids = _mid_map(markets)
    needed = []
    seen = set()

    def add(tok):
        if tok and tok not in seen:
            seen.add(tok)
            needed.append(tok)

    no_of = {}
    for m in markets:
        y, n = arb._pair_tokens(m)
        if y:
            no_of[y] = n
        if n:
            no_of[n] = y

    groups = {}
    for m in markets:
        if m.get("neg_risk"):
            key = m.get("neg_risk_market_id") or m.get("event_id")
            groups.setdefault(key, []).append(m)

    scored = []
    for group in groups.values():
        yes = [arb._pair_tokens(m)[0] for m in group]
        if any(t is None or t not in mids for t in yes):
            continue  # incomplete or unpriced set -> cannot be a clean arb
        total = sum(mids[t] for t in yes)
        deviation = abs(total - 1.0)
        if deviation > exhaustive_tol:
            continue  # not a plausible exhaustive partition
        scored.append((deviation, yes))

    # biggest mid deviation first: most likely to hide a real spread arb
    scored.sort(key=lambda x: x[0], reverse=True)
    for _, yes in scored:
        if len(needed) + len(yes) > budget:
            continue
        for t in yes:
            add(t)

    for r in rels:
        if r["type"] != "implies":
            continue
        a, b = r["antecedent_token"], r["consequent_token"]
        if mids.get(a) is None or mids.get(b) is None:
            continue
        if mids[a] - mids[b] < edge_proxy:
            continue  # mids already ordered correctly; no spread to harvest
        add(b)
        add(no_of.get(a))

    rest = sorted(markets, key=lambda m: (m.get("liquidity") or 0), reverse=True)
    for m in rest:
        if len(needed) + 2 > budget:
            break
        y, n = arb._pair_tokens(m)
        add(y)
        add(n)

    return needed[:budget]


def run_scan(args, rc):
    print(f"Scanning up to {args.max_events} active events "
          f"(min_edge={args.min_edge}, min_profit={args.min_profit}, fee={args.fee})...")
    events = runner.fetch_active_events(
        limit=args.max_events, min_liquidity=args.min_liquidity
    )
    markets = [m for ev in events for m in ev["markets"]]
    n_neg = sum(1 for m in markets if m.get("neg_risk"))
    print(f"Fetched {len(events)} events / {len(markets)} markets ({n_neg} neg-risk).")

    rels = relations.default_relations(markets)
    n_implies = sum(1 for r in rels if r["type"] == "implies")
    print(f"Relations: yes_no + event_exhaustive + {n_implies} auto-discovered "
          f"threshold-monotonicity implies.")

    tokens = _select_tokens(markets, rels, args.max_tokens,
                            exhaustive_tol=args.exhaustive_tol)
    print(f"Pricing {len(tokens)} targeted tokens (budget {args.max_tokens})...")
    prices = runner.attach_token_prices(tokens, workers=args.workers)
    print(f"Priced {len(prices)} tokens.")

    violations = arb.check_relations(
        markets, prices, rels,
        min_edge=args.min_edge, min_profit=args.min_profit, fee=args.fee,
        exhaustive_tol=args.exhaustive_tol,
    )

    if not violations:
        print("No executable violations at these thresholds.")
        return []

    total = arb.total_profit(violations)
    print(f"\n{len(violations)} executable opportunities | "
          f"est. profit if fully filled = ${total:,.2f}")

    top = violations[: args.top]
    for v in top:
        edge_pct = v["edge"] * 100
        roi = f"{v['roi']*100:.2f}%" if v["roi"] else "n/a"
        print(f"\n[{v['type']}/{v['kind']}] {v['question'][:78]}")
        print(f"  edge={edge_pct:.3f}%  roi={roi}  size={v['size']:.1f} sh  "
              f"profit=${v['profit']:,.2f}  needs={v['requires']}")
        print(f"  {_fmt_legs(v['legs'])}")

    if args.bankroll:
        chosen, capital, profit = allocate(violations, args.bankroll)
        print(f"\n--- bankroll allocation (${args.bankroll:,.0f}) ---")
        print(f"  capital deployed=${capital:,.2f}  expected profit=${profit:,.2f}  "
              f"return={profit/capital*100 if capital else 0:.2f}%")
        for v in chosen[: args.top]:
            print(f"  ${v['profit']:>8,.2f}  ({v['roi']*100:5.2f}% roi, {v['scale']*100:5.1f}% filled) "
                  f"{v['type']}/{v['kind']} {v['question'][:50]}")
    return violations


def run_research(args, rc, tool, holdout_cfg):
    llm = llm_mod.MockLLM() if args.provider == "mock" else llm_mod.get_llm()
    if isinstance(llm, llm_mod.MockLLM):
        print("Using MockLLM (no API key set). Set OPENAI_API_KEY or ANTHROPIC_API_KEY for a real loop.")
    else:
        print(f"Using {type(llm).__name__}")

    print(f"Holdout: {len(holdout_cfg.get('event_slugs', []))} slugs, "
          f"fraction={holdout_cfg.get('fraction')}")

    for i in range(args.cycles):
        print(f"\n--- research cycle {i + 1}/{args.cycles} ---")
        result = runner.run_cycle(
            tool,
            llm,
            rc["proposals_dir"],
            rc["graveyard_path"],
            holdout_cfg,
            sample_events=rc["sample_events"],
            sample_markets=rc["sample_markets"],
            min_edge=rc["min_edge"],
            min_profit=rc.get("min_profit", 0.0),
            fee=rc.get("fee", 0.0),
        )
        if result["status"] == "passed":
            print(f"PASSED: {result['n_violations']} violation(s) "
                  f"| est. profit ${result.get('est_profit', 0):,.2f}")
            for v in result["violations"][:10]:
                print(f"  edge={v['edge']:.3f} size~{v['size']:.2f} "
                      f"profit=${v['profit']:,.2f} {v['type']}/{v.get('kind','')} "
                      f"{v.get('question','')[:60]}")
        else:
            print(f"{result['status'].upper()}: {result.get('error') or 'no violations -> buried'}")


def main():
    env_mod.load_dotenv()
    ap = argparse.ArgumentParser(
        description="LLM research loop + live profit scan for prediction-market consistency arb (Track B)."
    )
    ap.add_argument("--holdout-config", default="research/holdout.yaml")
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--provider", choices=["mock", "auto"], default="auto")
    ap.add_argument("--mode", choices=["research", "scan", "both"], default="both",
                    help="research = LLM loop; scan = live universe profit scan; both = scan then research.")
    # scan options
    ap.add_argument("--max-events", type=int, default=400)
    ap.add_argument("--max-tokens", type=int, default=3000,
                    help="CLOB budget: max tokens to price per scan.")
    ap.add_argument("--min-liquidity", type=float, default=5000.0)
    ap.add_argument("--min-edge", type=float, default=None)
    ap.add_argument("--min-profit", type=float, default=None)
    ap.add_argument("--exhaustive-tol", type=float, default=0.10,
                    help="max |sum(mid YES) - 1| for a set to count as exhaustive.")
    ap.add_argument("--fee", type=float, default=None,
                    help="per-leg fee rate on $1 payoff notional (Polymarket taker fee, usually 0).")
    ap.add_argument("--bankroll", type=float, default=0.0,
                    help="if set, allocate capital across opportunities to maximize profit.")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    with open(args.holdout_config) as fh:
        cfg = yaml.safe_load(fh)
    holdout_cfg = cfg["holdout"]
    rc = cfg["research"]

    if args.min_edge is None:
        args.min_edge = rc.get("min_edge", 0.005)
    if args.min_profit is None:
        args.min_profit = rc.get("min_profit", 0.0)
    if args.fee is None:
        args.fee = rc.get("fee", 0.0)

    os.makedirs(rc["proposals_dir"], exist_ok=True)

    if args.mode in ("scan", "both"):
        print("=== LIVE PROFIT SCAN ===")
        run_scan(args, rc)

    if args.mode in ("research", "both"):
        print("\n=== RESEARCH LOOP ===")
        tool = runner.VisibleMarketTool(holdout_cfg)
        run_research(args, rc, tool, holdout_cfg)


if __name__ == "__main__":
    main()
