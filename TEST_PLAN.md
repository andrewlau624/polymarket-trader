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

- **2026-09-30, before any gap was computed:** added **S3, rest on Polymarket,
  hedge on Kalshi**. The owner pointed out Polymarket is the cheaper venue.
  As a taker it is not, at size: 1.74c vs 1.75c per contract at 50c. As a
  maker it pays a rebate of 0.0125·p(1−p), so a pair costs ~1.4c in fees
  instead of ~3.5c.

  **S3 — rest on Polymarket, hedge on Kalshi when filled**
  - **Post.** At an observation, for team X, a paper bid is posted on Polymarket
    at `pm_bid(X) + 0.001`, only if:
    - that price is still below Polymarket's ask for X; and
    - `price − rebate + kalshi_ask(not X) + kalshi_fee` ≤ 1 − 0.005 at that
      moment.

    X = long team uses the long book's bid. X = short team means offering the
    long side at `ask − 0.001`.
  - **Expiry.** One live paper order per (game, team); it expires after 30 min.
  - **Fill.** Filled only if a later observation of the game, within 30 min,
    shows Polymarket's ask for X at or below our price, i.e. the market traded
    through us.
    - This is optimistic about the queue but pessimistic about timing.
    - It cannot see fills that happen and revert between snapshots.
  - **Hedge.** On fill, buy the other team on Kalshi at its ask **at the fill
    observation** (taker, rounded-up fee, size = min(depth, 100)). Price moves
    against us between posting and the fill are therefore charged.
  - **P&L per pair:** `1 − (price − rebate) − (kalshi_ask + fee)`. A hedge that
    cannot be placed (no Kalshi ask, or depth < 10) is a failure.
    - Such a fill is marked to the Polymarket mid at the next observation.
  - **PASS** requires all of these over 14 days:
    - ≥ 100 paper fills on ≥ 20 games;
    - the 95% CI of mean net per pair, bootstrapped over games, entirely above 0;
    - mean net positive in both chronological halves;
    - ≥ $5/day of net at the filled sizes (capped at 100 pairs).
  - **KILL early** if after 7 days the mean net per fill is below 0.
  - **Before any real money:** one 1-share real resting order on Polymarket to
    confirm it rests and earns the rebate. RESEARCH.md says this was never
    observed.

- **2026-10-01, before any data was recorded:** added **S4, follow the international
  book onto Polymarket US**.
  - **Edge:** the international venue's book is ~70× deeper than Polymarket US's
    (0.05c vs 3.62c impact for 50k contracts, rivermarkets.com), so it may move
    first. A US price still sitting at the old level can then be bought before it
    catches up.
  - **Data:** `hunt/lag_recorder.py` polls both books back to back, every ~2–5 s,
    for games that are live or start within 2 h.
    - The international venue dates games in UTC and Polymarket US in ET. Pairs
      are confirmed by team names and start time, never by slug alone.
  - **Entry:** buy team X on Polymarket US at the ask when
    `intl_mid(X) − (us_ask(X) + us_fee) ≥ 0.010`.
    - The international book must have spread ≤ 0.02 and ≥ $500 at the touch.
    - Both snapshots must be < 1 s apart.
    - One entry per (game, team) per 10 minutes.
  - **Scored two ways:**
    - (a) **markout:** US bid after 60 s and after 5 min, minus entry cost, which
      means selling into the bid with the fee paid again;
    - (b) **hold to settlement.**
  - **Bootstrap unit:** the game.
  - **PASS** requires all of these: ≥ 100 entries on ≥ 20 games; the 95% CI of (b)
    expectancy per $ above 0; (b) positive in both halves; and a lead–lag showing
    the international mid moves first.
  - **KILL:** if after 30 games the median 60 s markout is ≤ 0 and (b) is below 0.

- **2026-10-01, before any price was compared:** added **S5, Pinnacle anchor on
  soccer**, using the international venue's history.
  - **Edge:** Pinnacle is the sharpest soccer book. Football-Data.co.uk publishes its
    pre-closing (PSH/D/A) and closing (PSCH/D/A) odds for 2025/26 in 16 European
    leagues plus MLS/MEX/BRA/ARG/JPN. If Polymarket's pre-kickoff price strays from
    Pinnacle's and the gap predicts results, a trader watching Pinnacle live (free
    resellers exist) can buy the cheap side.
  - **Matching:** a Football-Data match is paired with a Polymarket event by league,
    kickoff date and both team names. Polymarket has three yes/no markets per match
    (home win, away win, draw); kickoff is the market end time, cross-checked with
    Football-Data's UK-time kickoff. Unmatched or ambiguous matches are dropped.
  - **Fair value:** Pinnacle odds de-vigged proportionally (1/odds ÷ sum).
    "No" = 1 − "Yes".
  - **Entry:** the first taker BUY of a Yes or No token at price p with
    `p + 0.0695·p(1−p) ≤ fair − 0.02`, at most one per (match, token).
    - **Primary:** fair = closing line, window kickoff −30 min to −1 min, when
      Pinnacle is at or near its close.
    - **Secondary:** fair = pre-closing line (collected Tue/Fri afternoons), window
      kickoff −12 h to −1 min.
  - **Trade:** hold to resolution.
  - **Diagnostic (no pass bar):** Brier score of Polymarket's last price before
    kickoff against Pinnacle's closing probability, on the same outcomes.
  - **Bootstrap unit:** the match.
  - **Split:** kickoffs before 2026-03-01 are reported, but the verdict comes from
    2026-03-01 onward. There are no parameters to fit; the 2c threshold and both
    windows are fixed here.
  - **PASS** on matches from 2026-03-01: ≥ 100 entries on ≥ 50 matches, the 95% CI
    of return per $ above 0, and positive in both halves of that period.

- **2026-10-01, before running it:** added **T1, tennis favourite around match time,
  re-tested without the duration leak**.
  - The scan's "last hour before close" tennis cells leaked the outcome: comebacks make
    matches longer.
  - **Timing:** this version times entries from the **scheduled start**,
    S = `end_date` − 7 days (the venue's placeholder convention). S is known in advance.
  - **Markets:** ATP, WTA and minor tennis match winners on the international venue;
    set, total, handicap and spread markets are excluded.
  - **Entry:** the first aggressive buy of a token priced in band A (0.65–0.80) or
    band B (0.80–0.90), timestamped in [S, S + 6 h].
    - The slow-fill variant takes the first such buy ≥ 60 s after that one.
  - **Trade:** hold to resolution with the Polymarket US taker fee. One entry per
    (market, token, band). The bootstrap unit is the event.
  - **PASS:** for a band, on markets resolving from 2026-03-01, ≥ 200 markets and the
    95% CI of return per $ above 0 in **both** the first-fill and the slow-fill variants.
    Train is reported but cannot pass on its own.

- **2026-10-01, before any rating was compared with a price:** added **T2, table-tennis
  ratings vs Polymarket US**.
  - **Edge:** Setka Cup, Czech Liga Pro and TT Elite trade only on Polymarket US.
    Players play many matches a day, so a rating built from the venue's own settled
    results may know more than a thin book.
  - **Ratings:** Elo per league, fitted only on matches that started before the match
    being priced. Ratings come from `research/us_closed.jsonl`: the winner is the side
    whose settled price is 1, and 0.50 settlements are skipped.
    - K is chosen on matches before 2026-09-01 and then frozen.
    - New players start at 1500, and a player needs ≥ 20 prior matches to be traded.
  - **Prices:** `hunt/niche_recorder.py` snapshots, taking the last snapshot ≥ 60 s
    before the scheduled start. The state must be OPEN with an ask on the side bought.
  - **Entry:** buy side X at its ask when `elo_p(X) − (ask + 0.0695·ask(1−ask)) ≥ 0.05`,
    at most one per match. Hold to the venue's settlement.
  - **Bootstrap unit:** the match.
  - **PASS:** ≥ 200 entries on ≥ 200 matches, the 95% CI of return per $ above 0,
    positive in both chronological halves, and src/sim.py P(50% drawdown) < 10%.
  - **Diagnostic:** Brier of Elo vs Brier of the recorded mid, on the same matches.

- **2026-10-01, before any order:** added **M2, fill the empty side of a reward market**.
  - **Edge:** Polymarket US pays a daily pool per market. Each side of the book earns
    only in seconds when that side holds ≥ Target Size, and without a Max Spread each
    side is scored on its own even if the other side is empty
    (docs.polymarket.us/incentives/liquidity).
    - On 2026-10-01 about 90 market-sides in daily programmes had **no** bids.
    - A post-only bid at 0.1c for Target Size (collateral = 0.001 × size, e.g. $5–$20)
      would be the side's only order, so it would score 100% of that side every second.
    - If filled, we own a longshot bought at 0.1c, so the loss is capped at the
      collateral.
  - **Selection** (`hunt/reward_bot.py`, decided now):
    - daily or daily_event period, no Max Spread, market OPEN, and the market's
      endDate > 5 days away;
    - the side holds < 10% of Target Size, and our completing order sits at ≤ 1c (bid)
      or ≥ 99c (ask);
    - modelled pay = (pool ÷ markets in programme) ÷ 2 ≥ $1.10 per day;
    - at most $25 collateral per market and $100 in total.
  - **Measured outcome:** earnings rows from `/v1/incentives/earnings` (PAID / PENDING /
    SKIPPED) for every market-day we held, after the 5 + 2 business-day lag.
    Withdrawability is read from `availableToWithdraw` after crediting.
  - **PASS:** ≥ 5 markets × ≥ 3 daily periods, PAID ≥ 50% of the modelled amount,
    nothing withheld, and the credited rewards are withdrawable cash.
    - "PAID but credit only" is reported as a partial result.
  - **KILL:** any earnings row withheld or flagged for abuse, or every row SKIPPED
    after two periods.

- **2026-10-01, before any live data was recorded:** added **T3, table tennis in play:
  model price vs the US book**.
  - **Data:** `hunt/tt_live.py` polls every live table-tennis event about every 2–5 s.
    For each one it records the venue's own score (`/v1/events/{id}`: game-by-game
    points, `period` S1..S5) and the moneyline book, fetched back to back.
  - **Model (fixed now):**
    - Take p0 = the last pre-start mid.
    - Solve for the per-point probability q that makes P(win best-of-5, games to 11
      win by 2) = p0. Serve is ignored; the standard 11-point model makes the server
      irrelevant to game probability (arXiv 1109.6628).
    - Then P(win | games, points) comes from that q.
  - **Leader / lag diagnostic:** at every poll where the score changes, compare the book
    mid at that poll with the previous poll and with the next 3 polls. Report the share
    of point changes and of game changes where the book had *already* moved more than
    half of its eventual move before the score showed the change.
  - **Entry:** at a poll where the score just changed, buy side X at the ask when
    `model(X) − (ask + 0.0695·ask(1−ask)) ≥ 0.05`.
    - Polymarket state must be OPEN, ask size ≥ 10, and at most one entry per
      (match, game number).
  - **Scored:**
    - (a) markout: sell into the bid 30 s and 120 s later, fee paid again;
    - (b) hold to the venue's settlement.
  - **Bootstrap unit:** the match.
  - **PASS:** ≥ 150 entries on ≥ 75 matches, (b) 95% CI per $ above 0, both
    chronological halves positive, and (a) at 120 s with mean > 0.
  - **KILL:** if, after 200 matches, the book leads the score on > 80% of game changes
    (no lag to exploit).

- **2026-10-01, before any rating was compared with a price:** added **T4, tennis Elo vs
  the pre-match price (international-venue history), aimed at ITF**.
  - **Edge:** in lower-tier tennis, books rely on thin information and studies find the
    favourite–longshot bias strongest there. Polymarket US lists ITF, UTR, ATP and WTA.
  - **Matches:** single match-winner markets `atp-`/`wta-`/`itf-` on the international
    venue, resolved cleanly, from `markets.parquet`.
    - Doubles ("/") and "Completed Match" markets are excluded.
    - Players come from the question "<event>: A vs B", with answer1 = A.
    - Scheduled start S = end_date − 7 d.
  - **Ratings:** walk-forward Elo per player, men and women pooled (they never meet),
    updated in S order. K is chosen on S < 2026-03-01 from {16, 24, 32, 48}, then frozen.
    Both players need ≥ 10 prior rated matches.
  - **Price:** for each token, the LAST aggressive buy in [S − 6 h, S): a real ask
    before play.
  - **Entry:** buy token X when `elo(X) − (price + 0.0695·p(1−p)) ≥ 0.05`, at most one
    per match (the larger edge). Hold to resolution.
  - **Bootstrap unit:** the event.
  - **PASS (per level, ITF is the primary):** on S ≥ 2026-03-01, ≥ 300 entries, the 95%
    CI of return per $ above 0, and both chronological halves positive.
  - **Diagnostic:** Brier of Elo vs Brier of price on the same matches.

- **2026-10-01, before any price was compared:** added **T5, Czech Liga Pro: Polymarket
  US vs bet365**.
  - **Data:** OddsPapi `odds-by-tournaments` (tournament 36349, bookmaker bet365) is
    snapshotted every 3 h, which is 8 requests a day inside the free quota, by
    `hunt/clp_odds.py`. OddsPapi fixture ids are Sportradar ids, the same as
    `sportradarGameId` on Polymarket US events, so the join is exact.
    - Polymarket books come from `hunt/niche_recorder.py`, every 5 min.
  - **Fair value:** bet365 match-winner odds de-vigged proportionally, from the snapshot
    taken before the Polymarket quote and ≤ 3 h old.
  - **Entry:** the LAST Polymarket OPEN quote ≥ 60 s before the scheduled start. Buy
    side X at the ask when `fair(X) − (ask + 0.0695·ask(1−ask)) ≥ 0.04`, one per match.
    Hold to settlement.
  - **Bootstrap unit:** the match.
  - **PASS:** ≥ 150 entries on ≥ 150 matches, the 95% CI of return per $ above 0, both
    halves positive, and P(50% drawdown) < 10%.
  - **Diagnostic:** Brier of bet365 fair vs Polymarket mid on the same matches. If
    Polymarket's Brier is lower, bet365 is not the better-informed book and the test is
    expected to fail.
- **2026-10-01, T5 amendment, before any T5 data existed:** bet365 snapshots move from
  every 3 h to every 4 h, and a fair value may be ≤ 4 h old (was 3 h). The reason is the
  quota: one daily names request is needed so the droplet does not depend on a 113 MB
  local file, and 6 + 1 requests a day ≈ 210 a month stays under the free 250.

- **2026-10-01, T3 verdict and new T6.**
  - **T3 FAILED its bar.** On the droplet: 200 entries on 97 matches, held to settlement
    −1.3% per $, 95% CI [−18.5%, +14.9%].
  - **Diagnostic.** The venue's score API showed the change before the book had made
    half its move in 75% of 863 point changes and 65 game changes. The 30 s markout was
    +1.27c (n = 192).
  - **T6, pre-registered now: buy the scorer while the book lags, sell 30 s later.**
    It is graded ONLY on `hunt/tt_live.py` rows with t ≥ **1790879715** (this
    registration). Nothing recorded before that counts.
    - **Trigger:** a poll where the parsed score differs from the same match's previous
      poll (≤ 6 s earlier), and the book mid moved < 0.005 between those two polls.
    - **Size of the jump:** jump = T3 model(now) − T3 model(previous state) for the long
      player, with the T3 model and p0 rule unchanged. |jump| must be ≥ 0.04.
    - **Entry:** buy the side the jump favours at its ask. Polymarket state OPEN,
      ask size ≥ 10, at most one open trade per match.
    - **Exit:** sell into the bid at the first poll ≥ 30 s after entry. Both taker fees
      are charged. There is no hold to settlement.
    - **Bootstrap unit:** the match.
    - **PASS:** ≥ 300 trades on ≥ 100 matches, the 95% CI of mean P&L per trade above 0,
      and positive in both chronological halves.
    - **KILL:** mean ≤ 0 after 300 trades.
    - **Before any money:** a live check that an order sent within ~1 s of the score
      change fills at the recorded ask. Polling every 2.5 s is not proof of fillability.

- **2026-10-02, verdicts.**
  - **T6 KILLED:** 1,813 trades on 196 matches, −0.0735/share [−0.0931, −0.0573].
  - **T3 FAILED:** 442 trades, −0.8% per $ [−12.7%, +10.6%].

- **2026-10-02, before any S2b data:** added **S2b, Kalshi leads, short hold on
  Polymarket**.
  - **Why:** on the droplet's first 1.15 days, when the pre-game mids differed by ≥ 2c,
    Polymarket did 87% of the closing (8,349 gaps).
  - **Data:** `xvenue_recorder.py` rows with t ≥ **1790927546** only.
  - **Entry:** the S2 rule, Polymarket side only. Buy team X on Polymarket at the ask
    when `ask + fee ≤ kalshi_mid(X) − 0.010`, with Kalshi's spread ≤ 0.03, pre-game.
    One open trade per (game, team).
  - **Exit:** sell into Polymarket's bid at the first observation of that game
    ≥ 300 s later; a variant uses 1,800 s. Both taker fees are charged.
  - **Bootstrap unit:** the game.
  - **PASS:** at 300 s, ≥ 300 trades on ≥ 40 games, the 95% CI of mean P&L per trade
    above 0, and both halves positive.
  - **KILL:** mean ≤ 0 after 300 trades.
- **2026-10-02, data-quality filter for S1/S2/S2b/S3, not a rule change:** snapshot pairs
  whose two venues were fetched more than 2 s apart are dropped. The droplet logged one
  pair 123.5 s apart, and a gap between stale and fresh quotes is not a tradeable gap.
- **2026-10-02:** the T3/T6 table-tennis live recorder is retired, because both tests
  failed or were killed. Its report was also running the droplet out of memory.
