# Entry readiness v1

The chart header now evaluates the selected symbol/source/tactic on closed
4h, 1h and 15m candles. Hover or click a badge for the rule version, blockers,
indicator values, setup age, ATR drift, stop distance and modeled costs.
Candle-click history uses the same evaluator. Replay uses its visible-candle
close as the cutoff; no future candles enter the readiness calculation.

## States and limitations

- LONG / SHORT 90-100: actual entry rules pass, the trigger is fresh, warmup and
  execution coverage are present, and the stop distance exceeds three times the
  modeled round-trip costs. Neither side is inferred by complementing the other.
- WAIT: a named entry/risk prerequisite fails. No invented percentage is shown.
- LATE: the eligible trigger is older than the allowed bar count or has drifted
  more than the configured ATR limit. Defaults: zero elapsed bars, 0.5 ATR.
- HOLD / EXIT: state of a hypothetical 600-bar tactic simulation, not the user's
  real position. EXIT is an execution or pending signal exit, not a new short.
- N/A: unsupported tactic or missing, stale, incomplete or insufficient data.

This is an experimental rule-based readiness score, not estimated success odds.
It is not an instruction to execute a trade. All entry rules stay in the shared
engine; isolated browser workers run native source or a personal tactic copy.
Existing chart trade previews may still use zero costs and a different history
window. Readiness uses taker 0.055% and slippage 0.02% per side (0.15% round trip),
with a 1x cap. Funding is not included. Those differences are intentional and
must not be mistaken for a preview/backtest mismatch.

## Research variant

RelativeStrengthFreshV3 retains V2's shared exits and adds first-trigger age,
maximum ATR drift, cost-to-stop feasibility and a bullish last closed 5m candle
with complete 5m coverage. It is long-only and exploratory, not a replacement
for V2 or a promoted paper candidate. It appears in chart tactics, source copies
and the manual backtest builder. No running candidate or watcher is modified.

`cli/research_entry_readiness.js` compares V2 against nine frozen freshness/drift
combinations on a supplied candle dataset. Training is first 60%, validation
next 20%; the final 20% remains unevaluated. Choose parameters using training
only, then report that single choice in validation. Reports include modeled
costs, drawdown, sample counts, blocker counts and diagnostic coverage. Final
holdout eligibility requires positive train/validation returns, 150/50 completed
trades respectively and at least 95% diagnostic coverage in both splits. It
does not automatically run that holdout, promote candidates or enable orders.

The initial BTC/Bybit snapshot (1,500 1h candles) is saved under
reports/research-campaigns/entry-readiness-input-20261008.json with a dated
comparison report. V2 train/validation net returns were +9.79% / +2.91% (31/13
completed trades). The training-selected FreshV3 variant returned +12.16% /
-1.21% (8/5 completed trades). Execution coverage was only roughly 26-28%, so
these exploratory comparisons are incomplete-data diagnostics, not evidence
of a reliable winner. Holdout eligibility is false; no promotion occurred.

## Optimizations and verification

Sorted execution candle bounds use binary lookup instead of a full-history
scan per decision. Browser candle/source/results caches are bounded; failed
source requests can retry. Async generation guards discard responses after a
symbol, chart, tactic or replay cutoff changes. Superseded readiness workers are
terminated, preventing a backlog during fast replay. Custom workers retain their
timeout, network isolation and bounded result size.

Tests cover unchanged original backtest trades, prefix/replay invariance,
mandatory gates, independent directions, costs, warmup, execution coverage,
HOLD/EXIT, six source exports, training-only selection, untouched holdout,
historical details, badge-click explanations and desktop/mobile rendering.
The existing regime strategy smoke suite and tactic-source security tests pass.
