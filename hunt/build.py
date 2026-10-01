"""Turn the SII Polymarket (global) trade dump into compact research tables.

    research/.venv-data/bin/python hunt/build.py

Inputs  research/pmdata/{markets,trades}.parquet  (HF: SII-WANGZJ/Polymarket_data)
Outputs research/pmdata/mk.parquet       one row per binary market that resolved:
                                         winner token, category, last trade. The
                                         dump writes many resolved markets as
                                         0.995/0.9995 rather than 1, and unresolved
                                         junk as 0.9/0.1 or 0.5, so a side >= 0.99
                                         is the winner and anything else is dropped
        research/pmdata/entries.parquet  the FIRST taker BUY of each token in each
                                         (price band, hours-before-close bucket):
                                         a price someone actually paid, i.e. a
                                         fillable ask, never a mid or a last print

Taker BUY only: a taker buying token X at p crossed X's ask at p. Using taker
SELLs too would mix in bid prices.
"""

import duckdb

D = "research/pmdata"
BANDS = [0.0, 0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.90, 0.95, 0.98, 1.0]
HOURS = [0, 1, 6, 24, 72, 24 * 7, 24 * 30, 10 ** 6]

CATEGORY_SQL = """
CASE
  WHEN p IN ('nba','nfl','mlb','nhl','cfb','cbb','wnba','kbo','npb','mls','epl','ucl','lal','bun',
             'sea','fl1','uel','fifwc','fwc','ere','por','tur','arg','bra','mex','uef','ufc',
             'boxing','f1','pga','atp','wta','cricipl','crint','nascar','cwc','afc','con','itsb')
       THEN 'sport_' || p
  WHEN p IN ('lol','cs2','dota2','val','r6','cod','ow','rl','sc2','mlbb','kog','pubg')
       THEN 'esport_' || p
  WHEN p IN ('btc','bitcoin','eth','ethereum','sol','solana','xrp','doge','bnb','hype')
       THEN CASE WHEN q ILIKE '%up or down%' THEN 'crypto_updown' ELSE 'crypto_level' END
  WHEN p IN ('elc','spl','acn','col','es2','chi','aus','lib','cdr','bl2','j1100','sud','nor','fif',
             'efa','mwoh','jap','kor','den','swe','sco','bel','gre','aut','sui','pol','cze','rou')
       OR q ILIKE 'will % win on 20%' THEN 'sport_soccer_minor'
  WHEN p IN ('itf','itfme','itfwo','utr','wimbledon') THEN 'sport_tennis_minor'
  WHEN p = 'euroleague' THEN 'sport_euroleague'
  WHEN p = 'league' OR q ILIKE 'lol:%' THEN 'esport_lol'
  WHEN p = 'highest' OR q ILIKE '%highest temperature%' THEN 'weather_high'
  WHEN p = 'lowest' OR q ILIKE '%lowest temperature%' THEN 'weather_low'
  WHEN q ILIKE '%up or down%' THEN 'crypto_updown'
  WHEN q ILIKE '%airdrop%' OR q ILIKE '%fdv%' OR q ILIKE '%token%launch%' OR q ILIKE '%public sale%'
       THEN 'crypto_launch'
  WHEN q ILIKE '%bitcoin%' OR q ILIKE '%ethereum%' OR q ILIKE '%solana%' OR q ILIKE '%xrp%'
    OR q ILIKE '%btc%' OR q ILIKE '%eth %' OR q ILIKE '%crypto%' OR q ILIKE '%ripple%' THEN 'crypto_level'
  WHEN q ILIKE '%temperature%' OR q ILIKE '%earthquake%' OR q ILIKE '%hurricane%' OR q ILIKE '%hottest%'
       THEN 'weather_other'
  WHEN q ILIKE '%win the%' OR q ILIKE '%wins the%' OR q ILIKE '%championship%' OR q ILIKE '%goalscorer%'
    OR q ILIKE '%goals scored%' OR q ILIKE '% vs. %' THEN 'sport_other'
  WHEN q ILIKE '% out by%' OR q ILIKE '%arrested%' OR q ILIKE '%in custody%' OR q ILIKE '%released%'
    OR q ILIKE '%resign%' THEN 'people_events'
  WHEN q ILIKE '%say %' OR q ILIKE '%mention%' THEN 'mentions'
  WHEN q ILIKE '%tweet%' OR q ILIKE '%post%times%' THEN 'tweets'
  WHEN q ILIKE '%rotten tomatoes%' OR q ILIKE '%box office%' OR q ILIKE '%billboard%'
    OR q ILIKE '%spotify%' OR q ILIKE '%oscar%' OR q ILIKE '%grammy%' OR q ILIKE '%emmy%'
    OR q ILIKE '%netflix%' OR q ILIKE '%album%' OR q ILIKE '%song%' OR q ILIKE '%movie%'
       THEN 'culture'
  WHEN q ILIKE '%fed %' OR q ILIKE '%interest rate%' OR q ILIKE '%cpi%' OR q ILIKE '%inflation%'
    OR q ILIKE '%gdp%' OR q ILIKE '%unemployment%' OR q ILIKE '%recession%' THEN 'macro'
  WHEN q ILIKE '%election%' OR q ILIKE '%president%' OR q ILIKE '%senate%' OR q ILIKE '%governor%'
    OR q ILIKE '%nominee%' OR q ILIKE '%prime minister%' OR q ILIKE '%parliament%'
    OR q ILIKE '%mayor%' OR q ILIKE '%primary%' OR q ILIKE '%trump%' THEN 'politics'
  WHEN q ILIKE '%war%' OR q ILIKE '%ceasefire%' OR q ILIKE '%strike%' OR q ILIKE '%invade%'
    OR q ILIKE '%military%' OR q ILIKE '%iran%' OR q ILIKE '%israel%' OR q ILIKE '%ukraine%'
       THEN 'geopolitics'
  WHEN q ILIKE '%stock%' OR q ILIKE '%s&p%' OR q ILIKE '%nasdaq%' OR q ILIKE '%close above%'
    OR q ILIKE '%earnings%' OR q ILIKE '%ipo%' OR q ILIKE '%market cap%' THEN 'finance'
  ELSE 'other'
END"""


def main():
    c = duckdb.connect()
    c.sql("SET memory_limit='10GB'; SET threads=8; SET preserve_insertion_order=false")
    c.sql(f"SET temp_directory='{D}/tmp'")
    print("markets ...", flush=True)
    c.sql(f"""
    CREATE OR REPLACE TABLE mk AS
    WITH m AS (
      SELECT id AS market_id, event_id, slug, question AS q, token1, token2, volume,
             lower(split_part(slug, '-', 1)) AS p, end_date,
             TRY_CAST(trim(split_part(replace(replace(replace(outcome_prices, '[', ''), ']', ''), '''', ''), ',', 1)) AS DOUBLE) AS o1,
             TRY_CAST(trim(split_part(replace(replace(replace(outcome_prices, '[', ''), ']', ''), '''', ''), ',', 2)) AS DOUBLE) AS o2
      FROM read_parquet('{D}/markets.parquet')
      WHERE closed = 1
    )
    SELECT market_id, event_id, slug, q, p, volume, end_date,
           CASE WHEN o1 >= 0.99 THEN 'token1' ELSE 'token2' END AS winner,
           {CATEGORY_SQL} AS cat
    FROM m WHERE greatest(o1, o2) >= 0.99 AND least(o1, o2) <= 0.01""")
    print(c.sql("SELECT count(*), count(DISTINCT cat) FROM mk").fetchall(), flush=True)

    print("last trade per market ...", flush=True)
    c.sql(f"""
    CREATE OR REPLACE TABLE last AS
    SELECT market_id, max(timestamp) AS t_end, count(*) AS n_trades, sum(usd_amount) AS usd
    FROM read_parquet('{D}/trades.parquet') GROUP BY market_id""")
    c.sql("CREATE OR REPLACE TABLE mk2 AS SELECT mk.*, t_end, n_trades, usd FROM mk JOIN last USING (market_id)")
    c.sql(f"COPY mk2 TO '{D}/mk.parquet' (FORMAT parquet)")
    print(c.sql("SELECT cat, count(*) n, round(sum(usd)/1e6) usd_m FROM mk2 GROUP BY 1 ORDER BY 3 DESC").df().to_string(), flush=True)

    band = "CASE " + " ".join(f"WHEN price < {b} THEN {i}" for i, b in enumerate(BANDS[1:])) + " ELSE 99 END"
    hrs = "CASE " + " ".join(f"WHEN h < {b} THEN {i}" for i, b in enumerate(HOURS[1:])) + " ELSE 99 END"
    print("entries ...", flush=True)
    c.sql(f"""
    COPY (
      WITH t AS (
        SELECT tr.market_id, tr.nonusdc_side AS tok, tr.price, tr.usd_amount, tr.timestamp,
               (m.t_end - tr.timestamp) / 3600.0 AS h
        FROM read_parquet('{D}/trades.parquet') tr JOIN mk2 m USING (market_id)
        WHERE tr.taker_direction = 'BUY' AND tr.price > 0 AND tr.price < 1
      )
      SELECT market_id, tok, {band} AS band, {hrs} AS hb,
             arg_min(price, timestamp) AS price, min(timestamp) AS ts,
             arg_min(usd_amount, timestamp) AS usd, arg_min(h, timestamp) AS h,
             count(*) AS n_in_cell, sum(usd_amount) AS usd_in_cell
      FROM t GROUP BY ALL
    ) TO '{D}/entries.parquet' (FORMAT parquet)""")
    print(c.sql(f"SELECT count(*) FROM read_parquet('{D}/entries.parquet')").fetchall())


if __name__ == "__main__":
    main()
