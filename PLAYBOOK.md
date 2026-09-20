# Playbook — what to run, in what order, and why

`make man` prints this. `make help` lists every target flat; this explains the
sequence. RESEARCH.md holds the evidence behind each claim.

## Where things stand

| | status |
|---|---|
| Reward farming | **dead.** $0.00 credited against $34 of inventory. |
| Static arbitrage | **dead.** 12 graveyard proposals, no violations on holdout. |
| In-play swing trading | **dead.** Edge is smaller than the 2c round trip. |
| Sports latency | **dead.** ESPN publishes plays 34-67s late. |
| Crypto binaries | **not listed** on this venue. |
| Ladder monotonicity arb | **WORKING.** ~$5 standing, risk-free, shorting proven. |
| Key-number verticals | built, ~100:1 payoff, blocked on the margining question. |
| Cross-market | built, 24/25 games efficient; the 1 gap is the same broken ladder. |
| Sub-period ladders | **untested.** 100 strikes, settle intra-game. |

One assumption still unverified: that `pos-5pt` means the team **receives** 5
points. Derived from price shape, not documentation. Everything above rests on
it. `make ladder-trial` plus Thursday's settlement is what confirms it.

## Phase 1 — check state (any time, no risk)

    make account        # TRADEABLE = buying power. nothing is withdrawable.
    make ladder-report  # do violations persist across sweeps?
    make crossmarket-report

## Phase 2 — hunt (no orders, no capital)

    make ladder-bg      # detached scanner, survives logout. ~15min/sweep.
    make ladder-report  # check back in an hour
    make ladder-kill

    make crossmarket-bg # outright vs ladder zero crossing
    make families       # what the venue lists at all

Research that needs no venue at all:

    make scores         # cache ESPN finals (one-off)
    make keynumbers     # how lumpy football margins are
    make calibrate      # price vs realised win rate on the cached tape
    make ladder-test    # self-test: asserts the sign convention

## Phase 3 — trade (REAL orders; run inside tmux)

    tmux new -s trial
    make ladder-trial   # $2, one sweep, 3 games. ~30s.
    make account        # matched pairs? how much buying power went?

Then wait for settlement. A correct pair realises **the credit**, or **the
credit plus ~$1/share**. It cannot realise a loss. If it does, the sign
convention is inverted — stop everything.

## Phase 4 — unwind

    make flatten        # dry run: shows bid/ask and the cost of crossing
    make flatten-cross  # dry run: crosses only where the spread is tight
    make flatten-cross-live
    make cancel         # pull every resting order

`flatten` is idempotent — it cancels its own prior orders first.

## Timing

CFB plays **Thursday to Saturday**, and every ladder is CFB. So this is a
weekend strategy; capital turns over 1-2x per week. Sub-period ladders
(1h/1q/2h/2q/3q/4q) settle mid-game and are the only route to intra-day
turnover — scan them during a live Saturday slate.

## Knobs

| var | default | meaning |
|---|---|---|
| `CAP` | 5 | dollars the ladder bot may deploy |
| `TRIAL_CAP` | 2 | dollars for the live trial |
| `NEAR` | 12 | strikes nearest a pick'em to scan |
| `GAMES` | 25 | ladders per sweep |
| `CYCLE` | 20 | minutes between sweeps |
| `RANK` | turnover | `turnover` (return/day) or `value` (biggest credit) |
| `MIN_CREDIT` | 0.01 | skip violations thinner than this |
| `MAX_INV` | 10 | old MM bot: cost basis per market before it stops bidding |
| `MIN_PX`/`MAX_PX` | 0.60/0.90 | old MM bot: price band it will buy in |

## Hard-won gotchas

* One bot at a time. Two share the rate limit and halve each other.
* A dry run lifts `--max-capital` and `--max-size`, or its depth figures are
  just those defaults echoed back.
* Recurring violations are a **stock**, not a per-sweep flow. You take each once.
* Each share of a pair ties up ~$1 until settlement. Capital is the constraint.
* `buyingPower` is the tradeable figure. `cash - reserved` is meaningless here.
* Foreground commands die with the session. Use tmux for anything live.
