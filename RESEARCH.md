# Crypto Algo Trading — Research Foundation

Blunt framing first. The edges that are *public and well-documented* are mostly gone or compressed.
"Loads of money" from a retail algo on a public strategy is not realistic. What IS realistic is
finding edges in (a) harder-to-source data, (b) timing/execution that retail ignores, or
(c) combining mechanisms that individually are weak but together form a signal. This doc
catalogs what actually drives the market (with evidence) and the strategy directions that are
NOT the standard public playbook.

---

## Part 1 — What actually drives this market (evidence-based)

Ranked roughly by documented explanatory power as of 2026:

### 1. Global liquidity conditions (macro) — the dominant driver
- Direction follows the Fed's interest-rate path vs other central banks, the dollar (DXY), and
  Treasury yields, not crypto-native news. When liquidity eases, capital floods risk assets; BTC
  is a primary beneficiary. When conditions tighten, BTC weakens (esp. as dollar strengthens).
- Rate *surprises* matter more than levels: an inflation/CPI surprise or FOMC hawkish surprise
  moves price sharply. PCE is the Fed's favored tool, but CPI jolts markets faster.
- Event-study evidence (Oct 2025 tariff shock): spillover beta ≈ 0.65 (p<0.001), cross-asset
  contagion ~20% stronger than 2018 trade-war spillovers. Volatility persistence is high
  (GARCH α+β ≈ 0.90) — big shocks leave vol elevated for a long time.

### 2. Institutional ETF flows — real, measurable price impact
- Spot BTC ETFs hold ~257K BTC (~7% of supply). $100M net flow ≈ +53 bps same-day return
  (Kyle's lambda 53 bps/$100M OLS, 74 bps IV). Flows explain ~21% of daily return variation and
  predict next-day returns.
- Critical finding — the "flow-persistence illusion": individual flow shocks reverse, BUT flow
  autocorrelation generates new shocks before prior reversals complete, producing a cumulative
  drift that looks permanent. Net effect ≈ +96 bps/$100M at 10 days. No reversal up to 12 months.
- This is a NEW, still-inefficient data source (SoSoValue, Farside, Databento). Most retail does
  not trade off daily net-flow streaks.

### 3. Stablecoin supply — the leading "dry powder" indicator
- Stablecoin minting historically *precedes* BTC rallies: capital converts fiat→stablecoin before
  deploying into risk. Rising supply with flat price = capital parked on sidelines, poised to move.
- 2025: $269B supply (record dry powder), +$77B YTD. Q1 2026: $308B→$318B.
- Less-known granular angle: stablecoin supply *resident on exchanges* (ready to hit the order
  book) vs total supply, and on-chain transfer velocity, signal WHEN dry powder deploys.

### 4. Leverage / derivative positioning — the amplification engine
- Perpetual funding, open interest, long/short ratios, and liquidation clusters determine not
  *whether* the market moves but *how violently*. Leverage manufactures fat tails and clustered
  volatility (positive feedback loop).
- Funding rates are strongly right-skewed carry instruments (BTC mean 1.13 bps/8h, 13.5% negative
  obs, kurtosis 30). Most carry concentrates in a few high-funding episodes.
- Liquidation cascades (Oct 2025: $19B, largest on record; Aug 2026: $1.74B short squeeze) are
  liquidity crises, not fundamental revaluations. OI collapses 25–70%; market makers withdraw then
  return → "recovery wick."

### 5. Market microstructure / mark-price oracle reflexivity
- Crypto liquidation engines have ENDOGENOUS crash amplification through the mark-price oracle
  that is structurally absent from equity markets. On fully on-chain venues (Hyperliquid), every
  position and forced fill is public, so the cascade's branching ratio λ can be measured in flight.
- Cascade signature lives in the LIQUIDITY sector (impact spread spikes 3.2–9.1x, OI clearing),
  NOT in the memory of any single price. Criticality/early-warning signals in price alone are a
  negative result.
- Stablecoin depeg domino: collateral assets used widely (USDe etc.) can trigger forced
  liquidations across LSDs/alt-L1s when they depeg.

### 6. Crypto-native flow / rotation signals
- Bitcoin dominance and the Altcoin Season Index gate alt rotations: a real altcoin season
  historically needs BTC dominance <55% AND Altcoin Season Index >75. As of 2026: dominance
  ~56–58%, season index 49 → no alt season, "probe" phase.
- Stablecoin-based FX parity deviations: buying USD exposure via stablecoins vs traditional FX
  leaves persistent price gaps across 27 fiat currencies / 64 exchanges (BIS). This is an emerging
  cross-market transmission channel.

---

## Part 2 — Strategy directions that are NOT the public playbook

The public playbook = momentum/breakout, RSI/MACD, simple funding-rate cash-and-carry, naive
buy-the-dip. All are arbitraged or regime-dependent. The directions below exploit mechanisms from
Part 1 that are under-traded or need data most retail won't source.

### A. Flow-persistence momentum (ETF net-flow streaks)
- **Mechanism:** Daily net ETF flow autocorrelation creates drift that completes before reversals.
  Flows predict next-day returns.
- **Edge over retail:** Trade the *flow streak* (direction + autocorrelation persistence), not price.
- **Signal inputs:** Daily net ETF flows (SoSoValue/Farside), flow 5-day autocorrelation, streak
  length. Enter on accelerating streak, EXIT on flow-autocorrelation decay — not on price.
- **Risk:** Requires daily data subscription; weekend/holiday gaps; flow data lags a day.

### B. Stablecoin-velocity deployment timer
- **Mechanism:** Stablecoin supply expansion precedes rallies; deployment (velocity) precedes the
  move within the expansion.
- **Edge:** Instead of "supply grew → bullish," time entry on exchange-resident stablecoin inflows
  and on-chain transfer velocity accelerating. Distinguishes "dry powder piling up" (supply up,
  velocity flat) from "dry powder deploying" (velocity up).
- **Signal inputs:** Exchange-resident stablecoin balances (USDT/USDC), on-chain transfer counts/
  velocity, total supply deltas. On-chain data is public — no expensive vendor needed.

### C. Post-cascade exhaustion mean-reversion (fade the overshoot)
- **Mechanism:** Cascades are liquidity events with a documented recovery-wick. OI clears 25–70%,
  liquidity providers withdraw then return. Cascade signature lives in OI-clearing + impact-spread,
  not price.
- **Edge over "buy the dip":** Condition entry on *mechanical* exhaustion, not price level:
  1) OI has collapsed (>X% from pre-cascade),
  2) impact-spread spike has begun to contract (MMs returning),
  3) branching ratio / ongoing liquidation flow has subsided.
  That timing is much more precise than guessing a floor.
- **Signal inputs:** Aggregate open interest, liquidation flow, order-book depth/impact spread
  across venues. On Hyperliquid, position/fill data is on-chain and free.

### D. Mark-price oracle reflexivity arbitrage (on-chain venue)
- **Mechanism:** Exchange liquidation engines have endogenous amplification via the mark-price
  oracle; during stress the oracle/venue pricing can decouple from the true cross-venue mid
  (the "depeg domino"). Fully-transparent venues let you measure the engine in flight.
- **Edge:** Detect when a venue's mark/liquidation pricing has decoupled from cross-venue spot and
  fade the mispriced overshoot (or provide liquidity where ADL is over-selling).
- **Signal inputs:** Per-venue mark price vs cross-venue consensus spot; on-chain liquidation
  fill logs; collateral-asset depeg monitoring (USDe etc.).

### E. Cross-venue funding-dispersion harvest
- **Mechanism:** The same perp funds differently across venues; funding is right-skewed with most
  carry in a few episodes. Shorting where funding is HIGHEST and buying spot is itself a selection
  edge vs single-venue cash-and-carry.
- **Edge:** Multi-venue funding scanner to find dispersion; only harvest during elevated-funding
  regimes (the skew is where the money is); exit on funding decay.
- **Caveat (honest):** Aggregate carry has compressed sharply since 2024 (Sharpe 6.45 full-sample
  → 4.06 post-2024 → negative in 2025). This is a yield *harvest*, not a get-rich strategy. Treat
  as a steady base return, not alpha.

### F. Vol-sell after macro surprise (volatility persistence)
- **Mechanism:** Macro surprises drive sharp moves; volatility persistence is high (GARCH
  α+β≈0.90), meaning elevated vol decays slowly — but the *direction* often overshoots then mean-
  reverts after the shock is absorbed.
- **Edge:** After a surprise event, realized vol is elevated (rich IV). Sell straddles/short vol on
  the decay when the event's directional beta is exhausted, rather than chasing direction.
- **Risk:** Fat tails; tail-risk hedging mandatory. This is a professional-grade option strategy —
  not for a first build.

---

## Part 3 — Honest reality check (read this twice)

1. **The high-certainty public edges are gone.** Carry/funding arb went from a Sharpe ~6 to
   negative by 2025. Any strategy a retail blog publishes is already being harvested at scale.
2. **Your realistic edges are in data access + timing, not magic formulas.** A and B are the
   strongest "less-crowded" candidates because they need daily flow/on-chain data most retail won't
   bother sourcing, and the mechanisms have documented evidence.
3. **Backtesting reality:** In-sample backtests lie. Walk-forward, transaction costs (fees +
   slippage + funding), and out-of-sample decay matter more than any indicator. Expect any edge to
   shrink as capital/attention follows it.
4. **"Loads of money" requires either scale (capital), latency (infrastructure), or a genuine
   data edge — ideally two of three.** A laptop strategy with a data edge (A, B) can be a
   legitimate starting point; it will not be a rocketship.
5. **Best path:** Build a rigorous backtest harness FIRST (reproducible data, walk-forward,
   cost model). Prove a strategy survives out-of-sample costs before ever risking real capital.
   Strategy A and B are the recommended first two to prototype because their data is available and
   their mechanisms have evidence behind them.

## Recommended first build
1. Data layer: daily BTC/ETH OHLCV + spot-ETF net flows + stablecoin supply/velocity (public).
2. Backtest harness: walk-forward, realistic fee+funding+slippage model, Sharpe/MaxDD reporting.
3. Prototype Strategy A (flow-persistence) and B (stablecoin velocity) as the first two candidates.
4. Paper-trade the survivor live for 60–90 days before any real capital.
