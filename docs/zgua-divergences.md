# Zgua Divergences

An independent Python chart study, not a tactic or a reproduction of proprietary Market Cipher. Find **Zgua Divergences** in Indicators and the Community library. Every divergence category has its own checkbox, colour and chart label. The default enables all eight categories; weak/equal-extreme variants are exploratory, not a measured reliability ranking.

| Category | Bullish lows | Bearish highs | Labels |
| --- | --- | --- | --- |
| Regular | Price lower; oscillator higher | Price higher; oscillator lower | RB / RS |
| Hidden | Price higher; oscillator lower | Price lower; oscillator higher | HB / HS |
| Exaggerated | Price approximately equal; oscillator higher | Price approximately equal; oscillator lower | EB / ES |
| Weak | Price lower; oscillator approximately equal | Price higher; oscillator approximately equal | WB / WS |

Regular geometry can indicate exhaustion; hidden geometry is normally interpreted in the context of an existing trend. Neither predicts the next candle. Exaggerated/weak names and A/B/C terminology are not universal; Zgua exposes exact numerical definitions rather than assigning confidence percentages.

## Detection Contract

- Strict price highs/lows with configurable Left/Right confirmation bars. Ties are not strict pivots.
- Oscillators are sampled at the **same price pivot timestamps**: Wilder RSI, MACD line, MACD histogram, WaveTrend, raw stochastic %K, OBV, CMF. This is price-anchored divergence, not independently matched oscillator pivots or cross-symbol divergence.
- Compare up to Lookback prior pivots, within Min/Max bars; use the nearest eligible match for each category. Related studies on a pair are combined into its label, not counted as independent probabilities.
- Price equality is a percent of the earlier swing price. Oscillator equality/minimum change is a percent of that oscillator's trailing range over `2 * max_bars + right + 1` bars at confirmation. Flat/unavailable studies cannot create strong divergences. Warmup is never backfilled.
- Disjoint comparisons: equal-price geometry first, then equal-oscillator weakness, then regular/hidden geometry. Differences inside the noise interval produce no strong classification.
- Do not read unfinished final OHLC. A confirmation candle becomes usable when the next candle opening is observed. Arrows are at that opening; connecting lines show historical pivot endpoints **retrospectively**. They were not available at the old pivot. On historic/replay data this conservative convention also leaves the final candle unused.
- The `+ break` suffix requires the confirmation candle close to break the intervening swing resistance/support. Absence of that suffix means no such price confirmation was established. It does not veto display of the geometric divergence.
- Chart labels use compact `RB (3)`-style counts of agreeing studies and `+` for that boundary break; the indicator info window lists the latest twelve observations with full study names. A shared marker carrier stacks same-bar categories, rather than drawing their text on top of each other.
- Latest Max events are retained (default 80, maximum 200); old annotations rolling off this display is not signal repainting. Draw lines can be disabled separately.

## Additional Context

Optional bullish/bearish **body engulfing** and **previous-confirmed-swing wick sweep/reclaim** patterns are separately labelled and disabled by default. They are not divergences. A sweep cannot reference an unconfirmed future swing. These are initial context detectors, not the entire chart-pattern library or proprietary SMC/order-block claims.

Multiple oscillators are correlated. RSI/Stochastic extremes are context, not required detection thresholds. Volume studies depend on venue-specific volume; CMF/OBV are not exchange order-flow delta. Strong trends can sustain repeated divergence and a confirmed pivot can arrive too late for an attractive entry.

## Later Research

Persist observation timestamps and exact pair metadata before testing entry/exit filters. Evaluate fresh price triggers, trend alignment, room to invalidation/objective, costs, and delayed confirmation. Compare with unchanged baseline tactics in training/validation, and keep a fresh final holdout untouched. No live orders, leverage settings, paper candidates or readiness scores are changed by this study.

## References

- [TradingView: RSI divergence and limitations](https://www.tradingview.com/support/solutions/43000589127-rsi-divergence-indicator/).
- [Author's public regular/hidden RSI documentation](https://www.tradingview.com/script/u679p68m-RSI-Divergence-Regular-Hidden-darshakssc/). Theory reference only; Zgua uses original Python, not copied Pine code or an identical pivot-matching scheme.
- [StockCharts: scanning divergences](https://help.stockcharts.com/scanning-and-alerts/scan-writing-resource-center/scanning-case-studies/scanning-for-divergences).
- [StockCharts: stochastic confirmation and limitations](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/stochastic-oscillator-fast-slow-and-full).
