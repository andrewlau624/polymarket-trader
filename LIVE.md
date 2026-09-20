# Going live

## The number that decides everything

```
hosting $18/mo -> net $-109.30/yr     <- loses money at ANY capital
hosting $ 6/mo -> net $  34.70/yr
hosting $ 4/mo -> net $  58.70/yr
hosting $ 0/mo -> net $ 106.70/yr
```

`make economics` reprints this from measured inputs. At $18/month the system
cannot pay for itself — "capital needed to cover $18/mo: IMPOSSIBLE", because
the depth ceiling binds before capital does. **Move the hosting first; it is
the only certain improvement available.**

Oracle Cloud Always Free runs this indefinitely at $0. A $4 Hetzner or your own
laptop also work. The bot does not need a server — see DEPLOY.md.

## What actually trades

One strategy is proven, executing, and risk-free: **spread-ladder monotonicity
pairs.** For lines L1 < L2 the higher line is strictly easier to cover, so
P(L2) >= P(L1). When `bid(L1) > ask(L2)` you sell the harder leg, buy the
easier one, and no outcome loses. Confirmed against the venue's own rules text,
executed live, both legs filled, mechanics matched the model to the cent.

Everything else is either measurement or waiting:

| | state |
|---|---|
| ladder monotonicity pairs | **LIVE.** ~$107/yr at $100 capital, $0 hosting |
| bookmaker-anchored pricing | built, coverage ~25% of games, untested live |
| forward calibration study | collecting — needs weeks before it says anything |
| NBA/NHL ladders | do not exist yet. `families-watch` flags the day they do |
| esports | no market on this venue. Stack is built and idle |
| key-number verticals | deprioritised: the venue does not net legs, so ~5% |
| bundle / exhaustive / multi-outcome | tested, no structure here |

## Go-live checklist

```
1. make money        confirm buying power; confirm Thursday's pair settled +$0.05
2. make ps           old MM service stopped AND disabled
3. make economics    HOSTING=<your real number>
4. ./cron_cycle.sh   run one cycle by hand, read cron.log
5. crontab -e        7 13,17,21,1 * * * /path/to/cron_cycle.sh
```

Step 1 is the gate. The pair on `clmsn-cah` settles Thursday and should realise
**the credit, or the credit plus ~$1/share**. It cannot realise a loss. If it
does, the sign convention is inverted and nothing should trade until that is
understood.

## Sizing

`CAP` in `cron_cycle.sh` is the dollars the ladder bot may deploy. Each
share-pair ties up ~$1 until settlement. Start at the amount you would shrug
off; the edge is real but small, and the failure modes this project actually
hit were all execution bugs, not bad strategy.

## What would change the economics

Not a better strategy — more cycles. CFB plays Thursday to Saturday, so capital
turns over weekly. NBA starts in about four weeks and is nightly. If the venue
lists `asc-nba` ladders, the same code runs ~5x as often:

```
CFB only, weekly       -> $107/yr
if NBA ladders appear  -> $320/yr
NBA nightly            -> $534/yr
```

`make families-watch` on the daily cron flags it the moment it happens. That is
the single event worth waiting for, and waiting costs nothing.
