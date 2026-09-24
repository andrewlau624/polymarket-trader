"""Income engine: sharp-anchored ladder trading with honest accounting.

Replaces ladder_bot's "rest both legs and call it arbitrage" with:

  model.py      margin distribution from an EXTERNAL sportsbook line
  signals.py    true taker arbs, rest-then-hedge arbs, value takes, MM quotes
  sizing.py     fractional Kelly for bets, capacity for arbs
  risk.py       exact scenario P&L per game, limits, pre-registered kill rules
  state.py      atomic state, write-ahead intents, per-order fill ledger
  execution.py  price-at-placement, hedge-on-fill, stale-quote pulls
  fairvalue.py  ESPN pickcenter line -> (mu, sigma), side resolution, finals
  measure.py    CLV and markouts: the fast proof (or disproof) of edge

Everything here except fairvalue.py and the client is pure and tested.
"""
