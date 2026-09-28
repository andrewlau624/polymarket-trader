# Weather: daily-high markets on Polymarket US

Five markets a day (NYC Central Park, Chicago Midway, Miami, LAX, SFO), six
1F bands each, settled on the NWS Climatological Report (CLI) the next morning.
The station's METARs are public all day. The question: does live station data
call the settling band before the market does?

Why this and not a forecast model: an open 7,902-market Kalshi backtest
(github.com/myfirstcodeo/kalshi-weather-fair-value) found the day-before
market beat a GFS+ECMWF model in every city and every price band (Brier
0.1195 vs 0.1466; buy-YES -38%, buy-NO -7%). It skipped same-day markets.
Observation, not forecasting, is the untested part.

## Phase 0 result (`python weather_study.py`, 2 years x 5 stations, 3,639 days)

For each hour of the climate day (local STANDARD time), M = the running max so
far, whole F, from hourly/special METAR temperatures plus 6-hour max groups.
Truth = the CLI high.

**The hourly readings miss the peak about a third of the time.** Before the
day's last in-day 6-hour max group arrives, CLI = M+1 on 23-51% of days: the
peak falls between hourly readings. That group is the jump in each table:

| station | 6-h max group lands (LST) | P(CLI = M) just before | just after | end of day |
|---|---|---|---|---|
| NYC | 18:51 (23:51Z) | 58.4% | 91.5% | 94.0% |
| MDW | 17:51 (23:51Z) | 52.5% | 90.2% | 94.8% |
| MIA | 18:51 (23:51Z) | 72.0% | 98.1% | 98.2% |
| LAX | 15:51 (23:51Z) | 38.2% | 97.9% | 98.9% |
| SFO | 15:51 (23:51Z) | 27.9% | 94.9% | 99.0% |

CLI < M (the live number OVERSTATING the settlement) is 0.0-0.8% at every hour.

Pooled, once the reading has fallen 3F+ below the running max, P(CLI = M) is
96.4% at 18:59 and 97.3% from 21:59. The pre-set bar was 97%: passed, narrowly.
MIA/LAX/SFO carry it (~98-99%); NYC/MDW stay at 92-95%, which is not enough to
bet a band on.

## What that means for trading

1. **Before 23:51Z** a trader watching hourly temperatures sees M, but the
   settlement is M+1 a third of the time. Anyone pricing the M band from hourly
   data overprices it and underprices M+1. Tradeable only if the venue does.
2. **After 23:51Z** (15:51 PST: 4:51 pm PDT for LAX/SFO) the 6-hour max group
   pins the band at ~98% at three stations. If the venue still prices that band
   well under 95c, that is the trade.
3. **Bands below M are dead** at >= 99.2% at every hour. Their NO side should
   already cost ~99c; worth recording, probably not worth trading.

Phase 1 records the venue's books beside the METARs to see whether 1 or 2 is
ever priced wrong, and at what size.

## Traps this handles, and where each came from

- Truth is the CLI archive (IEM `json/cli.py`), never IEM's `daily.py`, which
  reads ~1F low (METAR rounds to whole C; noted in the Kalshi backtest repo).
- The climate day is midnight-to-midnight local STANDARD time (in summer,
  1 am-1 am daylight time). A popular open bot used the UTC day.
- Stations are the ones the venue's rules name (Central Park, not LaGuardia;
  Midway, not O'Hare). The same bot had both wrong.
- A 6-hour max group is used only if its whole window lies inside the day.
