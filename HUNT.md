# The hunt — results log

Every strategy tested in the 2026-10 hunt, in the order it was graded. The rules for
each were written in `TEST_PLAN.md` before its data was looked at.

## Data traps found and fixed (read before reusing the global dump)

- **Resolutions.** `outcome_prices` writes many resolved markets as 0.995/0.9995,
  not 1. Unresolved junk shows as 0.9/0.1 or 0.5. A side at ≥ 0.99 (other ≤ 0.01)
  is the winner; anything else is dropped.
- **Direction.** About 75% of fills are recorded as "taker SELL", and from May
  2026 all of them are. The exchange mints complementary orders, so
  "taker SELL token2 at q" *is* an aggressive buy of token1 at 1 − q. The P&L is
  identical. Counting only "taker BUY" rows silently drops three quarters of the
  real asks and every fill from May 2026.
- **Dates.** The international venue names games by UTC date
  (`nfl-atl-no-2026-10-06`); Polymarket US uses the ET date
  (`aec-nfl-atl-no-2026-10-05`).
- **Pinnacle coverage.** Football-Data carries no Pinnacle odds after January 2026,
  because Pinnacle closed its public feed. Any Pinnacle-anchored test has no data
  after that.

## S5 — Pinnacle anchor on soccer: FAILED (2026-10-01)

**Sample.** 4,394 Football-Data matches paired with Polymarket three-way events
across 17 leagues (an audit of 14 random pairs found no errors). That gave
2.7M aggressive buys in the 12 h before kickoff.

- **Polymarket is not worse than Pinnacle.** Brier of the last Polymarket price
  in the final 30 min was **0.1923** against Pinnacle close **0.1953**, on 3,384
  outcomes. Mean |gap| was 2.0c.
- **Buying where Polymarket sat ≥ 2c below Pinnacle (after fee) lost money.**

| | entries | matches | return per $ | 95% CI |
|---|---:|---:|---:|---:|
| Primary: close, last 30 min | 182 | 114 | −4.9% | [−26%, +17%] |
| Secondary: pre-close, last 12 h | 983 | 470 | −3.2% | [−10.7%, +4.1%] |

- **No holdout verdict was possible**, because Football-Data has no Pinnacle odds
  from February 2026. The pre-registered holdout is empty. What we have points the
  wrong way, so this is not pursued with a substitute anchor.
