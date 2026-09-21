# Handoff

You are taking over a live prediction-market trading bot on Polymarket US.
Everything below is either MEASURED (stated with its evidence) or ASSUMED
(labelled). Do not treat the assumed parts as facts — six of them turned out
wrong already, and each one is documented so you don't rediscover them.

## Environment

```
box      DigitalOcean droplet, ~/polymarket-trader, python in .venv
creds    /etc/pm-us.env  (POLYMARKET_US_KEY_ID / _SECRET_KEY)
venue    Polymarket US only — CFTC-regulated. The global venue is
         geoblocked for US persons; do not build toward it.
account  ~$17.62 buying power, ALL promotional credit.
         availableToWithdraw = $0.00. Nothing is withdrawable yet.
docs     make man / make live / make deploy / RESEARCH.md (the evidence log)
```

## THE strategy, and why it works

Spread ladders (`asc-cfb-<game>-<pos|neg>-<N>pt<M>`) are a set of contracts on
one game's margin of victory. A contract at signed line L pays iff
`margin > -L`. **Confirmed from the venue's own rules text** via
`make rules VSLUG=<slug>` — the description field says so in plain English.
Read it; do not infer semantics from price shape (I did, and shipped nine fake
arbitrages).

Because a higher line is strictly easier to cover, `P(L2) >= P(L1)` for
`L1 < L2`. Long L2 / short L1 therefore pays `$1` iff the margin lands between
them, and `$0` otherwise — a **vertical**. Its entry cost decides the risk:

* **entry < 0** — the arbitrage. Paid to hold a `{0,+$1}` payoff. Cannot lose.
* **entry > 0** — a bet. Risk the premium, win $1 at `P(margin lands between)`.

`src/pm_us/vertical.py` scans every pair `l1 < l2` and returns both kinds
ranked. Both are the same code path.

## THE most important fact: fees decide execution

`docs.polymarket.us/fees`, effective 2026-09-17:

```
Fee = theta * C * p * (1-p)
  taker  theta = +0.0695   charged
  maker  theta = -0.0125   REBATE, paid to you
```

`src/pm_us/fees.py` reproduces all five of the docs' worked examples exactly.

At a typical leg price of 0.58, a two-leg pair costs **0.0341/share to take**
and **earns 0.0061/share to rest**. That 0.040 swing is larger than the largest
violation ever observed here. Taking makes 13 of 15 observed violations
negative. Resting makes all 15 positive.

**So the bot rests both legs (post-only) and never crosses.** Resting is safe
here because the edge is slow — violations stood in 12 of 15 observations
across ten sweeps over hours — and a resting *pair* is hedged against level
moves by construction, both legs being on the same game.

## THE biggest open question: $8,500 pools paying $0.00

`make hunt` finds **active liquidity reward programs on the same ladder
markets this account already trades** — `asc-cfb-col-bayl` and `clmsn-cah`,
pool **$8,500** each, target 25,000 shares. NFL games carry $32,000 pools.

Four rewards have been earned gross: **$0.08, credited $0.0000, every one
`SKIPPED`.** Nobody has found out why.

I previously dismissed the high share estimates as a ghost — the book is empty
near the touch because the game is days away. **That was about the share, not
the pool.** The pools are real, and it survives a hostile book:

| competing book | my share of $8,500 |
|---|---:|
| empty near touch (what was observed) | **$139.56** |
| half the target resting 20 ticks out | $22.51 |
| the full 25,000 target resting *at* the touch | $6.79 |

20 shares is ~$10 of capital. **The worst row still beats a year of the
arbitrage.** This is the largest number in the project by an order of
magnitude and it is one API call from being known.

```
make rewards        # dumps every field the venue returns, flags suspects
```

The bot reads `rewardPool`, `discountFactor`, `targetSize` and nothing else.
Any qualification rule the venue publishes fails us silently.

**Leading hypothesis, strong:** the reward-farming phase ran `--buy-only`,
which quotes **one side**. Liquidity programs generally require **two-sided**
quotes. If that is the rule, every skip was correct and the fix is a flag.

Other candidates the dump will settle: a minimum resting size, a maximum
spread from the touch, a minimum time-in-book (orders cancelled each cycle
never mature), or an explicit reason field on the earnings row.

## Open question 2: do maker orders actually rest?

**Do post-only orders actually rest on this venue, or are they silently
converted to takers?** Everything above depends on it and it has never been
observed. Run:

```
make ladder-kill; rm -f research/ladder_bot.lock
GAMES=3 VERTICALS=1 CAP=5 ./cron_cycle.sh
make money
```

* `OPEN ORDERS` populated → maker path works, economics hold.
* Positions appear with no open orders → converted to taker. The economics
  invert and the strategy needs re-gating at `credit > 0.036`.

## Second open question: Thursday

An existing pair is live: short `asc-cfb-clmsn-cah-2026-09-25-neg-0pt5`, long
`...-pos-0pt5`, 2 shares, $2.00 margin held. It was placed as a **take** before
the fee schedule was known. It settles Thu 2026-09-25.

A correct pair realises **exactly the credit** because the legs cancel. So:
* realises +$0.05 → the documented taker fee is not being charged
* realises ~$0 or less → it is, and that difference is the fee, measured

## Economics — read before optimising anything

`make economics HOSTING=<n> BANKROLL=<n>`

```
hosting $18/mo -> net -$109/yr    capital needed to cover it: IMPOSSIBLE
hosting $ 4/mo -> net  +$59/yr
hosting $ 0/mo -> net +$107/yr
```

**Hosting is the deciding term, not the strategy.** At $18/month this loses
money at ANY capital because depth caps the edge before capital does. The bot
does not need a server — the edge is slow, so `cron_cycle.sh` four times a day
on Oracle's always-free tier is equivalent. Moving hosting is the only
*certain* improvement available and it is worth more than any optimisation.

Modelled with maker execution and breadth: linear at ~78%/yr to about $1,000 of
capital, then saturating. **The fill rate in that model is a guess** and
everything scales off it linearly. Treat it as a shape, not a forecast.

## What scales, and what does not

`walk_depth()` in `vertical.py` proves it: a representative book holds 490
shares across all levels versus 20 at the touch — but net of taker fees **only
the top level survives**. That is the entire source of the capacity ceiling.

Resting has no depth limit, so the scaling lever is **breadth**:
`--max-games 0` scans all 50 ladders, and `src/pm_us/allocate.py` spreads a
base size across every qualifying candidate before giving surplus to the best.
Greedy allocation piles into three positions and stops scaling.

## Failure modes already hit — do not repeat these

1. **`all_programs()` is not the venue.** It returns only markets with an
   active reward programme. It hid 500 markets, all of NBA/NHL/CBB, and a
   ladder the bot was already trading. Use `markets()` with paging too.
2. **Semantics were wrong six times** — `assetNotional`, `balanceReservation`,
   `category`, `buyingPower`, the ladder sign, the UTC date window. Every time,
   the fix was reading the venue's own output instead of inferring. `make rules`
   and the `other balance fields:` dump exist for this.
3. **ESPN timestamps in UTC**, so a US evening kickoff is the next day. An
   exact date-prefix match silently drops every prime-time game.
4. **Unbounded JSONL reads OOM'd the droplet.** Use `src/pm_us/jsonlog.py`
   (`tail_records`, `iter_records`, rotating `append`). Never slurp a log.
5. **`ulimit -v` is address space, not RSS.** A 700MB cap killed python on
   numpy import, before it could log. It is now verified before being trusted.
6. **`scan()` once only compared adjacent strikes** and found $0.00 while the
   ladder carried seven violations. Most violations span several strikes.
7. **The old MM service (`pm-us-live`) calls `cancel_all()` on startup.** With
   maker-first execution the resting orders ARE the position. `cron_cycle.sh`
   disables it every cycle. Keep that.

## Dead ends — do not spend time here

* **Esports**: zero markets on this venue (1,700 checked, word-boundary
  filter). Esports has no spread markets ANYWHERE, so the ladder trade cannot
  port. A full esports stack exists in `src/esports/` and is correct and idle.
* **Reward farming**: $0.00 credited against $34 of inventory.
* **Bundle / YES+NO arb**: one book quoted from both ends here, so the sum is
  `1 + spread` by construction and can never be under 1.
* **Multi-outcome bundles**: 743 game groups, none with more than one leg.
* **Latency**: ESPN publishes plays 34–67s late; prices correct in 50–100ms
  against a 250ms taker delay. Nothing speed-based survives.
* **In-play momentum/reversion**: tested on 128,517 observations. The apparent
  edge was a `[0,1]` boundary artifact; nothing survives a 2c round trip.
* **Market making on spread**: needs 5c, these books quote 0.005.

## What is worth trying next, in order

0. **`make rewards`.** One call. Decides whether the reward line is
   four figures or zero. Everything below is refinement on $107/yr.

1. **Answer the post-only question.** Nothing else matters until then.
2. **Move hosting to $0–4/mo.** Certain, larger than any optimisation.
3. **Measure the maker fill rate.** `reconcile()` logs every pair as filled,
   half-filled or expired; after a day `make found` turns the modelled 35% into
   a number.
4. **Sub-period ladders** (`-1h`, `-4q`, 100 strikes) settle intra-game and are
   the only intra-day capital turnover on this venue. Never scanned during a
   live Saturday. `--min-strikes 2` now lets them through.
5. **NBA, ~4 weeks.** 222 markets, nightly, no ladders *yet*.
   `make families-watch` on the daily cron flags a new ladder family the day it
   appears. That is the single event that changes the economics — weekly
   becomes nightly.
6. **`run_bookline.py`** prices a whole ladder from one DraftKings line (ESPN
   serves it free): spread gives the mean, the de-vigged moneyline pins the SD
   via `sigma = mu/Phi^-1(p)`. Built, ~25% game coverage, never run live.

## You can see the box — things I could only guess at

Previous agents worked blind through `make` targets. You can inspect
processes, so settle these directly; each one cost real time to diagnose
indirectly.

```
ps aux | grep -E 'ladder_bot|mm_bot'      # is anything actually running
cat research/ladder_bot.lock              # stale lock has blocked 3+ cycles
ls -la research/*.jsonl                   # these OOM'd the droplet once
systemctl is-enabled pm-us-live pm-us-paper   # must be disabled: they cancel_all on boot
tail -50 cron.log                         # sweep rc + explanation is logged
free -m                                   # 3000MB ulimit was verified, but verify again
```

**A lock with no matching pid is the single most common failure.**
`LOCK_MAX_AGE=2100` should kill it; confirm that logic actually fires rather
than assuming it does.

**Verify every change against live output.** Most of this project's lost time
was correct analysis on broken plumbing: two patches silently failed to apply
and were committed as done, a memory guard was tested on macOS where it is a
no-op and shipped, and a scan regression returned $0.00 across 50 ladders
while a known-good ladder carried 7 standing violations.

## How to work

Verify against live output before believing an edit. Most of this project's
lost time came from shipping analysis that was right and plumbing that was not.
`make money`, `make ps`, `make found` and `cron.log` are the ground truth.

Use `GAMES=3` for interactive tests (under a minute). `GAMES=0` scans all 50
ladders and takes 15–25 minutes — correct for cron, painful to watch.

`RESEARCH.md` is the evidence log; add to it before adding a strategy.
