# Income engine

`income_bot.py` + `src/income/`. It replaces `ladder_bot.py` as the thing that
trades. It was written after a hostile strategy review; each section below
names the flaw it fixes.

## What was wrong, and what replaced it

| ladder_bot | income_bot |
|---|---|
| "Risk-free" = `ask(L1) > bid(L2) − ~0.004` when resting both legs. That fires on consistent books; it only pays if **both** limit orders fill, and the fill you get is the informed one. | **taker_arb**: `bid(L1) − ask(L2)` after **both taker fees**, depth-walked, IOC. **rest_hedge**: rest ONE leg; hedge the other by IOC the moment it fills. Credit is net of the hedge's taker fee. |
| A half-filled pair sat naked up to 12 h, then was unwound at an assumed 0.02 cost. | A manage pass every 5 min levels every pair. The hedge waits at most `--max-naked-min` (20) for a better price, then crosses. Resting legs are **pulled** when the hedge book moves against them, at kickoff, or after 6 h. |
| Orders priced from quotes up to 25 min old, collected across a whole sweep before any order went out. | Each game's books are read and acted on immediately. Every hedge re-reads its book. |
| Bets priced off a normal fitted to the ladder's own mids. The key-number PMF was not renormalised. | Fair value comes from **ESPN's DraftKings line** (spread → mu, de-vigged moneyline → sigma, shrunk to a league prior near a pick'em). The PMF is renormalised. If the model disagrees with the whole ladder (a wrong match or a flipped sign), value/mm are refused for that game. |
| Kelly computed and never used. Bets and arbs ranked on one EV-per-share axis. | Value takes use fractional Kelly on the binary actually bought (the NO side for sells), with a $ cap per order. Arbs are capacity- and capital-bound. |
| Fills inferred from aggregate positions. `positions()` failing ⇒ `{}` ⇒ vanished pairs booked as FILLED. | A fill is the change in **that order's** `cumQuantity`, read by id, priced from cumulative notional. If an order can't be read, nothing is booked. A send that times out is treated as *possibly live*: the intent is kept and the game is **frozen** until reconciled or cleared by hand (`--clear-suspect`). |
| Non-atomic JSON state. A corrupt file silently became a blank state, forgetting live orders. | Atomic writes (tmp + fsync + rename). A corrupt file is quarantined and the bot refuses to run. Write-ahead intents: a crash between send and id is recovered from open orders, or the game is frozen. |
| No risk limits. `greeks.py` unused. | **Exact** worst-case P&L per game, over every integer margin (ladders settle on one number, so no approximation is needed). Per-game and book-wide caps shrink orders to fit rather than skipping them. Daily-loss and drawdown halts. Net delta feeds MM skew. |
| No kill rules, no measurement, 0 tests. | Kill rules registered before any live trading (below). CLV and markout measurement, `--review`, 57 tests (`make test`), including a regression for every finding of an adversarial review of this engine. |
| Traded any ladder, any date. | New risk only on ladders dated today or later **and** confirmed pre-game by ESPN. Without that match there are no kickoff pulls and no automatic settlement, so no new risk is taken. Sub-period ladders are therefore not traded yet. |
| Dry runs wrote to the live state file. | Dry runs use `income_state.dry.json` / `income_ledger.dry.jsonl`. |

## Capital discipline for resting orders

A resting order locks collateral whether or not it fills (a sell ties up
1 − price). Two rules keep a slate of stale books from starving live trades:

- resting orders may use at most `--rest-budget` (40%) of `--capital`;
- a resting leg priced more than `--rest-max-gap` (8c) from fair, against the
  counterparty, is skipped. Nobody rational fills it: the first dry run wanted
  to sell Howard +7.5, worth ~0.3c, at 18.9c.

Only football (`cfb`, `nfl`) gets a fair-value model. Other sports on the
venue (MLB run lines, ...) get structural arbs and ESPN settlement only.

## Strategies

| name | pays when | risk | default |
|---|---|---|---|
| `taker_arb` | a monotonicity violation exceeds both taker fees | none once both legs fill; a short leg is levelled by the repair loop | on |
| `rest_hedge` | a resting price beats the other strike's touch by more than one taker fee | naked between fill and hedge (minutes), bounded by risk caps | on |
| `value` | one strike deviates from sportsbook fair by more than fee + 3c | directional on that game, sized by ¼ Kelly and capped by worst case | on |
| `mm` | two-sided quotes around fair earn the rebate, and may qualify for reward pools | inventory; skewed by net delta, capped per strike | **off** (experimental) |

## In-play: the day-trading half (`src/income/inplay.py`)

While a game is live the bot polls every 5 s: ESPN's score, clock and period,
plus the books of the ~14 strikes nearest the live fair value. Nothing is held
to settlement except crossed arbitrage pairs, whose result is locked at entry.

| name | status | what it does |
|---|---|---|
| `inplay_arb` | **LIVE** | Cross-strike violations as the ladder reprices mid-game. Both legs IOC; a short leg is hedged at once, with no slippage wait in-play. Needs no faster feed and no prediction. |
| `divergence` | **PAPER** | Final margin ~ N(score diff + mu_pre·tau, sigma_pre²·tau) (Stern). Enters when the market is off by more than the full round trip (two taker fees + half the spread + slippage, ~4c at the money) plus 1c. Exits on convergence, an 8c stop, a 15-min time stop, or before the final 2 minutes. Also watches NFL/CFB moneylines. |

Divergence stays on paper because this repo's evidence is against it:
in-play momentum and reversion showed no edge in 128,517 observations (§8),
and ESPN plays arrive 34–67 s late while the venue reprices in 50–100 ms (§4).
By default, a gap between our model and the market means *we* are stale.
The filters target exactly that:
- the gap must persist for **90 s**;
- the mid must stay within 2c while it does;
- signals are ignored for **150 s** after any score change we observe;
- a game is skipped when the model disagrees with the whole ladder, which is
  what a missed score looks like.

**GO rule (pre-registered).** Divergence may be considered for real money only
after all of these hold:
- ≥ 50 paper round trips over ≥ 6 games;
- mean net P&L > 0 with t > 2;
- positive in **both** halves of the sample.

`make income-review` prints the status. Promoting it is a code change (it has
no live path) and a human decision, not a flag.

Raw observations (every 30 s per live game: book, fair, score, tau) go to
`research/inplay_obs.jsonl`. That is the full-game capture §4 always needed,
for studying gap persistence offline.

## Favourites, on paper (`src/income/favs.py`)

The question: do contracts priced 0.85-0.96 before the game pay after fees,
held to settlement or cashed out at 0.97 / 0.98 / 0.99? Every cycle that
refreshes the market listing (once per 30 min) screens it, marks every open
position, and grades the ones whose market has closed using the venue's own
`settlement`. That covers props, totals and fights too. No extra book reads:
the listing's bestBid/bestAsk matched the book on every spot check. On the
first listing that was ~3,300 positions across ~200 games.

`make favs` prints win rate vs price and P&L per exit, by price, time to game
and market type, with a CI bootstrapped over games. The GO bar was set before
any data came in: n >= 300 settled positions, >= 60 games, and the best
exit's whole 95% CI above 0. It has no live path. `--no-favs` turns it off.

## Pre-registered kill rules (`src/income/risk.py`)

Written before any live result. A tripped rule cancels that strategy's orders.
It stays off until `--reset-kill <name>`.

- **value**: after 40 fills on closed games, mean CLV after fees < 0 with t < −1.
- **taker_arb / inplay_arb**: after 15 pairs, mean realised net credit < 0 with t < −1. A crossed pair should never lose; if it does, the fills aren't the prices the scan saw.
- **rest_hedge**: after 20 completed pairs, mean realised net credit < 0 with t < −1.
- **mm**: after 50 fills, mean (next-cycle markout + rebate) < 0 with t < −1.
- **Book halt**: realised loss today ≥ `--daily-loss`, or realised drawdown ≥ 25% of starting capital. No new risk; management continues.

CLV is the headline metric for `value`. Settled P&L on a few dozen binaries is
noise. Beating the closing line is the standard proof of edge in sports
betting, and it produces a verdict within about two CFB weekends.

CLV is measured against the **ladder's own last pre-game mid** for that
strike, not against our ESPN model. Judging the model by itself cannot catch
a stale line; CLV would just echo the entry edge back.

## Operator commands

```
income_bot.py --review                     # live books, metrics, kill status
income_bot.py --clear-suspect GAME         # after checking venue positions by hand
income_bot.py --settle GAME:MARGIN         # a game ESPN could not resolve
income_bot.py --reset-kill value           # re-enable a killed strategy (deliberately)
```

Games freeze (no scan, no repair) whenever the book might not match the
venue: a timed-out send, an unresolved intent, or 8+ repairs on one pair.
Postponed games (ESPN "post" with no completed result) are pulled and never
auto-settled.

## Rollout

1. `make test` and `make income-dry` on the box. Read what it would do.
2. Stop `cron_cycle.sh` in crontab. **Do not run both bots on one account**:
   they never touch each other's orders, but they spend the same cash.
3. Crontab, one line. The script picks full-scan vs in-play by time of day:
   ```
   */5 * * * *  CAP=5 /home/ihearthim/polymarket-trader/income_cycle.sh
   ```
4. `make income-review` daily. Scale `CAP` only once `value` CLV has n ≥ 40
   and is positive, and the rest_hedge fill rate is measured, not guessed.

## Still unproven (read before scaling)

- **IOC semantics** on this venue have never been observed. The first live
  full cycle should be watched: confirm IOC orders end FILLED or CANCELED,
  never resting.
- **ESPN line quality.** Its DraftKings line may be stale relative to sharp
  books. A stale anchor produces "edge" that is really lag, and CLV will say
  so. That is why CLV decides.
- **Key-number factors** come from RESEARCH.md §15 and are not re-validated.
- **Reward-pool qualification** is still unknown. Enabling `mm` is the
  experiment that would answer it (it quotes two-sided, which the
  `--buy-only` farming never did).
- Sub-period ladders have no ESPN match, so they are not traded at all yet.
  They need their own period state and settlement source.
