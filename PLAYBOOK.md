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

The settlement convention is **CONFIRMED** from the venue's own rules text
(RESEARCH.md S18): a strike at line L pays iff `margin > -L`. Read a market's
`description` field before modelling anything — it states the rule in plain
English, and inferring it instead cost this project six semantic errors.

Still unknown: whether the venue **nets** the two legs of a pair. That decides
whether key-number verticals return ~5% or ~470%, and `make ladder-trial`
answers it by how much buying power a pair consumes.

## The short set — eight commands

    make money      cash, positions, orders. TRADEABLE = buying power.
    make find       hunt for mispricings in the background. Places nothing.
    make found      what the hunt found, and whether it persists
    make trade      place ONE bounded live trade ($2, 3 ladders, ~30s)
    make out        cancel orders + exit positions. DRY RUN.
    make out-live   same, for real
    make quiet      stop every bot AND disable it (survives reboot)
    make rules VSLUG=<slug>   what a market actually settles on

Normal loop: `make find`, wait an hour, `make found`, then `make trade` if
something stands. `make money` any time. `make quiet` when done.

`make trade` takes ~30 seconds — no tmux needed. **Do not Ctrl-C it**: the two
legs of a pair go out back to back, and killing it in between leaves one leg
naked. If you do interrupt it, run `make found` and look for an `attempt` with
no matching `paired`, then `make money` to check for an unmatched position.

tmux is worth it for the long ones (`make find`, `crossmarket-bg`) — though
those already run detached. `Ctrl-b d` detaches, `tmux attach` returns.

After settlement a correct pair realises **the credit**, or **the credit plus
~$1/share**. It cannot realise a loss. If it does, stop everything.

## The longer list

Hunting, in more detail:

    make ladder-bg / ladder-report / ladder-kill    the scanner behind `find`
    make ladder-scan        one sweep, totals the lockable dollars
    make crossmarket-bg     outright vs the ladder's zero crossing
    make crossmarket-report
    make families           every market family the venue lists
    make hunt               the old reward-program scanner

Exiting, in more detail:

    make flatten            dry run: rest sells at the ask
    make flatten-cross      dry run: cross only where the spread is tight
    make flatten-cross-live
    make cancel             pull every resting order

`flatten` is idempotent — it cancels its own prior orders first.

Research, no venue needed:

    make scores         cache ESPN finals (one-off)
    make keynumbers     how lumpy football margins are
    make calibrate      price vs realised win rate on the cached tape
    make ladder-test    self-test: asserts the sign convention
    make keyvertical GAME=<base>   exact-margin bets, ~100:1 payoff

## The old MM bot is still enabled

`pm-us-live.service` runs the reward-farming strategy — the one that cost ~$16
and earned $0.00 credited. `make stop` stops it but leaves it **enabled**, so a
reboot brings it back, and the installed unit is stale: no `--max-inventory`
and no `--min-price`, so it accumulates longshots without a cap using the same
buying power the ladder pairs need.

    make quiet      # stops AND disables both services

Only `make run` re-enables it, and that regenerates the unit with the current
flags. Do not run it unless you mean to farm rewards again.

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
