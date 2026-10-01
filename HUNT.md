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

## Category × price × time scan (global dump, 1.03B fills): no tradeable survivor (2026-10-01)

**Method.**
- 2,438 cells, each one (category, price band, time-to-close bucket), with ≥ 60
  events in train (markets closing before 2026-03-01).
- 38 cells passed the shortlist rule. They were frozen and committed (`hunt/frozen.json`)
  and graded once on the holdout (2026-03-01 → 2026-07-20).
- A cell had to pass under both the first-fill entry and a 60 s slow-fill entry.

**Result.** 7 passed both rules, and none survives inspection.

- **Tennis favourites, ATP 95–98c, crypto launch: timing leak.** Their bucket was
  "hours before the market's last trade". For in-play sport that leaks the outcome:
  comebacks make matches longer, which pushes those entries out of the final-hour
  bucket. The favourite looks underpriced only because of the look-ahead.
- **Minor-league soccer underdogs 10–20c, 1–4 weeks out: subgroup luck.**
  - Across all soccer that cell is fairly priced: −6.1% train, +0.4% holdout,
    ~1,000 events.
  - The pass came from a few small leagues lumped together, with ~$5 per fill.
- **Crypto up/down 20–35c, 1–3 days out (+24% holdout, 287 events): not available.**
  Polymarket US does not list these markets.

**What every honest cut agrees on.**
- **Buying at the ask loses.** Equal weight per market, almost every
  sport/politics/culture/finance cell is −2% to −9% after the US taker fee, in both
  periods.
- **Late soccer longshots are the worst:** −28% to −44% for 10–35c in the final hour.

**Two traps found on the way.**
- **Weighting.** Dollar-weighted, buying favourites at 80–98c looks like +2–5%. One
  equal entry per market, the same cells are −2% to −9%. A few giant markets (big
  elections, Fed decisions) carry the dollar-weighted number.
- **Scheduled end dates.** Esports `end_date` is often hours *after* the match ends.
  "Hours to scheduled end" therefore includes post-decision junk fills (5–40 shares at
  1c), which explode equal-weighted returns.

**Resting orders (maker side).** Across all fills, resting orders earned +0.53%
(train) and +0.50% (holdout) per $ including the US maker rebate, dollar-weighted. That
is the spread. No category-level maker edge survived both the scheduled-time and the
equal-weight checks cleanly.

## Following skilled wallets: FAILED (2026-10-01)

**Selection.** 9,975 taker wallets with ≥ 30 events and t ≥ 3 on markets resolving
2025-01-01 → 2026-02-28, frozen in `hunt/skilled.json` before the test.

**Holdout (markets resolving from 2026-03-01).** Each wallet's first aggressive buy of
a token was copied at the first aggressive-buy price by anyone ≥ DELAY seconds later,
held to resolution, with the US taker fee.

| | entries | events | return per $ | 95% CI |
|---|---:|---:|---:|---:|
| follow, 60 s | 247,994 | 52,010 | −0.97% | [−1.10%, −0.85%] |
| follow, 10 min | 225,101 | 49,441 | −1.18% | [−1.33%, −1.05%] |
| placebo (random buy, same markets) | 318,182 | 58,293 | +1.48% | [+1.36%, +1.61%] |

Copying loses, and loses to random. This matches polymarket-sharps (no lag beat a
placebo).

## T1 tennis re-test (scheduled-start timing): FAILED (2026-10-01)

Entries ran from the scheduled start S = end_date − 7 d to S + 6 h, so they cannot
depend on how long the match runs.

- **Band 0.65–0.80, holdout:** first fill −2.05% [−2.93, −1.18], 10,317 markets;
  slow fill −0.82% [−1.82, +0.20].
- **Band 0.80–0.90, holdout:** first fill −1.24% [−2.05, −0.43]; slow fill +0.17%
  [−0.74, +1.12].
- Win rates equal prices. The earlier tennis "edge" was the duration leak.

## T2 table tennis, in progress

- **Ratings from results alone are nearly uninformative.** Elo built from 24,575
  settled matches on Polymarket US had Brier 0.2462 after 2026-09-01, against 0.2500
  for a coin flip. Its 60–70% favourites won 61.1%.
- **The comparison with prices is pending.** The niche recorder captures pre-match
  books; the grade waits for those matches to settle and for the ratings to be current.

## M2 — fill the empty side of reward markets: the one live lead (2026-10-01)

**Mechanism, quoted from docs.polymarket.us/incentives/liquidity:**
- A side earns only when it holds Target Size.
- "Programs without a Max Spread score each side on its own, even if the other side
  is empty."
- "If you're the only one providing liquidity on a second that qualifies, you earn
  that second's full share."
- Payouts under $1 are not paid. That explains the old SKIPPED rewards: $0.08 across
  pools shared by thousands of markets.

**What the scan found on 2026-10-01:**
- 44,718 reward markets, 343 with a daily pool, no Max Spread and a half-pool ≥ $1.10.
- About 90 of those had an **empty bid side**.

**The plan (`hunt/reward_bot.py`, dry run):**
- 8 post-only bids at 0.1c for Target Size, $95 collateral in total.
- Modelled at **$25.50/day** if nobody joins. Each order would be its side's only order.
- Fill risk: we own a longshot at 0.1c, so the loss is capped at the collateral.
- It needs a real test, because two things cannot be read off the API: whether 0.1c
  bids are treated as abuse, and whether rewards arrive as cash or as promo credit.

## T4 — tennis Elo vs pre-match ask: FAILED (2026-10-01)

**Sample.** 14,757 single-match markets (ATP, WTA, ITF). Prices were the last
aggressive buy in the 6 h before the scheduled start. K = 48 was chosen on train.

- **Price beats rating.** Brier on the holdout was price mid **0.2099** against Elo
  **0.2371** (2,832 matches). The dump's tennis history is too short to rate players
  well.
- **Entries at a ≥ 5c Elo edge:**
  - ATP holdout −7.6% [−14.8%, −0.4%], n = 1,201;
  - WTA holdout +0.0% [−9.6%, +9.4%], n = 718;
  - ITF holdout n = 41, too few to grade (+24.6% [−13.6%, +60.2%]).

## Live tests in progress (2026-10-01)

- **T3, table tennis in play.** `hunt/tt_live.py` records the venue's own score
  (`/v1/events/{id}`: game-by-game points) beside the book, 0.04 s apart, ~2.5 s per
  match, around the clock.
- **T2, table tennis pre-match.** Elo vs recorded pre-start books; it waits for
  settlements.
- **S4, international book vs US.** On lower-tier CS2 the international book was the
  *thinner* one (34/68c vs US 71/72c). The real test is major-league games tonight.
- **Hourly check-in** grades all three against their pre-registered bars.
