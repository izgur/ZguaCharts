# Chart Results Audit - 2026-10-04

## Findings From Saved History

The screenshots show different runs under the same shortened market label.
Reading the retained history identifies these BTCUSDT 1h RelativeStrengthV2
groups: 42 Bybit 84d runs with 35 trades, 12 Binance Spot 84d runs with 31
trades, nine Binance Spot 13d runs with five trades, four Binance futures 84d
runs with 31 trades, and three Bybit Spot 84d runs with 32 trades. These are
observations at audit time, not independent validation samples.

The five-trade entries therefore are genuinely shorter Binance Spot runs,
not the full Bybit 84-day test randomly losing 30 trades. The earlier chart
refresh implementation could shorten history and then use a 300-bar minimum
for a strategy preview. 300 hourly bars produce a requested 13d period. This
matches the old records but exact original request provenance was not stored.

The old history display omitted source and execution timeframe, summed
overlapping returns, and saved periodic chart previews as manual runs. Those
behaviors obscured comparisons. Old records are preserved, not rewritten.
Missing execution/coverage metadata is now explicitly shown as not saved.
New manual records retain source, execution timeframe, candle range/count,
and whether a final End-of-data exit is included. Chart refreshes no longer
save manual history and cannot run before candles finish loading.

## What the Percentage Means

RelativeStrengthV2 uses the risk-sized regime backtest engine:

    units = current_cash * risk_fraction / ATR_stop_distance
    trade_account_percent = net_trade_PnL / initial_account_equity * 100
    realized_account_percent = sum(completed_trade_account_percent)

Sizing applies the same calculation to winners and losers. It is not a rule
that multiplies only profitable trades by five. The account exposure versus
the underlying price move varies with the ATR stop, equity and size caps.
This can imply modeled exposure greater than cash where the chosen backtest
parameters allow it; it does not enable live leverage or change paper sizing.

Summing these initial-equity contributions is correct for realized account
P&L. Multiplying them as successive percentage returns would be incorrect.
Position sizing evolves with equity inside the engine. The prior tooltip
"compounded" was too ambiguous and has been replaced by the exact definition.

The chart sidebar excludes End-of-data exits; full backtests include a final
forced close. That explains some differences in trade count and return even
when the source/window otherwise match. The 77d sidebar span is the time from
first completed entry to last completed exit, not the requested 84d history.

Current chart previews explicitly request zero fee and zero slippage. The
display now makes this visible. A screenshot showing +62.58% is a modeled
historical realized result, not verified net real-world performance and not
evidence of future profitability. Its 30-trade sample is not a substitute for
separate costed training, validation and untouched holdout results.

## Research Status

Verified active saved identities: paper multi watcher PID 18740, order-flow
collector PID 18288, and 15-minute research campaign PID 4148. The latter's
55-job strategy list includes RelativeStrengthV2; its latest campaign log was
working on BreakoutRetestV2 for ETHUSDT. The legacy learning-runner settings
have enabled=false and schedule.enabled=false. Chart refreshes update a
preview, not strategy parameters. No candidate states, live-trading flags or
leverage settings were changed in this GUI/audit task.
The configured primary paper candidate is RelativeStrengthV2 BTCUSDT 4h.

## Verification Scope

Source formulas were inspected in core/backtest/regimeTrend.js. Regression
coverage verifies history metadata, zero auto-saves, partial-history guards,
legacy unknown fields and removal of misleading summed-return UI. This audit
does not certify all strategy results or eliminate possible historical-data,
execution-model or strategy bugs. A dedicated reproducibility and costed
out-of-sample audit remains necessary before treating a tactic as validated.
