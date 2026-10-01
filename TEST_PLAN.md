# Test plan — Kalshi vs Polymarket US (pre-registered 2026-09-30)

Written before any cross-venue price gap was computed. **Nothing in this file
may be changed after the recorder starts**, except to add a dated note saying
what changed and why. Results go in `RESEARCH_PIVOT.md`, never here.

## The edge, in one sentence each

- **S1 locked pair.** Two regulated venues sell the same game result, and when
  one venue's price for team A plus the other venue's price for team B (plus
  both fees) is under $1, the pair pays $1 whoever wins.
- **S2 cheaper venue.** When one venue's ask (plus fee) sits clearly below the
  other venue's mid, the cheap venue is noisy and the other is right, so buying
  the cheap side and holding to settlement makes money.
  - S2 fails if the *cheap* venue is the one that is right, which means it simply
    moved first.

## Why it might survive competition

The two venues have separate accounts, separate KYC and separate money. Moving
cash between them takes 1–3 days. Capital that has to sit on both venues is
expensive for market makers, so small gaps may be left alone.

## Why it probably will not

- Taking on both venues costs ~3.5c per $1 pair at 50c.
- Susquehanna and Wintermute quote both venues.
- A Sept 2026 Kalshi vs Polymarket (global) recorder found nothing after fees
  (RESEARCH_PIVOT.md §1).

## Universe

Game-winner markets listed on both venues:

| Kalshi series | Polymarket US slug |
|---|---|
| KXNFLGAME | aec-nfl- |
| KXNCAAFGAME | aec-cfb- |
| KXMLBGAME | aec-mlb- |
| KXNHLGAME | aec-nhl- |
| KXNBAGAME | aec-nba- |
| KXWNBAGAME | aec-wnba- |
| KXUFCFIGHT | aec-ufc- |

**Matching rules.**
- Same league and same ET game date: the Kalshi ticker date and the Polymarket
  slug date are both ET.
- Each Polymarket side maps one-to-one to a Kalshi market, by team code or team
  name. A game whose sides do not map one-to-one is dropped, never guessed.
- Polymarket's `long` side is read from `marketSides[].long` on every game. It is
  the first-listed team, not reliably home or away.

**Settlement differences, read from both venues' rules text on 2026-09-30.**

| | Polymarket US | Kalshi |
|---|---|---|
| NFL tie | $0.50 | $0.50 per team |
| Overtime / extra innings | included | included |
| Postponed, played within 48 h | same result | same result |
| Postponed 48 h–2 weeks | waits for the result | "fair price" |
| Postponed > 2 weeks | last fair market price | fair price |
| UFC draw / no contest | (check per market) | 50/50 |

A pair is therefore identical except for postponements between 48 h and 2 weeks.
That risk is recorded, not priced. Any postponed game is reported separately and
excluded from S1's riskless count.

## What the recorder records (no orders)

**`xvenue_recorder.py`** runs from cron every 5 minutes for 4.6 minutes. Each
sweep covers every matched game that starts within 7 days or is in play. Each
sweep writes one line per game:

- **Polymarket:** best bid and size, best ask and size, book `state`.
- **Kalshi:** for each team's market, best YES bid and size, and best YES ask
  and size. The ask is derived as 1 − best NO bid; the size is the size of that
  NO bid.
- `t`, game start time, and whether the game has started.

Output: `research/xvenue/rec-<UTC date>.jsonl`, daily files.

## Fill model and fees

- **Fills.** Taker at the displayed top-of-book price only. Size is capped at the
  displayed size at that level, and no deeper levels are used.
- **Polymarket state.** The book must be `MARKET_STATE_OPEN`.
- **Kalshi status.** The market must be `active`.
- **Polymarket fee per share:** `0.0695·p·(1−p)` (src/pm_us/fees.py). Buying the
  short side costs `1 − bid(long)`.
- **Kalshi fee per order:** `ceil_to_cent(0.07·C·p·(1−p))`. Rounding up per order
  is the conservative reading of an unverified detail; it costs ~2c on a
  1-contract order. Both fees are evaluated at the order size C used.
- **Kalshi routes to team B.** Team B's win can be bought on Kalshi either as YES
  on team B's market or as NO on team A's market. The cheaper of the two is used.

## S1 — locked pair

- **Opportunity in a sweep.** For a game and a team X:
  `cost = ask_V1(X) + fee_V1 + ask_V2(not X) + fee_V2`, where V1 and V2 are the
  two venues, evaluated both ways round, pre-game only.
  - `net = 1 − cost` per pair.
  - C = min(top depths, 100 pairs).
- **Episode.** Consecutive sweeps in which the same game, same direction has
  net ≥ +0.005 and C ≥ 10.
  - An episode counts only if it **persists across ≥ 2 consecutive sweeps**
    (~1–3 min apart). A one-sweep gap could have been two snapshots taken a second
    apart and is not fillable by this code.
- **Capturable profit.** For each counted episode: its first sweep's `net × C`,
  counted once.
- **PASS** requires all of these over 14 calendar days:
  - ≥ 20 distinct games with a counted episode;
  - median net per counted episode ≥ +0.5c per $1 pair;
  - capturable profit ≥ $5.00/day averaged over the 14 days. That is the floor for
    being worth a second account; it is not "real money".
- **KILL early** if after 7 days fewer than 5 counted episodes exist.

## S2 — buy the cheaper venue, hold to settlement

- **Fair value.** The other venue's mid, used only when its spread ≤ 0.03.
- **Entry.** `ask_V(X) + fee_V(ask) ≤ fair(X) − 0.010`, pre-game only.
  - Only the first trigger per (game, team, venue) counts. One game can give at
    most 4 entries, which are clustered.
- **Trade.** Cost = ask, and P&L per share = `payout − ask − fee`.
  - Payout is the venue's own settlement of the bought side: the Polymarket US
    `/settlement` endpoint, or the Kalshi market `result`.
  - Postponed or void games are excluded and reported separately.
- **Bootstrap unit:** the game. All entries on one game are resampled together.
- **PASS** requires all of these:
  - ≥ 100 entries over ≥ 20 distinct games;
  - the 95% CI of expectancy per $ staked, bootstrapped over games, lies entirely
    above 0;
  - expectancy is positive in both chronological halves, split by game start time;
  - `src/sim.py` walk-forward quarter-Kelly replay (5%/bet, 10%/game) Monte Carlo
    gives P(50% drawdown) < 10%.
- **KILL early** if after 50 entries over ≥ 10 games the point estimate is below
  −2c/share.

## Reported but NOT a pass criterion (diagnostic)

- **Lead–lag.** For each sweep with |mid_PM − mid_K| ≥ 0.02 pre-game, look at
  which venue's mid moved more toward the other by the next sweep. This says which
  venue is "right"; it is not a trade.
- **Shape of S2.** Win rate, average win, average loss, payoff against breakeven,
  and profit factor, all via `src/sim.py`.
- **In-play.** The same S1/S2 numbers computed in play, labelled "displayed
  in-play prices are not fillable". They can never pass on their own.

## If something passes

Nothing trades until:
1. a Kalshi account is opened and verified (the owner does not have one yet);
2. one week of paper trading on the same code path;
3. one real 1-contract order on each venue at the modelled price.

Then live trading starts at $100–200 per venue with these limits:
- quarter Kelly, 5% per bet, 10% per game;
- a daily loss stop;
- halt at a 30% drawdown;
- kill if rolling 50-entry expectancy falls below 0.

## Things that would make every number here wrong (tested in `tests/test_xvenue.py`)

- Polymarket long/short flipped (long is read from the data, not assumed).
- Kalshi YES ask built from the wrong book side.
- ET date vs UTC date: a Monday-night game is 00:15Z Tuesday.
- Team mapping crossing teams within a game.
- A fee charged on the wrong price.

## Amendments (dated, before any analysis)

- **2026-09-30, before any gap was computed:** the recording window was changed from
  36 h to 7 days ahead. 36 h held only 17 games on a Wednesday, because CFB is
  mostly Saturday. More pre-game games give a faster verdict; the rules are
  unchanged.
  - Consequence: a full sweep of ~300 games takes ~3 min, so "consecutive sweeps"
    are 3–5 min apart. That makes the S1 persistence rule stricter, not looser.
  - S1/S2 now also include entries days before the game. Capital is locked longer
    for those, and the report shows net per $ per day held.
