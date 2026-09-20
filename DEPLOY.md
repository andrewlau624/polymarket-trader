# Deployment — the cheapest thing that works

`run_economics.py` is blunt about this: at **$18/month hosting the system loses
$109/year at any capital**, because depth caps the edge before capital does.
Hosting is not an overhead here, it is the deciding term.

```
hosting $18/mo -> net $-109.30/yr
hosting $ 6/mo -> net $  34.70/yr
hosting $ 4/mo -> net $  58.70/yr
hosting $ 0/mo -> net $ 106.70/yr
```

## It does not need a server

The edge is slow. Violations stood in 12 of 15 observations across ten sweeps
over several hours — they are structural inconsistencies nobody reconciles, not
an informational race. A 20-minute daemon loop was paying for a persistent
machine to rediscover the same opportunities.

Four cron runs a day capture nearly the same thing.

```
crontab -e
7 13,17,21,1 * * *  /home/ihearthim/polymarket-trader/cron_cycle.sh
```

`cron_cycle.sh` runs one ladder sweep, one price snapshot for the forward
calibration study, and the family watch that flags the day a nightly-sport
ladder appears. It clears a stale lock rather than dying on one, and each step
has a timeout so a hung call cannot wedge the cycle.

## Where to run it

| option | cost | notes |
|---|---|---|
| **Oracle Cloud Always Free** | $0 | ARM VM, free indefinitely. Best option. |
| Hetzner CX22 / Vultr | ~$4 | trivial to run, no signup friction |
| your own laptop | $0 | fine — cron only needs it awake at those hours |
| current droplet | $18 | **the reason this loses money** |

Anything with Python and outbound HTTPS works. The cycle uses a few minutes of
CPU a day and well under 512MB.

## What still runs anywhere

`bo3`, `keynumbers`, `calibrate`, `es-feed`, `run_economics` need no venue
credentials at all — run them on a laptop.

## Before going live

1. `make money` — confirm buying power and that the Thursday pair settled right
2. `make ps` — confirm the old MM service is stopped AND disabled
3. run `cron_cycle.sh` by hand once and read `cron.log`
4. only then add the crontab line
