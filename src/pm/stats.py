"""Paper-trading stats: state snapshot + a self-contained HTML page.

The bot rewrites research/stats.html every loop with the numbers embedded and
a meta-refresh, so any static server (or your website's webroot) shows a live
page without any JS/backend.
"""

import json
import os
import time
from datetime import datetime, timezone


def build_state(broker, markets, mode, bankroll, start_time,
                est_rewards_per_day, reward_accrued, last_mid, selected,
                last_book=None, avg_cost=None, realized=0.0, adverse=None,
                buys=0, sells=0):
    last_book = last_book or {}
    avg_cost = avg_cost or {}
    adverse = adverse or []
    cash = broker.cash
    positions = []
    inv_value = 0.0
    inv_liq = 0.0        # what you'd actually get selling at the bid
    basis = 0.0
    for m in markets:
        for tok in m["tokens"][:2]:
            tid = tok["token_id"]
            size = broker.positions.get(tid, 0.0)
            if abs(size) < 1e-9:
                continue
            mid = last_mid.get(tid)
            bid, ask = last_book.get(tid, (None, None))
            cost = avg_cost.get(tid, 0.0)
            val = size * (mid or 0.0)
            # conservative: longs valued at the bid, shorts at the ask
            mark = (bid if size > 0 else ask) if (bid if size > 0 else ask) else mid
            inv_value += val
            inv_liq += size * (mark or 0.0)
            basis += size * cost
            positions.append(
                {
                    "question": m["question"][:60],
                    "outcome": tok.get("outcome"),
                    "size": size,
                    "avg_cost": cost,
                    "mid": mid,
                    "value": val,
                    "pnl": val - size * cost,
                }
            )
    equity = bankroll + cash + inv_value
    equity_liq = bankroll + cash + inv_liq
    unrealized = inv_value - basis
    n_fills = len(getattr(broker, "fills", []) or [])
    n = buys + sells
    return {
        "mode": mode,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "uptime_min": (time.time() - start_time) / 60.0,
        "bankroll": bankroll,
        "cash": cash,
        "inventory_value": inv_value,
        "equity": equity,
        "equity_conservative": equity_liq,
        "pnl": equity - bankroll,
        "pnl_pct": ((equity - bankroll) / bankroll * 100.0) if bankroll else 0.0,
        "realized": realized,
        "unrealized": unrealized,
        "pnl_conservative": equity_liq - bankroll,
        "fills": n_fills,
        "buys": buys,
        "sells": sells,
        "pct_buys": (buys / n) if n else 0.0,
        "adverse_mean": (sum(adverse) / len(adverse)) if adverse else 0.0,
        "adverse_n": len(adverse),
        "open_orders": broker.snapshot().get("n_open", 0),
        "est_rewards_per_day": est_rewards_per_day,
        "est_rewards_accrued": reward_accrued,
        "positions": positions,
        "markets": [
            {"question": m["question"][:60], "daily_rate": m["daily_rate"]}
            for m in selected
        ],
    }


def _row_class(pnl):
    return "pos" if pnl > 0 else ("neg" if pnl < 0 else "")


def render_html(state):
    s = state
    pos_rows = "".join(
        f"<tr><td>{p['question']}</td><td>{p.get('outcome') or ''}</td>"
        f"<td class='num'>{p['size']:.0f}</td>"
        f"<td class='num'>{p['avg_cost']:.3f}</td>"
        f"<td class='num'>{(p['mid'] if p['mid'] is not None else 0):.3f}</td>"
        f"<td class='num'>{p['value']:+.2f}</td>"
        f"<td class='num {_row_class(p['pnl'])}'>{p['pnl']:+.2f}</td></tr>"
        for p in s["positions"]
    ) or "<tr><td colspan=7 class='muted'>no inventory</td></tr>"
    mkt_rows = "".join(
        f"<tr><td>{m['question']}</td><td class='num'>{m['daily_rate']:.0f}</td></tr>"
        for m in s["markets"]
    ) or "<tr><td colspan=2 class='muted'>none selected</td></tr>"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>MM bot stats</title>
<meta http-equiv="refresh" content="15">
<style>
 body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;background:#0f1115;color:#e6e6e6;margin:0;padding:24px}}
 h1{{font-size:18px;margin:0 0 4px}} .muted{{color:#8a8f98}}
 .grid{{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}}
 .card{{background:#171a21;border:1px solid #232733;border-radius:10px;padding:14px 18px;min-width:150px}}
 .card .k{{font-size:12px;color:#8a8f98;text-transform:uppercase;letter-spacing:.04em}}
 .card .v{{font-size:26px;font-weight:600;margin-top:6px}}
 .pos{{color:#3ddc84}} .neg{{color:#ff5c5c}}
 table{{border-collapse:collapse;width:100%;margin-top:8px}}
 th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #232733}}
 th{{color:#8a8f98;font-weight:500;font-size:12px;text-transform:uppercase}}
 td.num{{text-align:right;font-variant-numeric:tabular-nums}}
 h2{{font-size:14px;color:#8a8f98;text-transform:uppercase;letter-spacing:.04em;margin:24px 0 0}}
</style></head><body>
<h1>Polymarket MM bot <span class="muted">({s['mode']})</span></h1>
<div class="muted">updated {s['updated']} &middot; uptime {s['uptime_min']:.0f} min</div>
<div class="grid">
 <div class="card"><div class="k">Starting</div><div class="v">${s['bankroll']:.2f}</div></div>
 <div class="card"><div class="k">Mark-to-mid</div><div class="v {_row_class(s['pnl'])}">${s['equity']:.2f}</div></div>
 <div class="card"><div class="k">Liquidatable (truth)</div><div class="v {_row_class(s['pnl_conservative'])}">${s['equity_conservative']:.2f}</div></div>
 <div class="card"><div class="k">Rewards accrued*</div><div class="v pos">${s['est_rewards_accrued']:.2f}</div></div>
 <div class="card"><div class="k">Realized</div><div class="v {_row_class(s['realized'])}">${s['realized']:+.2f}</div></div>
 <div class="card"><div class="k">Unrealized</div><div class="v {_row_class(s['unrealized'])}">${s['unrealized']:+.2f}</div></div>
 <div class="card"><div class="k">Adverse/fill</div><div class="v {_row_class(-s['adverse_mean'])}">{s['adverse_mean']:+.4f}</div></div>
 <div class="card"><div class="k">Fills (B/S)</div><div class="v">{s['buys']}/{s['sells']}</div></div>
 <div class="card"><div class="k">Cash</div><div class="v">${s['cash']:.2f}</div></div>
 <div class="card"><div class="k">Inventory</div><div class="v">${s['inventory_value']:.2f}</div></div>
 <div class="card"><div class="k">Open orders</div><div class="v">{s['open_orders']}</div></div>
 <div class="card"><div class="k">Rewards est/day</div><div class="v">${s['est_rewards_per_day']:.2f}</div></div>
</div>
<h2>Inventory</h2>
<table><tr><th>Market</th><th>Outcome</th><th class="num">Size</th><th class="num">Avg cost</th><th class="num">Mid</th><th class="num">Value</th><th class="num">P&amp;L</th></tr>{pos_rows}</table>
<h2>Quoted markets</h2>
<table><tr><th>Market</th><th class="num">$/day</th></tr>{mkt_rows}</table>
<p class="muted">* reward accrual is a proxy estimate (score model); not a settled payout.
Paper P&amp;L is mark-to-market and does not settle until positions resolve.</p>
</body></html>"""


def write(research_dir, state):
    os.makedirs(research_dir, exist_ok=True)
    with open(os.path.join(research_dir, "mm_state.json"), "w") as fh:
        json.dump(state, fh, indent=2)
    with open(os.path.join(research_dir, "stats.html"), "w") as fh:
        fh.write(render_html(state))
