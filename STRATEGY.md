# Where the significant money is

The arbitrage works and pays about **$107/yr at $100**. That is real and it is
also small. Everything below is ranked by how much bigger it could be, with the
evidence for and against each, and what would settle it.

The ranking is by expected value, not by confidence. #1 is the least certain
and by far the largest.

---

## 1. Liquidity rewards — pools of $8,500 per game, earned $0.00

`make hunt` found active reward programs on the **same ladder markets this
account already trades**:

| market | pool | target | est/day |
|---|---:|---:|---:|
| `asc-cfb-col-bayl-pos-3pt5` | $8,500 | 25,000 | $8,288 |
| `asc-cfb-clmsn-cah-pos-5pt` | $8,500 | 25,000 | $7,821 |
| `aec-nfl-car-cle` | $32,000 | 150,000 | $405 |

Four rewards have been earned gross — **$0.08, credited $0.0000, every one
`SKIPPED`.**

I previously wrote the 0.9+ share estimates off as a *ghost*: the book is empty
near the touch because the game is days away, and real makers arrive at
kickoff. **That reasoning was about the share. It was never about the pool.**
The pools are real, and the arithmetic survives contact with a hostile book:

| competing book | my share of $8,500 |
|---|---:|
| empty near touch (what was observed) | **$139.56** |
| half the target resting 20 ticks out | $22.51 |
| the full 25,000 target resting *at* the touch | $6.79 |

20 shares is about $10 of capital. **Even the worst row pays more in one
period than the arbitrage pays in a year.**

**The blocker is a single unknown: why `SKIPPED`.** The bot reads
`rewardPool`, `discountFactor`, `targetSize` and nothing else. If the venue
publishes a qualification rule, we have never read it and would fail it
silently.

**Leading hypothesis, and it is a strong one:** the reward-farming phase ran
with `--buy-only`, which quotes **one side**. Liquidity programs almost
universally require **two-sided** quotes. If that is the rule, every reward was
correctly skipped and the fix is a flag.

```
make rewards        # dumps every field the venue returns, flags the suspects
```

Do this first. It is one API call and it gates a four-figure annual number.

---

## 2. Quote the whole ladder, not two legs of it

The maker **rebate is paid on every fill regardless of edge** — 0.0125·p(1−p),
about 0.0031/share at the money. Picking off individual violations caps at the
depth sitting at the touch. Quoting all 24 strikes across 50 ladders does not:

```
24 strikes x 50 ladders x 5% fill x 5 shares = 300 shares/day
  rebate  $0.94/day
  spread  $0.75/day   (when round-tripped)
          ~$263/yr on CFB days alone, before any edge
```

That is 2.5x the arbitrage from the rebate alone — **and it is the same
quoting activity that earns #1.** They are one strategy, not two.

**What makes it safe is new:** `src/pm_us/greeks.py`. Every strike on a ladder
is a function of the same underlying — the margin of victory — so a book of a
hundred quotes is *one* exposure, not a hundred:

```
short 5 @ -1.5, long 5 @ +0.5   -> net delta -0.0022   (a pair is flat by construction)
short 5 @ -1.5 alone            -> net delta -0.1204   ($0.12 per point of margin)
```

And a naked leg does not have to be unwound where it was filled — `best_hedge`
offsets it at whichever strike is cheapest, which is usually not that one:

```
cheapest offset: +5.1 shares at -6.5  (spread 0.005, cost $0.013)
```

That is what lets a $17 account run a hundred quotes instead of two pairs.

---

## 3. Sub-period ladders — 4x the return per day of capital

Return per day of capital is what matters, and it is not the edge:

| ladder | edge | settles in | per day |
|---|---:|---:|---:|
| full game | 0.050 | 5.0d | 0.0100 |
| 1st half | 0.030 | 0.3d | 0.1000 |
| 4th quarter | 0.020 | 0.1d | **0.2000** |

The 4th-quarter ladder carries the *smallest* edge and is worth **4x** the
full-game ladder. The code already handles them — `--hurdle` is a return-per-day
gate precisely for this. They have **never been scanned**, because they only
exist while a game is being played. This costs nothing but a Saturday.

---

## 4. Quote wide, days early

The ladder books are empty near the touch days before kickoff because nobody
wants inventory that long. **That emptiness is the reward share.** Quoting both
sides wide is not a directional bet: you are never picked off on both, and on a
ladder the fill you do get is hedgeable at another strike. It earns for
*presence*, not for risk — if, and only if, #1 says presence is what pays.

---

## Order of operations

1. `make rewards` — one call, settles whether #1 is a four-figure line or zero
2. Confirm post-only orders actually rest (`HANDOFF.md` leads with this; it
   gates #2 as well)
3. Scan sub-period ladders on a live Saturday — free, code already written
4. Only then widen the quoting book, using `greeks.py` to keep net delta near zero

**The honest summary:** #3 is certain and small. #2 is likely and moderate.
**#1 is uncertain and larger than everything else combined**, and it is one API
call away from being known.
