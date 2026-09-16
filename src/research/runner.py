import hashlib
import os

import yaml

from src.pm import arb, clob, gamma
from src.research import graveyard, proposals


def load_holdout_config(path="research/holdout.yaml"):
    with open(path) as fh:
        cfg = yaml.safe_load(fh) or {}
    return cfg


class VisibleMarketTool:
    def __init__(self, holdout_cfg):
        self.slugs = set(holdout_cfg.get("event_slugs", []))
        self.fraction = float(holdout_cfg.get("fraction", 0.0))
        self.seed = holdout_cfg.get("seed", 42)

    def _is_holdout(self, event_id):
        if not self.fraction or event_id is None:
            return False
        h = hashlib.md5(f"{self.seed}:{event_id}".encode()).hexdigest()
        return int(h, 16) % 1000 / 1000.0 < self.fraction

    def _filter_events(self, events):
        return [
            ev for ev in events
            if str(ev["slug"]) not in self.slugs and not self._is_holdout(ev["event_id"])
        ]

    def events(self, params=None):
        events = gamma.get_events(params)
        return self._filter_events(events)

    def markets(self, params=None):
        markets = gamma.get_markets(params)
        return [m for m in markets if not self._is_holdout(m["event_id"])]


def fetch_holdout_markets(holdout_cfg):
    slugs = set(holdout_cfg.get("event_slugs", []))
    fraction = float(holdout_cfg.get("fraction", 0.0))
    seed = holdout_cfg.get("seed", 42)

    events = gamma.get_events({})
    holdout_events = []
    for ev in events:
        in_slugs = str(ev["slug"]) in slugs
        h = hashlib.md5(f"{seed}:{ev['event_id']}".encode()).hexdigest()
        in_fraction = fraction > 0 and int(h, 16) % 1000 / 1000.0 < fraction
        if in_slugs or in_fraction:
            holdout_events.append(ev)

    markets = []
    for ev in holdout_events:
        markets.extend(ev["markets"])
    return markets


def attach_holdout_prices(markets, max_markets=60, workers=8, min_liquidity=0.0):
    """Fetch executable top-of-book for the most liquid markets.

    Returns one row per token with best bid/ask, touch sizes and the
    midpoint (kept for backwards compatibility / resolved-market checks).
    """
    liquid = [
        m for m in markets
        if m["accepting_orders"]
        and not m.get("closed")
        and len(m["clob_token_ids"]) >= 2
        and (m["liquidity"] or 0) > min_liquidity
    ]
    liquid.sort(key=lambda m: (m["liquidity"] or 0), reverse=True)
    liquid = liquid[:max_markets]

    from concurrent.futures import ThreadPoolExecutor

    def fetch(m):
        out = []
        for token_id in m["clob_token_ids"][:2]:
            try:
                tob = clob.top_of_book(token_id)
            except Exception:
                continue
            if tob["bid"] is None and tob["ask"] is None:
                continue
            if tob["bid"] is not None and tob["ask"] is not None:
                mid = (tob["bid"] + tob["ask"]) / 2.0
            else:
                mid = tob["bid"] if tob["bid"] is not None else tob["ask"]
            out.append(
                {
                    "token_id": token_id,
                    "price": mid,
                    "bid": tob["bid"],
                    "ask": tob["ask"],
                    "bid_size": tob["bid_size"],
                    "ask_size": tob["ask_size"],
                    # aggregated near-touch depth, used for larger sizing
                    "depth": tob["ask_size"],
                }
            )
        return out

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for chunk in ex.map(fetch, liquid):
            rows.extend(chunk)
    return rows


def attach_token_prices(token_ids, workers=24):
    """Price an explicit set of tokens (top-of-book), skipping any that fail.

    Lets the scanner spend its CLOB budget on the tokens that actually feed a
    discovered relation instead of every token in the universe.
    """
    from concurrent.futures import ThreadPoolExecutor

    unique = list(dict.fromkeys(t for t in token_ids if t))

    def fetch(token_id):
        try:
            tob = clob.top_of_book(token_id)
        except Exception:
            return None
        if tob["bid"] is None and tob["ask"] is None:
            return None
        if tob["bid"] is not None and tob["ask"] is not None:
            mid = (tob["bid"] + tob["ask"]) / 2.0
        else:
            mid = tob["bid"] if tob["bid"] is not None else tob["ask"]
        return {
            "token_id": token_id,
            "price": mid,
            "bid": tob["bid"],
            "ask": tob["ask"],
            "bid_size": tob["bid_size"],
            "ask_size": tob["ask_size"],
            "depth": tob["ask_size"],
        }

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for row in ex.map(fetch, unique):
            if row:
                rows.append(row)
    return rows


def fetch_active_events(limit=60, page_size=100, min_liquidity=0.0, only_neg_risk=False):
    """Fetch active events with their COMPLETE set of sibling markets.

    Market-level pagination drops ``eventId``/siblings, which makes a
    multi-outcome neg-risk set look incomplete and produces phantom arbs.
    Going through /events keeps every sibling together.
    """
    events = []
    offset = 0
    while len(events) < limit:
        page = gamma.get_events(
            {
                "active": "true",
                "closed": "false",
                "limit": min(page_size, limit - len(events)),
                "offset": offset,
                "order": "liquidity",
                "ascending": "false",
            }
        )
        if not page:
            break
        events.extend(page)
        offset += len(page)
        if len(page) < page_size:
            break
    out = []
    for ev in events:
        markets = [
            m for m in ev["markets"]
            if m.get("accepting_orders") and not m.get("closed")
            and len(m.get("clob_token_ids", [])) >= 2
        ]
        liquid = [m for m in markets if (m.get("liquidity") or 0) > min_liquidity]
        if len(liquid) < 2 and not any(m.get("neg_risk") for m in markets):
            continue
        if only_neg_risk and not ev.get("neg_risk"):
            continue
        out.append({**ev, "markets": markets})
    return out


def fetch_active_markets(limit=600, page_size=500, min_liquidity=0.0, include_neg_risk=True):
    """Page the Gamma /markets endpoint for active, tradable markets."""
    markets = []
    offset = 0
    while len(markets) < limit:
        page = gamma.get_markets(
            {
                "active": "true",
                "closed": "false",
                "limit": min(page_size, limit - len(markets)),
                "offset": offset,
                "order": "liquidity",
                "ascending": "false",
            }
        )
        if not page:
            break
        markets.extend(page)
        offset += len(page)
        if len(page) < page_size:
            break
    out = []
    for m in markets:
        if not m.get("accepting_orders") or m.get("closed"):
            continue
        if len(m.get("clob_token_ids", [])) < 2:
            continue
        if (m.get("liquidity") or 0) <= min_liquidity:
            continue
        if not include_neg_risk and m.get("neg_risk"):
            continue
        out.append(m)
    return out


def run_cycle(visible_tool, llm, proposals_dir, graveyard_path, holdout_cfg, sample_events=8, sample_markets=40, min_edge=0.02, min_profit=0.0, fee=0.0):
    events = visible_tool.events({})
    if not events:
        return {"status": "no_visible_data", "error": "no visible events"}

    sample_markets_list = []
    for ev in events[:sample_events]:
        sample_markets_list.extend(ev["markets"][:6])
    sample_markets_list = sample_markets_list[:sample_markets]

    grave = graveyard.summaries(graveyard_path)
    visible_blurb = "\n".join(
        f"- [{m['question']}] outcomes={m['outcomes']} prices={m['outcome_prices']} "
        f"condition_id={m['condition_id']} tokens={m['clob_token_ids']} event={m['event_id']}"
        for m in sample_markets_list
    )

    system = (
        "You are a research analyst for a prediction-market consistency-arbitrage system. "
        "You propose LOGICAL mechanisms between contracts that must hold by no-arbitrage. "
        "Your job is NOT to predict outcomes; it is to find structurally guaranteed "
        "mispricings. The relations you propose will be applied to markets you have NEVER "
        "seen (a sealed holdout), so propose GENERIC mechanisms that must hold for ALL "
        "markets of a type, not instance-specific rules tied to one visible market. "
        "Use 'scope': 'all' to mean the relation applies to every matching market. "
        "Output JSON only, no prose. "
        "Relation types: "
        "'yes_no' {scope, note} -- for any binary market, yes+no must sum to ~1; "
        "buy both when sum < 1 (use scope 'all'); "
        "'implies' {antecedent_token, consequent_token, note} -- if antecedent resolves Yes then "
        "consequent must also resolve Yes, so p_antecedent <= p_consequent; "
        "'event_exhaustive' {scope, note} -- the outcome markets of one event are mutually "
        "exclusive and exhaustive, so the sum of their YES prices must be ~1 (use scope 'all'). "
        "The proposal JSON must have exactly these keys: mechanism, counterparty, "
        "why_not_arbitraged, falsification, target_market, relations."
    )
    user = (
        "VISIBLE MARKETS (for calibration only):\n" + visible_blurb +
        "\n\nPREVIOUSLY BURIED PROPOSALS (do not re-propose cousins of these):\n" +
        ("\n".join(grave) if grave else "(none)") +
        "\n\nPropose a pre-registered proposal with at least one GENERIC relation (scope 'all'). "
        "Only include relations you are confident are logically necessary for all markets of "
        "that type."
    )

    try:
        proposal = llm.propose(system, user)
    except Exception as e:
        return {"status": "llm_error", "error": str(e)}

    import src.research.proposals as proposals_mod
    try:
        proposals_mod.validate(proposal)
    except ValueError as e:
        fake = {"proposal_id": "invalid", "mechanism": str(e), "relations": [], "falsification": "", "counterparty": "", "why_not_arbitraged": "", "target_market": ""}
        graveyard.bury(graveyard_path, fake, f"invalid schema: {e}")
        return {"status": "invalid_proposal", "error": str(e)}

    proposal = proposals_mod.new_proposal(**proposal)
    path = proposals_mod.save_proposal(proposal, proposals_dir)
    assert os.path.exists(path), "proposal must be logged before holdout access"

    holdout_markets = fetch_holdout_markets(holdout_cfg)
    prices = attach_holdout_prices(holdout_markets, max_markets=holdout_cfg.get("max_markets", 60))
    violations = arb.check_relations(
        holdout_markets,
        prices,
        proposal["relations"],
        min_edge=min_edge,
        min_profit=min_profit,
        fee=fee,
    )

    if violations:
        proposals_mod.update_status(proposals_dir, proposal["proposal_id"], "passed", violations)
        return {
            "status": "passed",
            "proposal_id": proposal["proposal_id"],
            "violations": violations,
            "n_violations": len(violations),
            "est_profit": arb.total_profit(violations),
        }
    proposals_mod.update_status(proposals_dir, proposal["proposal_id"], "failed", {"violations": []})
    graveyard.bury(graveyard_path, proposal, "no price violation on sealed holdout")
    return {"status": "failed", "proposal_id": proposal["proposal_id"], "violations": []}