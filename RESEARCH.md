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

## 5c. buyingPower was right all along

Four snapshots were needed to read this correctly:

```
when            cash  buyPow  bonusRes  bonusHold  cash-res
start           9.01    9.01      9.01      25.00      0.00
after probe     9.59    7.59      7.58      24.98      2.00
mid flatten     8.35    8.26      8.23      24.97      0.09
after crossing 17.42   17.33     17.33      24.89      0.09
```

`buyingPower` tracks `bonusReservation` almost exactly, and **both rose when
positions were sold**. So "reserved" never meant "locked by open orders" - it
means "this is bonus credit". Subtracting it from cash produced a meaningless
$0.09 and a warning that `buyingPower` "disagreed", when `buyingPower` was the
correct field the entire time and the derived number was the wrong one.

The report now leads with **TRADEABLE (buying power)** and shows cash, the
bonus portion, and `availableToWithdraw` separately. Section 5a's conclusion
stands - nothing is withdrawable - but the operational reading was inverted:
the account was never short of tradeable capital.

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

## 13. Live dry-run results: the strategy is real and small

Seven sweeps, probe **ACCEPTED** (twice) - the venue permits selling a
contract we do not hold, so the paired trade is executable.

**Persistence is strong.** 12 of 15 violations stand in at least half the
sweeps; only 3 of 19 were one-sweep stale quotes. The `clmsn-cah` ladder has
been internally inconsistent for hours. This is a standing inefficiency, not
noise - nobody is taking it.

**Honest value, after fixing a 7x overstatement.** The first report said
"$1.43 per sweep". Wrong twice: in dry run `execute_pair()` never ran, so
every violation was sized as if it had the whole capital cap to itself (15
violations x 5 shares needed $72 of collateral against a $5 cap); and
recurring violations are a STOCK you take once, not a per-sweep flow. Fixed
numbers, best credits first:

```
 $5 -> $0.20     $20 -> $0.74
 $9 -> $0.36     $50 -> $1.39
```

Sweeps now report $0.15-$0.30, which matches.

**Three structural facts learned from the sweeps:**

1. **The violations are concentrated.** 12 of 15 durable ones sat on a single
   ladder while the bot swept 25 evenly. It now ranks ladders by how many
   opportunities they have produced before, then by date.
2. **Near-dated ladders dislocate harder.** The richest credit seen, +0.060,
   was on a game dated the same day. Games a week out are stale; games about
   to start are actively wrong.
3. **A full sweep takes ~15 minutes, not the ~3 the pause implies** - the
   venue's rate limiting adds about 5x through backoff. Combined with (1),
   breadth is the wrong thing to spend the cycle on.

Two instances were also running at first, sharing one rate limit and halving
each other's coverage (27-minute sweeps, every number logged twice). A pid
lockfile now refuses a second one.

**Dry runs now measure TRUE depth.** Sizes used to be clipped by the
live-trading cap, which is why every violation logged exactly 5 shares and the
cap table had to warn it was understating itself. A dry run places nothing, so
it now runs uncapped unless `--max-capital` is given explicitly.

### The honest bottom line

This works, it is durable, and it is worth about **$0.36 on a $9 account**.
It scales close to linearly with capital up to the depth limit - roughly
$1.40 at $50. The blocker is no longer technical.

Before funding anything: the account is promotional credit with
`availableToWithdraw: 0` (section 5a). A 4%-to-settlement return on locked
bonus credit is not the same thing as a 4% return.

## 14. Next experiments, ranked

The monotonicity trade works because this venue prices related markets in
separate books and does not reconcile them. That is a property of the venue,
not of spread ladders, so the question is where else it applies.

**1. Moneyline vs the ladder's zero crossing.** `neg-0pt5` means the team
gives half a point, so it pays exactly when the team wins outright - which is
what `aec-<game>` pays on. Two independent books, identical settlement, must
agree. This needs no ladder structure, just two markets that settle the same,
and it roughly doubles the surface area. The graveyard's "spread implies
moneyline" proposals were tested on the GLOBAL venue, never this one.
`make crossmarket`.

**2. Other ladder types.** The same machinery works on any monotone strike
set. Candidates: over/under totals, UFC "fight passes round N", and the
half/quarter ladders already visible in the slug list (`-2h`, `-4q`). Those
sub-period books are thinner than full-game, so they should be *more*
inconsistent, and UFC is where the only rewards ever landed. `make families`
inventories what exists before any of it is built.

**3. Listing-time dislocation.** Violations concentrate on particular ladders
and the richest credit seen (+0.060) was on a same-day game. A newly listed
ladder is presumably seeded by a model and only corrected once flow arrives,
so watching for *new* ladders and scanning them immediately should beat
sweeping old ones. This is an optimisation of an edge already proven rather
than a new edge.

**4. The 1-point band.** On `clmsn-cah`, P(margin > 1.5) = 0.597 and
P(margin > -1.5) = 0.607, so the market prices the entire -1.5 to +1.5 band at
**1%**. Margin 0 is impossible in CFB (overtime), but a 1-point margin happens
~3.3% of the time. Same key-number error as section 9, in a band narrow enough
to trade with two adjacent strikes. Blocked by the same thing: an inconsistent
ladder cannot be read as a distribution, so this only becomes measurable once
the monotonicity violations on that ladder are gone - possibly because we took
them.

Deliberately not pursued: anything needing the ladder to be a valid
distribution (blocked), anything needing a low-latency feed (dead, section 4),
and anything needing size (the account is the constraint, section 12).

## 15. Higher reward:risk — exact-margin verticals

Two adjacent strikes isolate one margin. Long the higher line, short the lower,
and the pair pays $1 if the margin lands between them and $0 otherwise, so the
**maximum loss is the premium**. On `clmsn-cah` the margin-3 vertical costs
**0.010** on executable prices, for a 99:1 payoff. Unlike the arbitrage, this
can lose.

The robust argument needs no key-number claim at all. Fit a normal to the
ladder's own strikes - `clmsn-cah` implies margin ~ **Normal(+3.0, 13.0)** -
and then:

```
margin 3: ladder charges 0.010, its OWN fitted shape says 0.0307  -> EV +0.021
margin 1: ladder charges 0.000, its OWN fitted shape says 0.0303  -> EV +0.030
margin 2: ladder charges 0.100, its OWN fitted shape says 0.0306  -> EV -0.069
```

**The ladder disagrees with itself.** Adjacent 1-point strikes near the money
are priced ~0.005 apart when a 13-point-wide distribution implies ~0.03 a step.
That is the same defect as the monotonicity violations - insufficient spacing
in the near-money region - and it makes the cheap verticals positive-EV without
any appeal to football scoring. Note margin 2 is *overpriced* on the same
ladder, so this is not a blanket "buy verticals" claim.

Key numbers are then a kicker on top. Measured as a **local spike** against
neighbouring margins:

```
NFL  R(3) 3.20x  R(7) 2.07x  R(10) 2.07x  R(14) 2.48x  R(1) 0.48x
CFB  R(3) 3.24x  R(7) 3.47x  R(10) 2.39x  R(14) 2.82x  R(1) 0.54x
```

An earlier version divided by a global normal fitted to `|margin|` and got
R(3) = 6.0, which was measuring "3 is far below the mean absolute margin"
rather than "3 is a spike" - a folded distribution is nothing like a normal, so
that denominator was meaningless. `run_keyvertical.py` gates on the smooth EV
and reports the R-boosted figure separately, because the robust number should
decide whether to trade.

Note R(1) < 1: a 1-point margin is *rarer* than its neighbours, so the margin-1
vertical is attractive only on the spacing argument, not the key-number one.

### What decides whether this beats the arbitrage

**Margining, and it is unknown.** If the venue nets the two legs, capital is
the 0.010 premium and the expected return is enormous. If it does not, capital
is ~$1.01 a share and the expected return is ~5% - no better than the
risk-free trade, for real risk. The live trial's collateral usage answers this,
and it should be answered before a cent goes here.

Second: a sign error in the settlement semantics costs the credit on the
arbitrage, but here it means betting on entirely the wrong margins.

## 16. Frequency beats size: rank by turnover, not credit

Absolute credit is the wrong objective if the goal is compounding. What matters
is **return per day of locked capital**, because a pair ties up ~$1 a share
until its market settles and the account cannot redeploy until then.

```
  $/day   total  days  ret/day   violation
  0.600    0.15  0.25   6.25%    a same-day ladder
  0.400    0.10  0.12   4.17%    a 4q ladder, settles in hours
  0.192    0.96  5.00   0.83%    clmsn-cah  sell -1.5 buy +0.5
  0.025    0.15  6.00   0.26%    col-bayl   sell -0.5 buy +1.5
```

**A 0.015 credit settling tonight beats a 0.040 settling Thursday by 7x on a
daily basis** - and the bot ranked it last, because `rank_games` sorted by
historical hit count, which favours the ladder with the most standing
violations regardless of when it pays. `--rank turnover` (now the default)
sorts by soonest settlement instead. `--rank value` keeps the old behaviour for
a one-off sweep.

### The hard limit: every ladder is college football

`sports: cfb(50)` - every spread ladder on the venue is CFB. NFL has outright
markets only, no ladders. CFB plays Thursday to Saturday, so:

```
Thu 9/25   clmsn-cah, army-templ, howrd-rutger
Fri 9/26   most of the slate
Sat 10/3   next week
```

A Sunday trial with `--max-days 1` matched **0 ladders**, correctly. Capital
therefore turns over **1-2x per week, not daily** - not because the edge is
weak but because the underlying events only happen on certain days. The
turnover ranking still decides which ladder to take first within a slate, but
it cannot manufacture a daily cycle out of a weekly sport.

If daily compounding is the requirement, this strategy cannot deliver it alone.
What could: a venue listing ladders on daily sports (NBA, MLB, soccer), or the
cross-market check in section 14, which works on outright markets and so is
not restricted to CFB.

### The bug that mattered more

`game_date()` anchored its date pattern to the end of the slug. Sub-period
ladders put the date mid-slug (`...-2026-09-19-4q`), so it returned None and
`within_days()` rejected them. **`--max-days` was silently excluding the only
markets on the venue that settle in hours** - precisely the ones this objective
wants. Fixed, and `sub_period()` now recognises 1h/2h/1q-4q so those score
0.12 days instead of the 7-day fallback.

### Why exiting early does not solve this

The obvious way to recycle capital faster is to close a pair when the
mispricing corrects, rather than holding to settlement. The persistence data
says that will rarely fire: violations stood in 7 of 10 sweeps across hours,
so there is usually no correction to exit into. **The property that makes the
edge reliable is the same one that makes it slow.** Short-dated markets are the
lever, not early exit.

### What this implies about where to look

Sub-period ladders are the best structural fit for a compounding objective:
they settle at the end of a quarter or half, their books are thinner than
full-game (so more inconsistent), and `make families` counts them. They are
also small - a 4q ladder had 6 strikes - so expect fewer violations per ladder
and rely on breadth across the slate.

## 17. The full market inventory, and what it rules out

From `make crossmarket-report`, 1,200 markets:

```
asc-cfb-ladder        945      sub-period ladders:
aec-cfb-outright      197        1h 18   1q 18   2h 18
aec-nfl-outright       46        2q 18   3q 16   4q 12
cpoc-ussec-outright    12
```

**Ladders exist only for CFB.** NFL has 46 outright markets and zero ladders.
That matters because the cross-market check needs BOTH a ladder and an outright
for the same game - so cross-market is **also CFB-only**, and the hope that it
could run on a daily sport is dead. `cpoc-ussec` is 12 outrights with no
ladder, so nothing to pair against either.

**Daily compounding is not available on this venue for any strategy here.**
Everything with exploitable structure is college football, which plays Thursday
to Saturday. That is a property of what the venue lists, not of the edge.

### Cross-market is mostly efficient, and the one gap may not be independent

24 of 25 games priced their outright and their ladder's zero crossing within
the round trip. Only `clmsn-cah` disagreed: **0.030 with 197 shares, about
$5.91** - bigger than every monotonicity violation combined, because an
outright is the venue's most liquid book.

But `clmsn-cah` is the same ladder carrying 12 of 15 monotonicity violations.
The likely reading is that its **ladder is broken and the moneyline is
correct**, so this is not a second independent edge - it is another view of the
same defect. The trade is still valid (two markets settling identically, priced
apart, so the gap locks in), but **do not add $5.91 to the ladder's $4.62 as if
they were separate pools**: they draw on the same mispriced strikes and the same
capital.

### What is actually left worth building

**Sub-period ladders: 100 strikes across 1h/1q/2h/2q/3q/4q.** They settle at
the end of a quarter or half rather than the game, so on a single Saturday
capital can turn over several times - which is the closest thing to the stated
objective that this venue supports. `parse_strike` already handles them
(`...-2026-09-19-2h-neg-13pt5`) and `days_to_settle` scores them at 0.12 days,
so the bot covers them already. They have never been scanned during a live game
because every session so far has been on a non-game day.

## 18. CONFIRMED from the venue's own rules text

`make verify VSLUG=asc-cfb-clmsn-cah-2026-09-25-neg-0pt5` returns what should
have been read on day one:

```
question    Will the Clemson cover -0.5 vs the California...?
line        -0.5
marketType  spreads / football_team_full_game_spread
description "This market will settle to Yes if Clemson wins by more than 0.5 ...
             Overtime is included if played. If the game is delayed, postponed
             or suspended and not rescheduled within two weeks, the market will
             settle to the last fair market price."
marketSides price 0.5850  long=True   -0.50  team=Clemson
            price 0.4200  long=False  +0.50  team=California
```

**The sign convention is correct.** `neg-0pt5` pays iff `margin > 0.5`, which
is exactly "pays iff `margin > -L`". The ladder is written from one team
throughout. Sections 9, 15 and 16 stand as written.

`markets.list()` carries a plain-English `description` on every market. This
project inferred the convention from the shape of a price curve, published nine
fake arbitrages off an inverted sign, and only found the documentation after
six separate semantic errors. **Read `description` before modelling anything.**

### Two structural discoveries in the same payload

**1. Every market has two sides and the venue prints both.** `marketSides`
gives Clemson -0.5 at 0.5850 (long) and California +0.5 at 0.4200 (short),
summing to 1.0050. `book_levels()` reads one book, so half the liquidity on
every strike has been invisible - and a pair of sides quoted above 1.00 is the
YES+NO structure the graveyard buried on the GLOBAL venue and never tested
here.

**2. `neg-0pt5` and `pos-0pt5` are the identical contract.**

```
neg-0.5 pays iff margin >  0.5
pos-0.5 pays iff margin > -0.5
```

The only margin between them is 0, which football cannot produce because
overtime is played. Both settle on "Clemson wins". They were quoted 0.583 and
0.552, and the live ladder offered `sell -0.5 @ 0.580, buy +0.5 @ 0.555` for
**+0.025**. That is not "the higher line is easier so the ordering must hold" -
it is the same contract at two prices, confirmed from the rules text.

`identical_pair()` generalises it: strikes k1 < k2 settle identically whenever
no achievable margin lies in `(-k2, -k1]`. Those violations now print
`[SAME CONTRACT]` and are the highest-confidence trades on the board.

**Postponement clause:** settles to "the last fair market price", not 50-50 as
the moneylines do. For a monotonicity pair both legs mark at their last prices,
preserving the gap, so the credit survives. Worth knowing rather than assuming.

## 19. First live pair: it works, and the venue does not net

```
asc-cfb-clmsn-cah-2026-09-25-neg-0pt5   -2   $0.84
asc-cfb-clmsn-cah-2026-09-25-pos-0pt5    2   $1.11
marginRequirement 2
```

Both legs filled. It is the `[SAME CONTRACT]` pair from section 18 - the two
strikes that settle on the identical event - which is the highest-confidence
trade available.

The 0.42 on the short is **collateral, not the sale price**: `1 - 0.58 = 0.42`.
So the pair is sold at 0.580 and bought at 0.555:

```
credit   +0.025/share x 2 = +$0.05
capital   0.975/share x 2 =  $1.95
```

`capital_per_share()` predicted $1.95 and the venue's own `marginRequirement`
came back **$2**. The model is right, and:

**THE VENUE DOES NOT NET THE TWO LEGS.** ~$1 of margin per share-pair. This
was the last open question from section 15, and it settles it:

* The monotonicity arb returns **~2.5% per settlement** on locked capital -
  unchanged, since that was always costed at ~$1/share.
* **Key-number verticals are a ~5% trade, not a 470% one.** The premium is
  tiny but the short leg still ties up (1 - price), so the payoff shape is
  attractive while the return on capital is barely better than the risk-free
  version. Not worth the added risk at this size. Deprioritised.

### A display bug this exposed

The positions table divided `cost` by a negative `net` and printed
`avg -0.420` for the short, which reads as "sold at 0.42" and makes the trade
look inverted - the opposite of what happened. Shorts now print the implied
sale price and the collateral separately, and `marginRequirement` is surfaced
on the balance line since it is the field that answers the netting question.

## 20. Esports: no ladders anywhere, but the best directional edge lives there

"Why not esports, it runs 24/7" deserved a real answer rather than "not listed
here". Classifying all 1,922 esports markets in the cached tape:

```
match winner   1140     "LoL: Golden Lions vs 9z Globant - Game 2 Winner"
map winner      506     "Valorant: Sentinels vs 2GAME - Map 1 Winner"
totals          274
handicap/spread   0
```

**Zero handicap or spread markets.** The ladder arb cannot port to esports, and
not because a venue declined to list it: a best-of-three has no margin of
victory to lay strikes across. The monotonicity trade needs an ordered set of
strikes on a continuous quantity, and esports does not produce one.

### But esports is where the only significant directional edge is

`run_calibration.py --category Esports`, n=1032:

```
   0.15-0.30   147   0.235  0.122   -0.113  [-0.164,-0.059] *
   0.75-0.90   127   0.819  0.906   +0.086  [+0.030,+0.135] *
```

Favourites at 0.75-0.90 win ~8.6 points more often than priced, longshots at
0.15-0.30 eleven points less. That is the **only** statistically significant
directional finding in this entire project, it is esports rather than football
(section 3's headline was this result being misread as general), and an 8.6
point edge clears a 2c spread comfortably - unlike everything in section 8.

### And esports has structure football lacks

Map 1, Map 2, Map 3 and the match winner are all priced separately. If maps
were independent with per-map probability p, a Bo3 match win is `p^2(3-2p)`:

```
map 0.55 -> match 0.575      map 0.75 -> match 0.844
map 0.65 -> match 0.718      map 0.85 -> match 0.939
```

That is a model, not a logical identity, so it is a statistical edge rather
than arbitrage - map independence is roughly true but side selection and
momentum violate it. With 506 map markets priced alongside their match
markets, there is real surface area to test it on.

### The blocker is access, and it is not technical

That tape is Polymarket **global**. `requirements.txt` carries
`py-clob-client` with the comment "Not for US persons", and polymarket.us
exists precisely because of that restriction. Nothing here should be built to
work around it.

The legitimate question is whether a venue a US person can use lists esports.
Kalshi is CFTC-regulated and worth checking - if it lists esports match
markets, the calibration edge above is the thing to point at it, and it is
already measured rather than hypothetical.

## 21. What the public bots actually prove

Researched: `polymm`, `skharchikov`, `ImMike/polymarket-arbitrage`,
`BlackCandleLab`, `CarlosIbCu`, and the r/PredictionsMarkets thread.

**Only one has real numbers.** `polymm`, public wallet: arb leg **+$8,293**,
directional residual **-$3,184**, ~$5k net, dead of *"got too slow to defend
its edge"*. It rested limit orders priced off bookmaker odds and informed flow
picked them off - that residual IS adverse selection, 38% of gross.

Everything else is unevidenced. `skharchikov` runs 29 features, a five-model
stacking ensemble, LLM consensus and Bayesian anchoring, publishes **no
performance figures at all**, paper-trades only, and blocks sports as
unprofitable. `ImMike` reports "99.6% win rate, $573 profit" from a
**simulation mode that generates its own mispricings**. Several repos are
lead-generation shells pointing at a Telegram sales channel.

The thread's useful claims: prices correct in 50-100ms against a 250ms taker
delay, so nothing speed-based survives; *"the only ones on the leaderboards are
market making pair arbitrage bots"*; and negative skew kills late-entry - one
loss eats fifty wins.

**Three of those independently validate the ladder trade.** It IS a
market-making pair arbitrage. It needs no speed - violations stood 12 of 15
across ten sweeps over hours, because they are structural inconsistencies
nobody reconciles rather than an informational race. And a monotonicity pair
has no losing state, so the skew that killed everyone is structurally zero.

### Structures ruled out, with reasons

* **Cross-venue (Polymarket vs Kalshi)** - excluded: Polymarket only.
* **Bundle arb, YES+NO < $1** - impossible here. The venue quotes ONE book from
  both ends: long ask 0.585 and "short" 0.420 sum to 1.005, which is exactly
  `1 + spread`. Separately-traded legs are what make bundle arb possible and
  this venue has none.
* **Multi-outcome / negative-risk** - `run_multi.py` groups by the venue's own
  event metadata rather than a slug regex, because the earlier check could only
  see two-sided games and its "no group has more than one leg" was a statement
  about the regex.
* **Market making on spread** - `ImMike` wants a 5c spread; these ladders quote
  0.005. Nothing to capture.

## 22. The economics decide this, not the strategy

`run_economics.py`, from measured inputs only:

```
hosting $18/mo -> net $-109.30/yr     capital needed to cover it: IMPOSSIBLE
hosting $ 4/mo -> net $  58.70/yr
hosting $ 0/mo -> net $ 106.70/yr
```

At $18/month the system **cannot** pay for itself at any capital, because the
depth ceiling binds before capital does. Hosting is not overhead here, it is
the deciding term, and moving it is the only *certain* improvement available.

**It does not need a server.** The edge is slow, so a 20-minute daemon was
paying for a persistent machine to rediscover the same opportunities 72 times a
day. `cron_cycle.sh` runs four times daily on a free tier.

Two throughput wins came out of that switch:

* **Depth over frequency.** 4 runs x 25 games x 24 strikes is 2,400 calls/day
  against the daemon's 10,368 - **77% fewer** - while covering **4.2x** more
  board per sweep. Every violation ever found sat within ~6 points of the
  money, which is precisely what `--near 12` scanned. The wings are unexamined.
* **Adaptive pacing.** Sweeps ran 3.0s/call against a 0.6s target because two
  backoff layers compounded. `src/pm_us/throttle.py` is AIMD - success creeps
  the interval down 3%, a 429 backs it off 80% - and converges to 5.8 req/s
  against a simulated 8 req/s ceiling, 3.5x the fixed pause.

## 23. FEES MAY KILL THE WHOLE STRATEGY

The single most important finding of the research phase.

`charanchivukula/polymarket-trading-infra` publishes Polymarket's fee curve as
**`0.07 * p * (1-p)` per share**, and reports live results of **-$135 over 46
trades, "mostly fees"**, against a backtest that modelled none and expected
+$120. Applied to a two-leg ladder pair:

```
 leg px   fee/leg   2 legs   credit       NET
  0.580    0.0171   0.0341    0.040   +0.0059
  0.605    0.0167   0.0335    0.035   +0.0015
  0.590    0.0169   0.0339    0.025   -0.0089
  0.550    0.0173   0.0347    0.010   -0.0246
```

**Almost every pair we have found goes negative.** The best violation ever
observed, 0.040, nets +0.006. The typical one loses money. `run_economics
--fee-rate 0.07` prints "FEES EXCEED THE CREDIT" and a $0.00 year.

Our own fills are consistent with a charge of roughly this size. Selling 7
clmsn-cah at 0.550 against a 0.462 average should have realised $0.616 and
realised **$0.440** — a gap of **0.0251 a share** against the model's 0.0173.
Same order of magnitude, far too close to dismiss, though FIFO accounting could
distort the arithmetic.

**The definitive test is already running.** A correct monotonicity pair
realises *exactly* the credit, because the legs cancel. So when the $2
`clmsn-cah` pair settles on Thursday:

* realises **+$0.05** -> no material fee, the strategy stands
* realises **~$0.00 or negative** -> fees eat it, and the ladder trade is dead
  at these credit sizes regardless of hosting, capital or frequency

`run_fees.py` probes for a fills endpoint to read the fee field directly rather
than inferring it. Guessing at this venue's semantics has been wrong six times
in this project (`assetNotional`, `balanceReservation`, `category`,
`buyingPower`, the ladder sign, the UTC date window); this one decides whether
anything should trade at all.

**Nothing should be sized up until Thursday settles.**

### If fees are real, what survives

Only credits above ~0.035, which were 2 of 15 observed violations. The
implication is not "stop" but "raise the gate": `--hurdle` must then be set
from `2*fee + target`, not from the target alone, and the opportunity count
drops by roughly an order of magnitude.

## 24. What the honest negative results teach

`charanchivukula` is the most methodologically serious repo found, precisely
because it published a failure:

* **~100 configurations across five timeframes; exactly one survived.** A
  log-normal fair-value model on 1h markets: +7.08c/contract, **t = 7.49**,
  2,468 trades, profitable in all six data months. Live, it **earned zero**.
  Cause: "overfitting + in-sample selection" — the threshold, debounce, sizing
  and gates were all chosen on the same six months that produced the t-stat.
  "A t-stat of 7.49 on the selection set is not out-of-sample evidence."
* **The 5m/15m edge was a non-capturable stale-quote latency artifact** — it
  vanished once realistic entry delay was added. "If the edge disappears when
  you add realistic latency, it was never yours."
* **Calibration was the cleanest diagnostic.** Actual win rates tracked the ASK
  in every bucket while the model over-predicted by ~10 points. Exactly the
  test `run_calibration.py` runs here.
* **Capacity, not capital, binds.** Median visible top-of-book **~$59**. Which
  matches our own finding that the $50->$100 marginal return halves.
* Their executor re-validates every ticket before placing: edge against the
  **ask** not the mid, ticket age <= 90s, >= 3 min to close, size capped at
  `min(0.25*Kelly*bankroll, 10% bankroll, visible ask)`.

That last one is a gap here: `ladder_bot` reads a book, then places, with the
whole sweep in between. With 24 strikes a quote can be a minute stale by the
time the pair goes out.

### Why this project's structure is more defensible than theirs

Their edge was a *model* of fair value, fitted on the same data that measured
it. The monotonicity trade is not fitted to anything — `P(L2) >= P(L1)` is
forced by the payoff definitions, so there is no parameter to overfit and no
in-sample selection to survive. That is the one respect in which this is on
firmer ground than a t-stat of 7.49.

Fees, however, apply to logic and models alike.

## 6. Rules

* Anything new goes through the sealed holdout in `research/holdout.yaml`
  before it gets capital. That discipline is why section 1 is trustworthy.
* An edge smaller than the spread is not an edge unless you are the maker.
* Write the kill criterion **before** running the experiment.
