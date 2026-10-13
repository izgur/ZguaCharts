# FreshV3 Audit

The Bybit Flask bridge relies on the CLI to supply BTC 4h regime data. FreshV3
was absent from the CLI's regime-runner list, leaving its benchmark/trend inputs
missing and producing zero trades. The CLI now recognizes it and sizes the BTC
history request for the actual signal interval plus warmup.

Personal/source-editor FreshV3 backtests also omitted the mandatory 5m history.
They now request real closed execution bars and preserve the replay cutoff.
FreshV3 excludes unfinished signal/execution bars. Missing benchmark/execution
inputs or unsupported signal intervals produce explicit errors instead of silent
zero-trade runs. Its coverage, timing, freshness and cost blockers are registered
in diagnostics, alongside the actual strategy name and input counts.

## Entry Rules

The first qualifying RelativeStrengthV2 setup is required by default, with
maximum zero bars of age and 0.5 ATR upward drift. ATR stop distance must exceed
three times modeled round-trip costs. The entire signal interval needs continuous
closed 5m candles and the final 5m candle must close green. Entry fills at the next
open. Signal periods must exceed 5m and be divisible by 5m.
These restrictions are intentional, not a guarantee of predictive accuracy.

## Diagnostic Results

Recent 1h samples of 1,000 closed bars, with BTC regime and real 5m history,
fees 0.055%/side, slippage 0.02%/side and a 1x cap:

| Symbol | Original FreshV3 Trades | Net Return |
| --- | ---: | ---: |
| BTCUSDT | 42 | -2.74% |
| ETHUSDT | 31 | -3.97% |
| SOLUSDT | 22 | -0.85% |

Allowing two bars and 0.75 ATR drift helped SOL in this sample but did not make
all markets profitable. Defaults were not changed based on that comparison.
This is debugging/research, not an independent holdout or candidate promotion.
The dated full report is under `reports/research-campaigns` (ignored by Git).
Existing paper candidates, their states and RelativeStrengthV2 rules are unchanged.

## Guest Demo

Guests and Member accounts see one locked RelativeStrengthV2 tactic, including
visibility, read-only source, bias and bounded historical backtest controls.
Demo requests use fixed baseline rules and costs, a 1x cap, a 2,500-bar limit,
rate limiting and no writes to shared backtest history. Other tactic-source and
backtest endpoints remain role-restricted. Only Tactics/Admin accounts can select
other tactics or edit/save personal tactics. The account service itself remains
localhost-only; public account hosting is a separate deployment task.
