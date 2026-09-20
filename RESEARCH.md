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

## 5. Settled: the rewards programme is not the business

Two days of live quoting produced **$0.07 gross, $0.04 credited**, while
carrying **$34.79** of one-sided directional inventory. Reward share is
size-over-book, and Target Size runs to hundreds or thousands of shares, so a
$44 account is a rounding error in every book it quotes. Rewards are a
subsidy on a position you wanted anyway, not a revenue line.

This is why the bot now exits inventory instead of hoarding it
(`--buy-only` means "never short", not "never sell") and caps cost basis per
market (`MAX_INV`).

## 6. Rules

* Anything new goes through the sealed holdout in `research/holdout.yaml`
  before it gets capital. That discipline is why section 1 is trustworthy.
* An edge smaller than the spread is not an edge unless you are the maker.
* Write the kill criterion **before** running the experiment.
