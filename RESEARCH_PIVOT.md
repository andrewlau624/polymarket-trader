# Research pivot — is there any edge left, and where?

Phase 1 of the pivot (2026-09-30). Research only: no strategy code and no orders.
Everything in `RESEARCH.md`, `INCOME.md` and `WEATHER.md` is taken as settled and
is not re-tested here.

**Short answer.** Nothing on this venue can be bought at a price that beats the
market by **taking**. Every taker idea we found, ours or anyone else's, loses to
fees or to faster people. The only edges that show up in large datasets go to the
**maker**: the side that rests orders and gets paid, through the venue's reward
pools or through other people's optimism. Both are small for a $200 account. Both
can be measured cheaply, and neither has passed any test yet.

---

## Two corrections to the old notes, found during this research

1. **The reward pools are shared, not per game.** `STRATEGY.md` and `HANDOFF.md`
   read "$8,500 per game". The live endpoint (`gateway.polymarket.us/v1/incentives`
   needs no key) shows the same `rewardPool` repeated on every market of a
   programme. For example, `nfl_spreads_live` lists $21,000 on each of its 1,271
   markets, which works out to about $16.50 per market.
   - Today there are 115 distinct programmes, worth **about $73k in total** across
     ~30,000 markets. That is not $48.6M, which is what you get by adding the
     repeated pools.
   - The docs do not say outright that pools are shared. Treat this as strongly
     implied but not proven.
2. **"SKIPPED" is very likely the $1 minimum.** The docs say rewards under $1.00
   are not paid (docs.polymarket.us/incentives/liquidity). We earned $0.08 gross,
   so the rewards were skipped for that reason, not because of a hidden
   qualification rule.
   - The docs also say you do not need to quote both sides unless the programme
     sets a `maxSpread`. That kills the "--buy-only disqualified us" theory.

## How the venue's reward scoring actually works (documented)

Source: https://docs.polymarket.us/incentives/liquidity

- **Sampling.** The book is sampled once a second. Each order scores
  `discountFactor^(ticks behind best) × size`.
- **Normalisation.** Each side's total score is set to 1.0 every second. **Every
  second is worth the same however thin the book is**, so a small order in a thin
  book takes a large share.
- **Target Size.** Only the first `targetSize` shares counted outward from the best
  price score. Target Size is for all traders combined, not a per-person minimum.
- **Penalty for standing back.** Discount factors are 0.1–0.5, so one tick behind
  the best price earns 10–50% as much.
- **Periods.** `early`, `day_of` (6 h before start) and `live`. Payout comes 5+2
  business days after the period. Cancelled or postponed games pay nothing.
- **Unknown.** It is not clear whether the money is withdrawable cash or
  promotional credit. The user programmes pay credit; the liquidity programme only
  says "credited".

Where the money sits today, per market (pool ÷ markets in programme):

| programme | per market | target | note |
|---|---:|---:|---|
| mlb_wc_moneyline_live | $538 | 25,000 | 4 markets, books hold ~1M shares at the best price → tiny share |
| ufc_main_moneyline_live | $455 | 20,000 | |
| wnba_moneyline_live | $400 | 10,000 | |
| nfl_moneyline_live | $355 | 150,000 | ~17k shares of score per side → $100 gets ~1% |
| ufc_main_moneyline_dayof | $57 | 20,000 | pre-fight |
| mlb_wc_moneyline_day_of | $44 | 30,000 | pre-game |
| nfl_moneyline_day_of | $12 | 500,000 | pre-game |
| spreads, props, table tennis, esports | $0.01–$16 | | almost all of the 30k markets |

**About 90% of the money is paid in the `live` period**, which is in-play. That is
exactly where this project already showed displayed prices cannot be filled and
informed traders pick off resting orders. The pre-game money is small.

A rough one-snapshot scan of nine books estimated **~$0.004/hour (median) for $100
resting at the best bid**. The best of the nine was $0.07/hour.
- Moneylines with fat per-market pools were mostly not in that sample.
- My own arithmetic on the NFL moneyline books puts $100 at the best price at about
  1% of one side, roughly **$1.80 per game live, before any losses on fills**.

---

## The candidates, scored

Scores run from 1 (bad) to 5 (good). "Capacity" is a realistic $/day for a
$200–1,000 account at top-of-book depth.

### 1. Cross-venue: Polymarket US vs Kalshi (and other CFTC venues)
- **Edge mechanism:** two prices for "the same" event. It is not the same event:
  0 of 74 matched NFL/MLB/EPL pairs were settlement-identical in a pre-registered
  study (github.com/anaborne/cross-venue).
  - Postponement: Kalshi settles at last fair price after 48 h; Polymarket US waits
    two weeks and uses the result (docs.polymarket.us/faqs/sports-faqs).
  - Kalshi weather moved to The Weather Company in Aug 2026; Polymarket US uses the
    NWS CLI.
- **Evidence it exists today:** weak.
  - Taking both sides costs ~3.5c per $1 pair at 50c (Kalshi 0.07·p(1−p) +
    Polymarket US 0.0695·p(1−p)).
  - A September 2026 bid/ask recorder of Kalshi vs Polymarket (global) concluded
    "nothing survived"; the best Fed-market episodes barely beat Kalshi's ~3.25%
    interest on idle cash (github.com/MobinHariri/kalshi-polymarket-microstructure).
  - A live NFL check cost $1.033 after fees for a $1 payout (defirate.com
    calculators).
  - Susquehanna (Kalshi's market maker since 2024) and Wintermute quote both
    venues.
- **Data cost:** $0. Kalshi's REST book and history are public; the Polymarket US
  gateway book is public.
- **Latency needed:** seconds, against professional market makers.
- **Capacity:** both legs are locked to settlement on two venues, with 1–3 day
  transfers between them.
- **Legality:** fine. Kalshi is a CFTC DCM, KYC, retail API.
- **Time to verdict:** 2 weeks of a recorder.
- **Variant worth noting:** rest a free maker order on Kalshi (maker fee 0 on most
  series) priced off Polymarket US. That is market making against Susquehanna, not
  arbitrage.
- **Score: 2.**

### 2. Market making / liquidity rewards on Polymarket US
- **Edge mechanism:** the venue pays a fixed pool per market-second, so in a thin
  book a small resting order collects a share of that pool.
  - It survives competition only where the pool is too small for professionals to
    bother with.
- **Evidence it exists today:** the pools are real and public, and scoring is
  documented.
  - The pre-game money per market is small ($4–57), and the live money sits where we
    already know we get picked off.
  - No one has published net reward-minus-adverse-selection P&L on either Polymarket
    venue. warproxxx/poly-maker's author wrote in Jan 2026: "In today's market, this
    bot is not profitable and will lose money"
    (raw.githubusercontent.com/warproxxx/poly-maker/f1bf5e1/README.md).
  - Our own two days: $0.07 gross against $34.79 of one-sided inventory (RESEARCH.md
    §5).
- **Data cost:** $0.
- **Latency needed:** low pre-game (prices move slowly); high live.
- **Capacity:** probably **$1–5/day** pre-game for $200. Unknown live, where it
  could be negative.
- **Legality:** fine. The programme is open to all participants.
- **Time to verdict:** reward share can be estimated in days from public books. The
  net (rewards − markout) needs real resting orders, because rewards are only paid
  on real quotes and fills only happen to real orders.
- **Score: 3.** Cheap and fast to decide. Its likely ceiling is small.

### 3. Faster data
- **Measured gap:** the venue leads the free Riot feed by ~10 s, ESPN is 34–67 s
  late, and in-venue scouts reportedly price off the stadium (gambling911, Dec
  2025).
- **Esports:**
  - PandaScore Live is €1,000/game/month, and its stats plans exclude
    betting-related use. Its <5 s prediction-market feed is sales-only
    (pandascore.co/pricing, /predictions).
  - GRID holds Riot's exclusive LoL data and is commercial; its free tier is CS2 and
    Dota 2 only.
- **US sports:**
  - Sportradar and Genius are contract-only, and Sportradar's terms bar betting use
    without written approval.
  - SportsDataIO says its own play-by-play runs 20–30 s behind cable TV
    (sportsdata.io/help).
  - The Odds API refreshes in-play every 40 s.
  - Pinnacle resellers cost $99–149/mo for pre-game odds, which is not a latency
    edge; our venue is already within 1.3c of DraftKings.
- **Weather:** 1-minute ASOS arrives 2–5 minutes late (Synoptic docs), and the venue
  already prices the 6-hour max correctly.
- **Verdict:** nothing under ~$1k/month is faster than the people already setting
  the price, and the feeds that might be faster forbid betting use.
- **Score: 1.**

### 4. Structural mispricing (coherence across related markets)
- Already buried. There are 12 graveyard proposals; YES+NO cannot sum below 1 on a
  one-book venue, and multi-outcome groups have one leg (RESEARCH.md §1, §21).
- Ladder monotonicity violations were real, but credits were 1–4c, mostly erased by
  the taker fee, and the capacity is cents (§16, §23b). As a maker they survive, but
  capacity is ~$100/yr (§22).
- **Score: 1.** Not re-tested.

### 5. Settlement / scheduled-release edges
- Kalshi stops trading on CPI and jobs one minute before the 8:30 ET release
  (kalshi-public-docs CPI.pdf).
- TSA weekly closes before Sunday's figure, and AAA gas closes the night before; both
  have ~$50 depth.
- Kalshi weather closes 01:00 ET, before the CLI report. Polymarket weather keeps
  trading after the high, but our own test showed it priced correctly (WEATHER.md).
- Earnings-call "mention" markets backtested +4.1c and then went **−3.6c forward**
  (github.com/palashpawar/kalshi-earnings-edge).
- I found no credible published P&L for "settlement sniping".
- **Score: 1.**

### 6. Markets sharp money ignores (culture, mentions, single-name, politics sub-markets)
- **Edge mechanism:** retail buys YES on exciting outcomes, and the patient maker who
  sells it to them collects the "optimism tax".
- **Evidence it exists today:** the best large-sample evidence in this whole report,
  but measured on Kalshi, not Polymarket US.
  - On 72.1M Kalshi trades, takers average −1.12% and makers +1.12%. The maker–taker
    gap is ~2.2 pp in sports and **>7 pp in world events/media**
    (jbecker.dev/research/prediction-market-microstructure).
  - In single-name markets YES is bought 60.9% of the time but settles YES 32.5% of
    the time (Bartlett & O'Hara, SSRN 6615739, Apr 2026).
  - Entertainment prices are too extreme (arXiv 2602.19520).
  - The bias is shrinking over time (Bürgi/Deng/Whelan, UCD WP2025_19).
  - **This is not our failed longshot test.** That one bought sports longshots as a
    taker. This sells non-sports YES as a maker.
- **Data cost:** $0. Becker's dataset is free (github.com/Jon-Becker/prediction-market-analysis).
- **Latency needed:** none.
- **Capacity:** unknown on Polymarket US. The culture/politics books there are small,
  and the reward programmes on them (`politics_t4_coverage`, AWD, TV, MUSIC) pay
  $0.002–$64 per market.
- **Legality:** fine.
- **Time to verdict:** a day for the Kalshi backtest. Weeks to months for Polymarket
  US settlements, because these markets resolve slowly.
- **Score: 3.**

### 7. Other ideas considered and dropped
- **Volume incentive / taker rebates:** these pay takers, so they reward volume we
  would lose money generating. Rebate tiers start at $250k/month taker volume.
- **Novig, Railbird, Robinhood as cheaper second legs:** fees are lower but the
  sources are unverified, and they bring the same not-identical-settlement problem.
  Revisit only if candidate 1 ever shows a gap.
- **Crypto binaries priced off spot:** RESEARCH.md §7. The model was never shown to
  be calibrated out of sample, and prices correct in under 100 ms.

---

## Ranking and recommendation

| rank | candidate | one-sentence edge | why it might survive | ceiling for $200 |
|---|---|---|---|---|
| 1 | **Pre-game reward-paid market making** on thin, well-funded books (UFC main card, MLB/WNBA day_of, CFB T1 moneylines) | The venue pays a fixed pool every second split by share of resting depth, and thin pre-game books let a small order hold a large share. | Pools are too small per market for professional market makers to fight over, and pre-game prices move slowly so fills are less toxic. | ~$1–5/day if it works |
| 2 | **Maker-side NO in culture / mention / single-name markets** | Retail overpays for exciting YES outcomes, and a patient resting seller collects it. | It is a behavioural bias, not a speed race, and it is documented on 72M trades. | unknown; depth is the question |

Dropped: cross-venue (not identical contracts; fees eat taking; the maker variant
competes with Susquehanna), faster data (too slow or too expensive, and its terms
ban betting use), coherence arbitrage (dead), settlement sniping (markets close
first).

**Plainly:** neither candidate is likely to make "real money" on a $200 bankroll.
The realistic best case is a few dollars a day. What they can do is show, with
measured numbers, whether a positive-expectancy maker process exists. That is the
precondition for scaling capital, and no amount of capital fixes a negative
expectancy.

### Cheapest decisive tests

**Candidate 1, reward MM.**
1. **Free, read-only, ~1 week.** Every 60 s, record the book, scoring depth and the
   pool for every market in pre-game programmes with ≥$20 per market. Compute the
   modelled reward for a hypothetical $50 order at the best price and at one tick
   back.
   - Kill it if the modelled reward is under $1 per market-period (the payout floor)
     on most markets.
2. **If step 1 survives:** a live micro-test, which needs your OK because it places
   real orders. Rest $20–50 two-sided on ~10 markets per day for 2 weeks.
   - Score: `PAID rewards − markout of every fill at 60 s and at the start of the
     game + maker rebate`, bootstrapped over market-days.
   - This is the only way to measure it: rewards and fills do not exist without real
     orders.

**Candidate 2, optimism tax.**
1. **Free, 1 day.** Run Becker's Kalshi dataset, pre-registered:
   - Take maker-side NO fills priced 60–95c on mention, entertainment and
     single-name markets.
   - Training period: before 2025-07. Test period: 2025-07 onward, untouched until
     the rule is frozen.
   - Kill it if the 2026 return after fees is under +2% or the event-clustered t is
     under 2.
2. **If it passes:** record Polymarket US culture/politics books and grade them
   against settlement with the `favs.py` pattern, as maker fills only.

**Next phase (2):** pre-register both in `TEST_PLAN.md` before collecting any data.
