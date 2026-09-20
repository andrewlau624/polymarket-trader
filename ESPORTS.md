# Esports strategy

CS2, Valorant, League of Legends. Runs continuously — esports plays around the
clock, so unlike the football ladder there is no schedule to wait for.

Three layers, in strict priority. Capital goes to the surest thing first.

---

## 1. No-arbitrage LP — risk-free, model-free

A best-of-3 has exactly six possible outcomes: `AA BB ABA ABB BAA BAB`. Every
market on that series is an **indicator over those six**:

```
map 2 winner = A   ->  {AA, BAA, BAB}
match winner = A   ->  {AA, ABA, BAA}
total > 2.5 maps   ->  {ABA, ABB, BAA, BAB}
```

So an event is one linear system, and "are these prices coherent?" is "does any
probability vector reproduce them?" When none does, there is an arbitrage. The
LP returns the portfolio rather than leaving it to be spotted by eye:

```
maximise  t
s.t. for every outcome w:
       sum_i u_i (payoff_iw - ask_i) + sum_i v_i (bid_i - payoff_iw)  >=  t
     0 <= u_i <= depth_i     (bought at the ask)
     0 <= v_i <= depth_i     (sold at the bid)
```

`t` is the profit guaranteed in the **worst** outcome, at executable quotes,
capped at real depth. Not a mid-price illusion — that lesson was paid for in
the football work, where mid-based checking produced nine fake arbitrages.

**The case that recurs.** Once map 1 is decided, the series reaches a third map
**iff the map-1 loser takes map 2**. So `P(over 2.5)` and `P(map-1 winner takes
map 2)` are complementary — exactly one happens — and their prices must sum to
1. On the cached tape that broke in **12 of 106 events by 0.06–0.115**. One
real observation: `over 0.720 + map2 0.710 = 1.430`, so selling both locks
0.430 per unit with no losing state.

This is precisely the moment the swings create it: a map ends, four markets
reprice at once, and thin books do not stay consistent through that.

Generalises to Bo5 (20 outcomes) and to any number of legs, and it finds
combinations nobody thought to look for.

---

## 2. Statistical edge — measured, not assumed

From 514 resolved esports markets (one leg each, sampled before the final 30%
of each tape):

```
BUY  priced 0.75-0.90   n= 70   +0.107   CI [+0.038,+0.164] *
SELL priced 0.15-0.60   n=324   +0.070   CI [+0.019,+0.119] *
```

Split-half on the favourites: **+0.096 / +0.117** — stable. LoL alone: +0.109,
CI [+0.018,+0.178]. Both clear a 2c spread, which is what killed every football
signal.

An earlier version of this number was wrong twice: it was quoted as a general
result when it is esports-specific, and it used BOTH legs of each market
because esports labels legs with team names so the `"Yes" in outcomes` check
fell through — halving the effective sample and narrowing every CI. Corrected
here.

**Why it exists** — see layer 3. The race model shows trailing teams are priced
as though they win an implausible share of remaining rounds.

---

## 3. Model divergence — the mechanism

Two games, two different mathematical objects. Using the wrong one is how a
model ends up sophisticated and badly calibrated.

**CS2 / Valorant are a race to 13**, so the state space is finite and the
answer is *exact* — a Markov chain on (rounds_a, rounds_b), with a
gambler's-ruin tail at 12-12:

```
W(a,b) = p*W(a+1,b) + (1-p)*W(a,b+1)      W = q^2/(q^2+(1-q)^2) at deuce
```

**LoL has no rounds.** It ends when a nexus falls, and the state variable is
gold difference, which behaves like a diffusion — so it is a digital option:

```
P(win) = Phi( (goldDiff + mu*tau) / (sigma*sqrt(tau)) )
```

That gets the essential behaviour free: `+5000` gold is 0.747 at minute 10 and
0.986 at minute 30, because tau shrinks. Objectives are priced as
gold-equivalent so one state variable suffices — a baron is ~3000, which is the
discrete jump that moves a market.

**The sharpest diagnostic is the inversion.** Given a market price, what
per-round edge does it imply?

```
CS2 3-9 down priced at 0.25  ->  implies winning 63% of remaining rounds
CS2 3-9 down priced at 0.10  ->  implies 55%
```

63% for a team that is visibly losing is not credible. That is the
favourite-longshot bias of layer 2, with a mechanism attached.

### Live data — tested, not assumed

```
LoL   WORKS.  esports-api lists live matches + gameIds; feed.lolesports.com
              serves totalGold / towers / barons / dragons / inhibitors per
              side, updating in game. The whole state vector, free, no account.
Dota  WORKS.  api.opendota.com/api/live, unauthenticated.
CS2   NONE.   HLTV blocks scrapers, official endpoints are partner-gated.
Val   NONE.   Riot publishes no live esports feed.
```

So layer 3 runs on LoL today. CS2 and Valorant have an exact race model ready
and no score source — that is a data problem, not a modelling one, and it is
worth saying plainly rather than shipping a model with nothing to feed it.

Verified against a live match (`FUE vs EDGY`, WSCI) while writing this: the
feed returned `in_game` with a live frame, and the state fed straight into
`lol_win_prob`.

---

## Risk

Kelly is growth-optimal for a **known** edge. Ours comes from 70 observations,
and the measured +0.107 at 0.82 implies staking **59% of bankroll on one
match**. So: quarter Kelly on the CI's *low* end, never the point estimate —
which turns 59% into 5.3% — then three caps that bind before it does.

| cap | default | why |
|---|---|---|
| per position | 5% | no single leg dominates |
| **per event** | **10%** | map1, map2, match and totals on one series are *the same bet wearing four hats* |
| total deployed | 60% | keeps powder dry |
| drawdown | 20% | **halts**, does not shrink — a broken model does not self-correct |

Arbitrage legs skip Kelly entirely: an LP-verified portfolio has no losing
state, so capital and depth are the only limits.

---

## Running it

```
make es-discover     # what esports the venue actually lists
make es-dry          # continuous, places nothing
make es-live         # real orders, BANKROLL=<n>
make es-report       # what it found
make bo3             # re-run the tape study
```

## The open question

Everything above is built and tested. What is **not** confirmed is that
Polymarket US lists esports at all — the family inventory showed only CFB and
NFL, but that came from the reward-program endpoint, which omits markets with
no incentive attached. `make es-discover` is the check, and it prints what it
sees rather than assuming.

If the venue has no esports, layers 1 and 2 are correct and unusable here, and
the honest move is a venue that lists it rather than a worse strategy on this
one.
