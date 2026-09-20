# Research log — what is dead, what is a candidate, what is being measured

The point of this file is to stop good ideas from being re-proposed after
they have already been paid for and killed. Add to it before you add a
strategy.

## 1. Dead: static and logical arbitrage

Twelve proposals in `research/graveyard.jsonl`, every one buried with **"no
price violation on sealed holdout"**:

| family | proposals |
|---|---|
| YES + NO must sum to 1 | `8008f793`, `58a0ebcc`, `cea49a6f`, `b0890e2b` |
| event-exhaustive outcome sums | `e8300c7d` |
| spread → moneyline implication | `631eb601` |
| spread → total implication | `51b102b2` |
| spread ordering / same-team monotonicity | `33bbfd4e` |
| over-under threshold monotonicity | `031b7f36` |

The books are internally consistent. **The edge is not in cross-market
inconsistency.** That leaves exactly two places it can be: *calibration*
(price vs realized frequency) and *latency* (price vs news).

## 2. Dead: anything that needs convexity

A binary contract is **linear in probability**. YES + NO ≡ $1 at settlement,
always. So the obvious "straddle" — hold both sides, wait for a swing, sell
one leg and ride the other — has *zero* volatility exposure. It is a $1 bond
bought for $1 plus two spreads, and after you sell a leg you are simply long
the other one at a worse entry than buying it outright.

This is the structural difference from options, where gamma makes a straddle
pay on realized vol in either direction. There is no gamma here. Any strategy
whose thesis is "profit from movement regardless of direction" is dead on
arrival on a single binary market.

The tradeable cousin needs two legs *not* pinned to $1 — correlated but
distinct markets. See section 1: already buried.

## 3. Candidate: calibration (favourite–longshot)

    make calibrate            # run_calibration.py --split-half

On 848 resolved markets from the cached global tape, one leg per market,
sampled before the final 30% of each tape:

```
      bucket     n  avg px    win     edge              95% CI
   0.15-0.30   183   0.234  0.164   -0.070  [-0.121,-0.012] *
   0.45-0.60   303   0.528  0.502   -0.026  [-0.081,+0.030]
   0.60-0.75   240   0.670  0.692   +0.022  [-0.038,+0.078]
   0.75-0.90   157   0.821  0.917   +0.096  [+0.051,+0.137] *
```

### It is an esports result, not a general one

`label_tape.py` resolves every cached tape to its gamma question and category.
The mix that produced the table above:

```
  Esports 1032 | Other 211 | Politics 30 | Crypto 29 | Sports 15
```

**Sports has 15 observations.** Split by category (`--by-category`), esports
is where the whole effect lives, and slightly stronger: `-0.113` on 0.15-0.30
and `+0.086` on 0.75-0.90. "Other" flips sign in the low buckets. So the
headline number is a Valorant/LoL result, and the venue being traded is UFC
and college football.

`make run` still ships `MIN_PX=0.60`, on the grounds that favourite-longshot
bias is well documented across betting markets generally and that buying
0.08 lottery tickets needs no study to reject. Treat it as a **prior**, not
as a measurement of this venue. To replace the prior with evidence:

    python fetch_tape.py --category Sports --max-events 300 --match-only
    python label_tape.py && python run_calibration.py --category Sports

**Status: candidate, not a finding.** Reasons to distrust it:

* 14 buckets were tested, so ~0.7 false positives are expected by chance.
* Split-half gives +0.067 and +0.030 on favourites — same sign, unstable
  magnitude. Size on the weaker one, if at all.
* Resolution is *inferred* from terminal tape prices, so late samples are
  partly self-fulfilling. `--cut 0.5` weakens it to +0.075 but keeps the sign.
* Only cleanly-resolved, actively-traded markets survive the filter.
* This tape is Polymarket **global**, and 77% esports. The live venue is US
  sports. Transfer is an assumption, and the category split above is evidence
  against it rather than for it.

What it is already good for: the *negative* result is better supported than
the positive one, and buying longshots is precisely what the bot was doing
(118 shares at 0.084). `make run` now passes `MIN_PX=0.60`.

**To promote it to a finding:** validate forward on the US venue. Record
entry prices from `research/us_timeseries.jsonl`, join to resolutions after
settlement, and re-run the same bucketing. Needs ~200 resolved markets before
the CI means anything.

## 4. Open: latency and divergence

    make watch WATCH_MIN=180     # log book + ESPN plays, no orders
    make lag                     # analyse it

`log_edge.py` records the US top-of-book and the ESPN play feed on one clock.
ESPN is keyless and gives every play an ISO `wallclock` plus its own live win
probability. `analyze_lag.py` reports three things, any of which kills the
idea:

1. **Feed staleness** — how old a play already is when the API hands it to
   us. This is the ceiling. If plays arrive 20s late, no amount of fast code
   beats a market watching the broadcast.
2. **Repricing lag** — time from a scoring play to the first ≥1 tick book
   move. *Negative means the book moved before our feed delivered the play*,
   i.e. we are structurally late and this feed is untradeable.
3. **Divergence** — market mid vs ESPN win probability. A gap that is
   persistently **wider than the spread** is tradeable; a gap inside the
   spread is not.

**Kill criteria, decided in advance:** if median feed staleness exceeds median
repricing lag, abandon the latency thesis on this feed and do not try to
optimise the code path. The problem is the feed, not the software.

### Preliminary, n=2 — do not act on this yet

A capture during the closing minutes of LSU @ MISS (2026-09-19) appended only
two live plays before the game ended, with staleness of **66.7s and 33.8s**.
Our poll interval was 4s, so that delay is ESPN's own publishing, not ours.

If a full-game capture confirms ~30-60s, **the latency thesis is dead on the
free feed**: a paid low-latency feed (Sportradar, Genius) is the only way to
race a book, and that costs orders of magnitude more than this account.

That would not kill the *divergence* thesis. A stale win probability is still
usable if the market takes minutes to converge to the right level — slowness
of the venue, not speed of the feed, is what that trade needs. Measure the
gap's persistence, not its arrival time.

**Next capture: a full game from kickoff**, both halves, with the book
attached (drop `--espn-only`). n=2 at the end of a clock-stopped fourth
quarter is an anecdote.

## 5a. The account is promotional credit, not cash

`make account` dumped the balance fields the report did not recognise:

```
bonusHold: 25, bonusReservation: 9.01045, displayedBonus: 9.01045,
displayedCash: 0, assetAvailable: 0, availableToWithdraw: 0
```

**`displayedCash: 0` and `availableToWithdraw: 0`.** The $9.01 is bonus
credit against a $25 promotional hold, not money. Nothing in this account is
currently withdrawable, so "profit" here is not realisable profit until
whatever the bonus terms require has been satisfied. Read the promo terms
(wagering requirement, expiry, whether winnings convert) before sizing
anything, because they, not the edge, set the objective function.

This is also why `assetNotional` read $0.00: the venue is tracking bonus and
cash separately and the report was reading the wrong drawer.

## 5b. Every reward was SKIPPED — probably because of --buy-only

Four rewards, $0.08 gross, **$0.0000 credited**, all `SKIPPED`. Liquidity
programmes generally require a **two-sided** quote to qualify. The bot ran
`--buy-only`, which posted bids and no asks, so it plausibly never qualified
for any of them — the $0.08 was never going to land.

If that is the cause, the exit-quoting change (asks whenever inventory is
held) is also the fix, and the test is cheap: run two-sided for one period
and see whether status moves off SKIPPED. **Do that before concluding
anything further about reward economics** — every number in section 5 was
measured on a bot that may never have been eligible.

## 5. Settled: the rewards programme is not the business

Two days of live quoting produced **$0.07 gross, $0.04 credited**, while
carrying **$34.79** of one-sided directional inventory. Reward share is
size-over-book, and Target Size runs to hundreds or thousands of shares, so a
$44 account is a rounding error in every book it quotes. Rewards are a
subsidy on a position you wanted anyway, not a revenue line.

This is why the bot now exits inventory instead of hoarding it
(`--buy-only` means "never short", not "never sell") and caps cost basis per
market (`MAX_INV`).

## 7. Open: crypto binaries priced off spot

    python run_digital.py --by-horizon       # BTC, calibration of the model

Why this and not sports: the underlying is free, continuous and sub-second
observable, so fair value is *computable* rather than guessed —
`P(S_T > K) = N(d2)` — and it can be validated tonight against 67,539 hourly
bars already on disk, instead of waiting weeks for match resolutions.

`run_digital.py` deliberately does **not** backtest a strategy. It asks
whether the model is calibrated, because a gap between an uncalibrated model
and a market price tells you nothing about who is wrong.

**Result so far: the model is not yet trustworthy.** Brier skill is +37.5%
over a constant baseline, but actual outcomes run above model almost
everywhere (+0.073 at 0.60-0.75). Two causes, separated by testing:

1. **Drift.** Per-year error tracks BTC's return almost exactly: +0.065 in
   2020 (+305%) against -0.021 in 2022 (-65%). A zero-drift model cannot
   know the asset went up 20x, and "crypto goes up" is a macro bet, not a
   pricing edge. Not tradeable.
2. **Overstated volatility.** A residual +0.03-ish survives in every year
   including the bear ones. It is under-confidence in *both* directions,
   which is the signature of σ being too high — EWMA decays slowly after a
   vol spike. `--vol-scale 0.8` flattens both tails (+0.066 → +0.032 mid,
   -0.025 → +0.008 low) and improves Brier 0.1949 → 0.1939.

A hypothesis that was **wrong**, recorded so it is not retried: the `-½σ²T`
convexity term is not the cause. Removing it moves the mid bucket only
+0.066 → +0.062.

**Do not trade this yet.** λ=0.8 was grid-searched on the same data that
measured it, which is curve fitting. Before it prices anything: fit the vol
estimator on one period, test on a disjoint one, and confirm the flattening
survives. Only then is a market-vs-model gap evidence about the market.

## 8. Tested: in-play swing trading (momentum / mean reversion)

    python run_swing_offline.py --by-category --gap 3

The load-bearing question for day-trading events: does an in-play move predict
the next move, net of costs? Rebuilds size-weighted 5-minute bars per outcome
token from the cached tape and relates the past 3-bar move to the forward
3-bar move, skipping `--gap` bars in between.

**Result: no edge survives scrutiny.** The slope is negative (-0.047), i.e.
slight mean reversion, and fading a >5c fall appeared to pay +0.022 net of a
2c round trip (t=8.0), surviving a 15-minute gap (+0.014, t=5.2) so it is not
bid/ask bounce. It still does not hold up:

* **`mean fwd` is positive in EVERY bucket**, including flat and including
  rises. Falls recover *and* rises continue. That is not reversion, it is an
  upward drift in the sample. Across all observations it is **+0.0055**,
  where a balanced Yes/No sample must be ~0 because the two legs cancel.
* **The "edge" is monotonic in price level and flips sign at the top:**
  +0.0181 at 0.02-0.15, +0.0085 at 0.15-0.30, +0.0046 at 0.30-0.70,
  **-0.0094** at 0.70-0.85, -0.0087 at 0.85-0.98. Genuine reversion would be
  roughly symmetric. This is the signature of a variable bounded in [0,1]:
  after a fall you sit near the floor, where absolute moves up exceed moves
  down. "Fade the drop" is mostly "buy the thing with more room above it".
* Mid-book (0.30-0.70) still carries a +0.0066 mean forward move that the
  boundary does not explain. **That unexplained bias is larger than the
  +0.0046 the mid-book trade would earn**, so the trade is inside the error
  of the measurement.

**Do not trade this.** To revive it, explain the +0.0055 first: it is probably
in the bar construction (empty bars are dropped, so a forward window spans
variable real time, and thin trading correlates with direction). Rerun on
fixed real-time spacing with both legs of each market forced into the sample,
and check that the mean forward move is ~0 before reading any bucket.

The honest summary of the day-trading thesis so far: at a 2c round trip on
these books, a signal needs to move ~2.5c reliably. Nothing measured here does
that once the bias is removed, and the cost is the binding constraint, not the
signal.

## 9. The best idea found so far: spread-ladder shape

    python fetch_scores.py --league nfl --from 2012 --to 2025
    python run_keynumbers.py --league nfl
    python run_ladder.py --list

**Section 2 was too broad and this corrects it.** "Two opposing positions" is
dead on YES/NO of one market, because those are pinned to $1 and have no
convexity. It is *not* dead across two strikes of a spread ladder, which are
pinned to nothing. The venue lists ladders: `asc-cfb-clmsn-cah-2026-09-25` has
`pos-5pt`, `neg-5pt`, `pos-6pt`, `neg-4pt`, `pos-7pt`, `neg-7pt`.

A ladder is a discretised CDF of the margin of victory, and two adjacent
strikes isolate an exact margin:

    P(margin > k-0.5) - P(margin > k+0.5) = P(margin == k)

Football margins are built out of 3s and 7s, so that distribution is spiky.
From 3,825 NFL finals (2012-2025) against a normal(11.3, 9.1):

```
 margin   games   actual   smooth   ratio
      3     553   0.1446   0.0288    5.02x
      7     328   0.0858   0.0392    2.19x
      6     257   0.0672   0.0369    1.82x
      9      60   0.0157   0.0424    0.37x
     12      68   0.0178   0.0438    0.41x
```

A ladder priced off a smooth curve misplaces **0.276** of probability mass
(CFB: 0.241, with extra spikes at 10 and 14). Buying the 2.5/3.5 vertical from
a smooth-pricing counterparty is worth +0.116 per $1 before cost.

Why this is the most promising thing in this file:

* **It does not require predicting the winner.** It is relative value between
  two strikes on the same game - direction-neutral.
* **Bounded, known risk.** The vertical costs what it costs and pays $1 or 0.
* **The edge (+0.116 on margin 3) is far larger than the ~0.02 spread**, which
  is the constraint that killed section 8.
* **It uses the maker infrastructure that already exists.**

Reasons it may still be nothing, in order of how likely they are to kill it:

1. **Key numbers are the most basic concept in football handicapping.** Any
   competent counterparty prices them correctly, and the 5x figure only
   applies against someone pricing smoothly. Nobody serious does.
2. **Contract semantics are unconfirmed.** "5pt" may mean "> 5" or ">= 5",
   which differ by exactly the point mass at 5 - the thing being traded. Get
   this wrong and the sign of the trade flips. **Confirm before sizing.**
3. The ladder books were empty near the touch in `make hunt`, so the mids may
   not be real prices.
4. Both legs must fill. One-legged, it is a naked directional bet.

### The semantics, and the bug that came from guessing them

The first version of `run_ladder.py` assumed a strike meant `P(margin > k)`
and reported **nine risk-free arbitrages** on the first live ladder. All nine
were fake. The live prices run 0.033 at the -20.5 line up to 0.988 at +20.5 -
they *increase* with the line, and a `P(margin > k)` series must decrease.

The real convention: `pos-7pt5` is the team **receiving** 7.5 points,
`neg-7pt5` is the team giving them. Writing the line as signed L, the contract
pays iff `margin > -L`, so price is non-decreasing in L. Covering +5.5 is
strictly easier than covering +1.5.

The tool also printed implied probabilities of **-0.070, -0.143, -0.165**.
Negative probabilities, shipped with a recommendation to trade on them. That
is the fourth time in this project that assuming an API's semantics instead of
checking them produced a confident wrong answer (`assetNotional`,
`balanceReservation`, `category`, now this). `run_ladder.py --self-test` pins
the direction against real quotes so it cannot silently invert again.

### What is actually there

Re-run on **executable** prices - bid of the harder leg against ask of the
easier one, never mids, because a mid-based check invents arbitrage out of a
wide spread - the same ladder has **6 genuine violations**:

```
sell -1.5 @ 0.595  buy +0.5 @ 0.555   credit +0.040/share, worst case 0
sell +1.5 @ 0.605  buy +5.5 @ 0.570   credit +0.035/share, worst case 0
sell -1.5 @ 0.595  buy +5.5 @ 0.570   credit +0.025/share, worst case 0
sell -0.5 @ 0.580  buy +0.5 @ 0.555   credit +0.025/share, worst case 0
... 2 more at +0.010
```

These are real: covering the higher line is strictly easier, so long-higher /
short-lower can never lose, and the credit is kept. **This is the first
positive-expectancy result in this file.** It is also the smallest - the
binding question is depth at the touch, which the tool now prints. A 4c edge
on 2 shares is 8 cents.

Caveats before sizing: both legs must fill or it is a naked directional bet;
the semantics above are inferred from price shape, not from venue documents,
and should be confirmed; and short legs need collateral, so a pair ties up
roughly $1 for a ~$0.04 lock.

### Key numbers: not testable on this ladder yet

With the sign fixed, the implied point masses come out far ABOVE empirical
(margin 7: implied 0.165 against 0.033 actual). That is not evidence the
market overprices key numbers - it is the same inconsistency that produces the
arbitrage above, contaminating every adjacent pair. **An inconsistent ladder
cannot be read as a distribution.** Retest only on a game whose ladder passes
the monotonicity check.

### Live verdict: the theory is confirmed, the capacity is cents

Run on the real `clmsn-cah` ladder with depth printed:

```
sell +1.5 @ 0.605  buy +3.5 @ 0.565   credit +0.040 x 1 share  = $0.04
sell +1.5 @ 0.605  buy +5.5 @ 0.570   credit +0.035 x 1 share  = $0.04
sell +2.5 @ 0.590  buy +3.5 @ 0.565   credit +0.025 x 1 share  = $0.03
...9 violations, 2 of them with ZERO size on one leg
TOTAL LOCKABLE ON A 30-STRIKE LADDER: $0.16
```

**The key-number thesis is confirmed in live prices.** The market implied
P(margin == 3) = **0.005** on one side and a *negative* number on the other,
against an empirical 0.057 / 0.037. Three is the most common margin in
football and this ladder prices it near zero - an 11x underpricing, exactly
the error a smooth curve makes. Buying the -3.5/-2.5 vertical costs 0.010 on
executable prices and pays $1 at ~5.7%: **+0.047 EV per share.**

Depth on that vertical: **2 shares.** EV $0.09.

So the finding is real and it is small. Everything sits at 1-2 shares. The
interesting question is therefore not the per-game edge but the **aggregate
across the slate** - 43 games expose a ladder. `--scan-all` sweeps them and
totals the lockable dollars.

The strategic point: this edge is uncapturable at institutional size, which
is precisely why it still exists. A $9 account is the only kind that can take
all of it. That is the first time in this file that being small is an
advantage rather than the binding constraint.

Open before any of this trades:

* **Confirm the settlement rule for a line** with the venue. Everything above
  rests on semantics inferred from price shape.
* Rate limiting hid 10 of 30 strikes on the first live run. Now 0.6s between
  calls with exponential backoff, and `--near` scans only the strikes around a
  pick'em, where every violation was found.
* Legging risk is the real execution problem at 1-2 shares of depth.

`run_ladder.py` checks both, `--self-test` pins the sign convention, and
`--scan-all` answers the capacity question.

## 10. The blocker: there is no short intent

`src/pm_us/client.py` exposes exactly two intents:

```
INTENT_BUY  = "ORDER_INTENT_BUY_LONG"
INTENT_SELL = "ORDER_INTENT_SELL_LONG"
```

`SELL_LONG` sells shares you already hold. **There is no short intent.** Every
structure in section 9 - the monotonicity pair, the key-number vertical -
needs to sell a leg we do not own. If the venue rejects that, the mispricing
is real and completely unreachable.

This is not a detail to discover in production. `ladder_bot.py --probe` sends
one 1-share sell on a market we do not hold, reports accepted or rejected, and
cancels. One share of risk to answer the question the entire strategy rests
on.

**Nothing should trade until the probe answers.** If it is rejected, section 9
is dead on this venue and the remaining option is a venue that supports
shorting, or buying the cheap leg outright, which is a directional bet and not
the trade.

## 11. Can it run on esports, and 24/7?

**Esports: not on this venue.** Every ladder listed is `asc-cfb-*`, and the
outright markets are `aec-cfb`, `aec-nfl`, `aec-ufc`. The venue is US sports.
`ladder_bot.py` prints the sport breakdown each sweep, so this is checkable
rather than assumed.

Even if it listed esports, the structure is weak there: a Bo3 map handicap has
two or three rungs, and both the monotonicity check and the key-number test
need a dense ladder. Football is unusually good for this precisely because
scores are built from 3s and 7s across a wide range of margins. Esports is
where the *global* tape's liquidity is (1,922 of 2,484 cached markets), but
that is a different venue.

**24/7: yes, and for a better reason than expected.** Every violation found so
far was on a game *days* away, not a live one. Pre-game ladders are thin and
stale, which is exactly where inconsistency lives, so the forward slate is the
hunting ground and it is always populated. The bot sweeps every ladder on a
cycle rather than watching one game.

What 24/7 does NOT buy: depth. At 1-2 shares per violation the constraint is
capacity, not opportunities per hour. Running continuously across 43 ladders
is how a few dollars of edge gets collected; it is not how it gets bigger.

## 12. What actually binds: capital, not opportunities

Each share of a pair ties up `buy_px + (1 - sell_px)` = **1 - credit**, i.e.
about a dollar, until the game settles. The long leg has to be funded and the
short leg can settle at 1 so it needs collateral. (If the venue nets the two
legs the requirement is lower - that is the optimistic case, so budget for
this one.)

```
sell 0.595 buy 0.555 -> credit 0.040, capital $0.96, return 4.2% to settlement
sell 0.605 buy 0.570 -> credit 0.035, capital $0.97, return 3.6%
sell 0.590 buy 0.565 -> credit 0.025, capital $0.98, return 2.6%
sell 0.580 buy 0.570 -> credit 0.010, capital $0.99, return 1.0%
```

So the cap is not a risk dial, it is the whole business:

```
 $5 cap ->  5 share-pairs -> ~$0.15 locked per settlement cycle
 $9 cap ->  9 share-pairs -> ~$0.27
$50 cap -> 52 share-pairs -> ~$1.56
```

Two consequences:

1. **Capital binds before depth does, in aggregate.** Any one violation is
   1-2 shares, but there are dozens across 43 ladders. With $9 you can fund
   about nine share-pairs total, so the scarce resource is dollars and the
   bot must spend them on the best credits. It now demands a progressively
   better edge as capital depletes, so a 0.005 violation early in a sweep
   cannot eat the budget a 0.04 one needs later.
2. **A 4% return to settlement is only good if settlement is soon.** These
   games are days out. 4% over a week is excellent annualised and irrelevant
   in absolute terms on $9.

An earlier version of the bot tracked `deployed` in SHARES and compared it to
a dollar cap, so `--max-capital 5` actually meant "5 shares". It was only
close to right because a pair happens to cost about a dollar. Fixed to track
dollars via `capital_per_share()`.

## 6. Rules

* Anything new goes through the sealed holdout in `research/holdout.yaml`
  before it gets capital. That discipline is why section 1 is trustworthy.
* An edge smaller than the spread is not an edge unless you are the maker.
* Write the kill criterion **before** running the experiment.
