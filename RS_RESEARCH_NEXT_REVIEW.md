# RS Research Next Review

Updated: 2026-09-10

## What Is Running

- Paper watcher: 11 paper-only RelativeStrengthV2 candidates, refreshed and initialized from current candles.
- 1h refinement: `benchmark-1h-refine`, BTCUSDT 1h vs `CRYPTO_BASKET`, 1095 days, focused around the best 1h cluster.

## Paper Candidates To Compare

| Group | Candidate |
| --- | --- |
| Older 4h references | `rs-btc4h-best-risk03`, `rs-btc4h-threshold-0025-risk03`, `rs-btc4h-lookback42-risk03`, `rs-btc4h-robust-nearby-risk03`, `rs-btc4h-meaningful-nearby-risk03`, `rs-btc4h-basket-benchmark-risk03` |
| Improved 4h basket winners | `rs-btc4h-basket-lb42-thr0-rsi61-atr27-risk03`, `rs-btc4h-basket-lb42-thr0-rsi58-atr26-risk03`, `rs-btc4h-basket-lb42-thr0-rsi60-atr27-risk03` |
| Best 1h candidates | `rs-btc1h-basket-lb156-thr003-rsi58-atr27-risk015`, `rs-btc1h-basket-lb156-thr0025-rsi58-atr30-risk02` |

## Best Known Research Results

| Timeframe | Verdict | Best Cluster |
| --- | --- | --- |
| 4h | Robust | `rsLookback=42`, `rsThreshold=0`, `rsiMin=58-61`, `rsiMax=72`, `atr=2.6-2.7`, `risk=0.03` |
| 1h | Promising but sensitive | `rsLookback=156`, `rsThreshold=-0.035..-0.025`, `rsiMin=58`, `rsiMax=72/74`, `atr=2.7/3.0`, `risk=0.015/0.02` |

## Next Decision

If the 1h refinement confirms the same cluster, keep one conservative 1h paper candidate and one aggressive 1h paper candidate. If it fails, keep 1h as research only and move the next optimization effort to 15m with stricter execution filters rather than promoting 1h.
