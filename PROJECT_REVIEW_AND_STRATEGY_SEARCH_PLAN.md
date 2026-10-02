# ZguaCharts Project Review And Strategy Search Plan

Date: 2026-09-05

This document summarizes what the app is, what it currently does, what research we have already run, which results are most promising, and how I would design the strategy-search process if I were building this app from scratch with the goal of finding robust, profit-seeking trading tactics.

Signals, backtests, and paper runs are research tools only. Nothing here is financial advice, and the current app state is paper-only.

## 1. Product Goal

ZguaCharts started as a local TradingView-like chart dashboard and has grown into a research workbench for discovering, testing, validating, and paper-observing trading strategies.

The practical goal is not just "show candles". The real goal is:

1. Load clean market data.
2. Visualize charts, indicators, signals, backtests, and paper status.
3. Search many possible strategy families and parameter combinations.
4. Reject weak or overfit candidates.
5. Promote only strong candidates into disabled/manual review.
6. Run paper-only observation before any real-money discussion.

That is the right direction. The app is already much more than a chart toy: it has a research engine, optimizer, quality gates, paper simulator, safety checks, and audit trails.

## 2. High-Level Design

The project is split into three main layers.

| Layer | Main files | Responsibility |
|---|---|---|
| Browser GUI | `templates/index.html`, `static/app.js`, `static/styles.css` | Render charts, controls, tables, diagnostics, paper/research panels. |
| Flask backend | `app.py`, `data_source.py`, `indicators.py`, `signals.py`, `backtest.py`, `strategy.py` | Serve the app, fetch/normalize data, expose APIs, bridge to Node research tools, own safety/promotion decisions. |
| Node core engine | `core/*`, `cli/*` | Run backtests, strategy rules, optimization, research batches, validation, paper simulation, reports. |

The important design rule is good: the frontend should render results, not invent trading logic. Indicators, strategies, scoring, quality gates, paper simulation, and promotion decisions belong in backend/core code.

## 3. GUI Overview

The current GUI is organized around workflow pages:

| Page | Purpose |
|---|---|
| Charts | Multi-chart view with sources, symbols, timeframes, indicators, signals, watchlist, and active signals. |
| Dashboard | Strategy finder, campaign controls, champion board, tested strategy matrix, research ledger. |
| Research | Research tools for search, validation, diagnostics, and export. |
| Backtest | Manual strategy builder and read-only backtest runner. |
| Strategy Lab | Experimental area for families and strategy comparisons. |
| Ops | Health checks, operational diagnostics, data/cache status. |
| Settings | App/runtime configuration. |
| Paper | Paper simulation status, safety gates, observation quality, session events, trades, and tick instructions. |

The GUI is already valuable because it exposes the research process visually. The next GUI improvement I would prioritize is a clearer "Candidate Lifecycle" screen:

`Raw result -> reproducible -> robust -> paper candidate -> paper observation -> manual review`

That would make it obvious why something with high return is still rejected.

## 4. Data And Execution Model

The app uses public/no-key data sources by default:

| Source | Use |
|---|---|
| Bybit | Crypto historical and live candles. Main source for BTCUSDT research. |
| Hyperliquid | Optional crypto data/live feed. |
| yfinance | Mainly Indian stock data through Flask. |

Backtests use normalized OHLCV candles. Paper simulation uses the same kind of candle data, but advances a local state file instead of placing orders.

Important execution choices already present:

- Backtests can use `next-open` fills, which is more realistic than entering on the same candle close.
- Fees, slippage, drawdown, and trade count are considered.
- Research can run from GUI APIs or CLI scripts.
- Runtime configs live in `config/local/`, while tracked defaults stay in `config/`.
- Paper candidates are explicit and isolated.

## 5. Strategy Families

The app includes many strategy families and placeholders. The important families we have actively tested recently are:

| Strategy | Basic idea |
|---|---|
| RelativeStrengthV2 | Trade BTC when relative strength, RSI, ATR, trend/regime, and quality filters align. Current best family. |
| EmaBounceV2 | EMA pullback/bounce continuation idea. Paper results were poor; no longer active. |
| PullbackReclaimV2 | Wait for pullback, then reclaim confirmation. Best raw 15m lead, but not enough evidence. |
| RangeExpansionV2 | Squeeze/range expansion breakout logic. Looked interesting raw, later discarded. |
| SimpleAtrTrendV2 | ATR/trend follower. High raw return on 15m, but drawdown too high. |
| BreakoutRetestV2 | Donchian breakout/retest style. Did not produce useful 15m result in the latest sweep. |
| FibPullbackContinuationV1 | Fibonacci/pullback continuation. Too few trades in the latest sweep. |
| VWAP/Momentum/Mean Reversion variants | Useful research families, but no current champion from latest campaign. |

The app should keep testing strategy families, but the current best evidence says the active baseline should remain RelativeStrengthV2 on BTCUSDT 4h.

## 6. What We Recently Built Or Changed

Recent project work included:

1. Added causal regime entry filters for backtests.
2. Added `RelativeStrengthV2` bullish-only refinement tooling.
3. Prepared a disabled/manual RS paper candidate package.
4. Replaced the bad EmaBounceV2 paper candidate with RS.
5. Fixed paper enable/tick guards so they support the active candidate identity rather than hardcoding EmaBounceV2.
6. Implemented multi-paper candidate support.
7. Started three isolated RS 4h paper candidates side-by-side.
8. Ran a broad 15m optimizer campaign.
9. Ran focused reviews on the best 15m leads.
10. Restarted the multi-paper watcher after the old watcher stopped.

## 7. Current Paper Status

As of the latest check, the multi-paper watcher is running and all three active candidates are paper-only.

| Candidate | Strategy | Symbol | Timeframe | Risk | Current state |
|---|---|---|---|---:|---|
| `rs-btc4h-best-risk03` | RelativeStrengthV2 | BTCUSDT | 4h | 0.03 | Open long paper position |
| `rs-btc4h-threshold-0025-risk03` | RelativeStrengthV2 | BTCUSDT | 4h | 0.03 | Open long paper position |
| `rs-btc4h-lookback42-risk03` | RelativeStrengthV2 | BTCUSDT | 4h | 0.03 | Open long paper position |

Latest observed paper state:

| Candidate | Entry | Last price | Unrealized PnL | Equity | Closed trades | Warnings |
|---|---:|---:|---:|---:|---:|---|
| RS best | 81240.84 | 79705.50 | -178.80 | 9821.20 | 0 | none |
| RS threshold -0.025 | 81240.84 | 79705.50 | -178.80 | 9821.20 | 0 | none |
| RS lookback 42 | 81240.84 | 79705.50 | -178.80 | 9821.20 | 0 | none |

Interpretation: this is not promising yet in live paper observation, but it is also too early to judge because no paper trade has closed. The right action is to observe the exit behavior and compare realized results, not panic on one open unrealized loss.

## 8. Most Promising Backtest Result

The best validated family so far is still RelativeStrengthV2 BTCUSDT 4h with bullish-only causal regime filtering.

Recommended paper-risk version:

```json
{
  "strategy": "RelativeStrengthV2",
  "symbol": "BTCUSDT",
  "timeframe": "4h",
  "params": {
    "atrMultiplier": 2.8,
    "causalRegimeFilter": "bullishOnly",
    "indicatorFilterPreset": "trendQuality",
    "regimeMode": "looseBtcBull",
    "riskPct": 0.03,
    "rsLookback": 36,
    "rsThreshold": -0.03,
    "rsiMax": 72,
    "rsiMin": 55,
    "volumeFilter": false
  }
}
```

Key longer-window evidence remembered from recent research:

| Version | Period | Return | Profit factor | Max DD | Stress DD | Verdict |
|---|---:|---:|---:|---:|---:|---|
| RS aggressive risk 0.05 | 1095d | +360.81% | 2.38 | 19.32% | 21.65% | Strong, but too aggressive for paper/live review. |
| RS risk 0.03 | 1095d | +162.30% | 2.38 | 11.95% | 13.48% | Best practical paper candidate. |
| RS risk 0.03 | 1460d | +228.84% | 2.20 | 19.32% | 22.69% | Still good, but drawdown proves leverage would be dangerous. |

The key lesson: the high return is interesting, but the drawdown profile means leverage is not acceptable. A 22.69% max drawdown at 1x can become catastrophic at 5x before fees, slippage, liquidation mechanics, funding, and volatility gaps.

### Nearby-Parameter Robustness Update

On 2026-09-05, we added and ran a dedicated nearby-parameter robustness script:

```bash
npm run research:rs-nearby -- --days 1095 --top 20 --max-screen 120 --tag 20260905-quick
```

Report:

```text
reports/research-campaigns/btc4h-relativestrength-nearby-robustness-20260905-quick.json
```

The quick nearby test screened the closest 120 parameter variants around the active RS 4h setup. Result: `ROBUST_CLUSTER`.

| Metric | Result |
|---|---:|
| Tested nearby variants | 120 |
| Pass count | 120 |
| Pass rate | 100% |
| Near-one-change pass rate | 100% |
| Near-two-change pass rate | 100% |
| Median return | +168.11% |
| Median profit factor | 2.377 |
| Median drawdown | 12.37% |

Best nearby variant:

| Parameter | Old paper baseline | Best nearby |
|---|---:|---:|
| `atrMultiplier` | 2.8 | 2.6 |
| `rsiMin` | 55 | 58 |
| `rsLookback` | 36 | 36 |
| `rsThreshold` | -0.03 | -0.03 |
| `rsiMax` | 72 | 72 |
| `riskPct` | 0.03 | 0.03 |

Best nearby metrics:

| Scenario | Return | PF | DD | Trades | Status |
|---|---:|---:|---:|---:|---|
| Baseline costs | +195.26% | 2.44 | 12.84% | 86 | PASS |
| Double slippage | +186.29% | 2.37 | 13.25% | 86 | PASS |
| Double fees | +169.96% | 2.33 | 14.02% | 86 | PASS |
| High stress | +153.80% | 2.20 | 14.84% | 86 | PASS |

Walk-forward also passed all four folds. This is a good sign because the best setup is not just one isolated magic value. We added it as a fourth paper-only candidate: `rs-btc4h-robust-nearby-risk03`.

## 9. EmaBounceV2 Result

EmaBounceV2 is no longer a candidate.

Paper result:

| Metric | Result |
|---|---:|
| Closed trades | 5 |
| Wins | 0 |
| Losses | 5 |
| Net return | -0.5692% |
| Realized PnL | -56.924 |
| Max paper DD | 0.9335% |
| State | Paper recovery / handbrake triggered |

This was useful because it proved the paper safety loop works. It was not useful as a strategy.

## 10. Latest 15m Research Results

A broad 15m campaign ran across multiple strategies and symbols with a roughly 20-hour budget. It finished, but it did not produce a replacement candidate.

Important limitation: it did not exhaust every possible parameter combination. It sampled large grids under a runtime budget.

Top 15m rows:

| Rank | Strategy | Return | PF | DD | Trades | Status | Decision |
|---:|---|---:|---:|---:|---:|---|---|
| 1 | PullbackReclaimV2 BTCUSDT 15m | +75.99% | 2.50 | 12.47% | 31 | PASS | Rejected: under 40 trades and reproducibility not checked. |
| 2 | RangeExpansionV2 BTCUSDT 15m | +59.28% | 1.75 | 22.64% | 36 | PASS | Later discarded after lead review. |
| 3 | RelativeStrengthV2 BTCUSDT 4h | +35.31% | 1.69 | 11.95% | 58 | PASS | Active baseline, not a replacement. |
| 4 | SimpleAtrTrendV2 BTCUSDT 15m | +125.54% | 1.57 | 34.54% | 107 | WARN | Rejected: drawdown too high. |
| 5 | FibPullbackContinuationV1 BTCUSDT 15m | +0.10% | 1.86 | 0.32% | 2 | FAIL | Too few trades. |
| 6 | BreakoutRetestV2 BTCUSDT 15m | 0.00% | 0.00 | 0.00% | 0 | NO_TRADES | No useful activity. |
| 7 | RelativeStrengthV2 BTCUSDT 15m | -100.00% | 0.82 | 100.00% | 1232 | FAIL | Not usable on 15m with tested params. |

Focused lead reviews:

| Lead | Verdict | Reason |
|---|---|---|
| PullbackReclaimV2 BTC 15m | Keep baseline | Active RS 4h baseline remained stronger after review. |
| RangeExpansionV2 BTC 15m | Discard lead | Lead activity failed with negative return. |

Conclusion: 15m may still contain something useful, but the last broad search did not prove a better tactic. The current best is still RS 4h.

## 11. What We Have Done Well

The project has several good research habits already:

- It separates raw return from practical eligibility.
- It tracks drawdown, profit factor, trade count, and warnings.
- It uses paper observation before promotion.
- It keeps real trading disabled.
- It has explicit promotion and paper enable gates.
- It records reports instead of relying on memory.
- It rejects high-return candidates when drawdown or reproducibility is weak.
- It can run long campaigns and focused follow-up reviews.

These are the right instincts.

## 12. Where The Current Search Is Weak

The biggest weakness is not that we failed to "copy the best strategy". The weakness is that brute-force parameter sweeps can easily reward noise.

Specific weaknesses:

1. Some campaigns sample huge grids rather than fully testing them.
2. Runtime budgets can stop before all symbols/strategies receive equal attention.
3. A high-return row can be overfit to one market regime.
4. 15m data is noisier and more fee/slippage sensitive than 4h.
5. Optimization grids can become too large without a good experiment design.
6. Paper observation is still young; open unrealized PnL is not enough evidence.
7. We need more systematic out-of-sample, walk-forward, and stress ranking before paper.

## 13. If I Built This Alone: Correct Strategy Search Method

If I were building this app from scratch to find profit-maximizing tactics, I would not begin by copying random "best strategies" from the internet. I would use outside ideas as hypotheses only. The edge must be proven inside our data, costs, fill model, market, and validation pipeline.

The correct way is a staged research funnel:

| Stage | Purpose | Output |
|---|---|---|
| 1. Define strategy hypotheses | Trend, breakout, pullback, mean reversion, volatility expansion, regime switching. | Human-readable strategy families. |
| 2. Build honest backtester | No lookahead, realistic fill model, fees, slippage, position sizing, stop logic. | Trusted simulation engine. |
| 3. Run broad but shallow search | Find which families produce activity and rough promise. | Raw candidates. |
| 4. Run constrained parameter refinement | Search only reasonable ranges around promising families. | Candidate clusters, not one magic row. |
| 5. Test robustness | Neighbor params, fee/slippage stress, random reruns, data slices, symbol/timeframe transfer. | Robust or rejected. |
| 6. Walk-forward validation | Train on older data, test on newer unseen data repeatedly. | Out-of-sample evidence. |
| 7. Regime analysis | Separate bull, bear, sideways, high-volatility, low-volatility periods. | Know when strategy works/fails. |
| 8. Paper observation | Let real-time paper state collect forward evidence. | Forward test record. |
| 9. Manual promotion review | Human checks risk, behavior, failure modes. | Paper continuation or rejection. |
| 10. Very small live test, if ever | Only after enough paper evidence, no leverage, strict kill switch. | Real-market execution evidence. |

The most important rule: optimize for robustness first, profit second. A slightly lower-return strategy that survives neighboring parameters, different periods, and higher costs is better than a beautiful top row that only works in one exact backtest.

## 14. What "Best Strategy" Should Mean

The app should not rank by return alone. A useful score should combine:

- Return.
- Max drawdown.
- Profit factor.
- Trade count.
- Average trade expectancy.
- Exposure time.
- Worst month or worst fold.
- Cost sensitivity.
- Parameter stability.
- Regime dependency.
- Time since last good performance.
- Live paper behavior.

My preferred selection rule:

1. Reject anything with catastrophic drawdown.
2. Reject anything with too few trades.
3. Reject anything that fails realistic fee/slippage stress.
4. Reject anything where neighboring parameters collapse.
5. Reject anything with poor walk-forward results.
6. Only then rank remaining candidates by return adjusted for drawdown and consistency.

This is slower, but it is the only way to reduce self-deception.

## 15. Should We Copy From The Best?

We should learn from known strategy types, but not blindly copy.

Good things to copy:

- Strategy categories: trend following, breakout, pullback continuation, mean reversion, volatility expansion.
- Risk concepts: ATR stops, volatility sizing, max drawdown limits, stop rules.
- Validation practices: walk-forward testing, out-of-sample testing, cost stress, paper trading.

Bad things to copy:

- Exact public parameter sets.
- Strategies advertised by return screenshots.
- "Works on all markets" claims.
- High-leverage examples.
- Strategies without fees, slippage, and realistic fills.

So the answer is: yes, study the best ideas, but make our own evidence. The market does not pay for copied labels. It pays, if at all, for a persistent edge after costs and mistakes.

## 16. Best Next Steps

My recommended next plan:

1. Let current RS 4h paper positions finish.
2. Compare the three RS variants only after realized exits, not while the trade is open.
3. Add a stronger candidate lifecycle GUI panel so raw, robust, paper, and rejected candidates are visually separated.
4. Run a targeted RS robustness drilldown around the current best 4h parameters:
   - `rsLookback`: 30, 33, 36, 39, 42, 45.
   - `rsThreshold`: -0.04, -0.035, -0.03, -0.025, -0.02.
   - `rsiMin`: 52, 54, 55, 56, 58.
   - `atrMultiplier`: 2.4, 2.6, 2.8, 3.0, 3.2.
   - `riskPct`: 0.02 and 0.03 for paper suitability.
5. Score clusters, not single rows. If many neighboring params work, we trust it more.
6. Run a separate 15m targeted campaign only for PullbackReclaimV2 and SimpleAtrTrendV2 with risk reduced to 0.02-0.03 and hard DD gates.
7. Keep EmaBounceV2 out unless a future version changes the formula materially.

## 17. Bottom Line

The app is heading in the correct direction. The current best research result is not a 15m scalper; it is the RS BTCUSDT 4h bullish-only strategy at paper risk 0.03.

The most important improvement is to make the search more robust and less fascinated by top-line return. The next version of the research system should treat every "great result" as guilty until it survives neighboring parameters, unseen time windows, higher costs, drawdown limits, and live paper observation.
