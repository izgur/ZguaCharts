# Tactic-specific entry readiness

Status: experimental rule-based v1 activated on October 8, 2026. Header badges
and candle-click details use the selected supported tactic's shared entry rules,
closed-bar data, freshness, execution coverage and modeled costs. Personal tactics
remain isolated in their existing worker. This is not a probability model.
No live trading, paper candidate configuration or automatic promotion is changed.

V1 intentionally shows a number only for eligible LONG/SHORT entries (90-100);
WAIT/LATE/HOLD/EXIT/N/A are named states, not fabricated numeric confidence.
The score starts at 100 and discounts the modeled cost-to-stop ratio and trigger
drift, bounded below at 90 after every mandatory gate passes. These are fixed,
experimental UI rules, not learned weights or validated odds. Full statistical
calibration and the promotion workflow below remain future work.

Supported built-ins: RelativeStrengthV2, EmaBounceV2, SimpleAtrTrendV2,
PullbackReclaimV2, BreakoutRetestV2, RelativeStrengthFreshV3. Other built-ins
explicitly report unavailable rather than falling back to generic TA.
The 600-bar hypothetical position context is not the user's account position.
Funding is excluded from the displayed 0.15% round-trip fee/slippage model.

## What the user will see

One compact decision per signal timeframe, using the selected chart symbol,
exchange/product and tactic: `1h LONG 94`, `15m WAIT 72`, `4h LATE 58`.
The number is **entry readiness out of 100**, not profit probability. The hover
shows tactic/version, closed-bar timestamp, execution timeframe, passing checks,
blockers, setup age, distance from trigger/stop, modeled costs and validation
status. An actionable alert must carry that same identity and timestamp.

90-100: an eligible, fresh entry; all mandatory tactic and execution gates pass.
70-89: promising setup, but still waiting for a named condition; not an entry.
Below 70: no qualifying setup. LATE means the original entry was missed, even
if the underlying trend remains strong. HOLD/EXIT describe an existing position,
not a new entry. Missing/stale data means UNAVAILABLE, never zero or GO.

These bands are initial UI semantics, not empirically proven thresholds. The
score weights and timing limits must be fitted and frozen before validation.
100 means all modeled requirements pass, never certainty or a perfect future
price. A low LONG readiness does not imply a SHORT: each direction needs its
own supported entry rules. Long-only tactics cannot emit actionable shorts.

## Existing pieces and missing pieces

The current BTC 4h/1h/15m badges use generic technical scoring, independent of
the chosen tactic. Their percentage is not a calibrated probability.
RelativeStrengthV2's actual entry checks are in `core/backtest/regimeTrend.js`:
base V2 blockers, optional indicator filters, benchmark-relative performance,
RSI limits and price above EMA20. Those rules, not an average of every indicator
in the picker, should be the initial signal source.

The existing execution context uses lower-timeframe candles for fills and
intrabar stops/targets, plus configured soft exits. It does NOT establish that
an entry has a 90% probability of success. Reuse its fill/risk checks; introduce
any new 5-minute timing filter as an explicit, versioned tactic variant and
test it separately. Do not silently change historical RelativeStrengthV2.

The October 7 flow monitor reports no qualifying passive-flow variant and no
sample-ready descriptive variant positive in both splits after its cost hurdle.
That does not evaluate every chart tactic, but it also does not establish a
validated overall winner. Repeated overlapping zero-cost chart backtests are
not independent evidence and their returns must not be summed as performance.

## Implementation order

1. **Shared decision evaluator.** Extract a read-only evaluation interface from
   the actual backtest entry functions. Return each prerequisite and its margin,
   the passed/blocked decision and trade-state blockers without placing orders.
   Replay, backtests, chart badges and alerts use the same evaluator. Include
   warmup, cooldown, open-position state, source availability and parameters.
   Native tactics first; personal tactics need a versioned diagnostic contract
   in their existing isolated worker, not arbitrary server-side execution.
2. **Freshness and execution gates.** Evaluate 4h/1h/15m using only candles
   closed as of the decision timestamp, with closed 5m execution candles where
   available. Track the first eligible trigger, elapsed bars, ATR-normalized
   drift, invalidation, stop distance, target potential and modeled costs.
   Passing an old trend condition is not a new trigger. Store latest candidates
   in separate read-only signal state; preserve running paper candidate state.
3. **Readiness and UI.** Define small interpretable groups: tactic prerequisites,
   fresh trigger, execution confirmation and risk/cost feasibility. Derive
   weights from training experiments, not attractive percentages. Any mandatory
   failure caps readiness below 90; stale or unsupported data disables it.
   Replace the generic BTC strip with selected-symbol, selected-tactic decisions.
   Reuse candle-click history and threshold-crossing alerts; debounce/deduplicate
   alerts by symbol, timeframe, tactic version and trigger. Mark unvalidated
   tactics as experimental and suppress GO notifications until promotion.
4. **Validate and promote.** Define outcomes before optimizing: default is a
   positive net realized return under the tactic's frozen exit/stop/timeout
   rules and realistic next-executable fill, at 1x exposure. Evaluate other
   targets such as target-before-stop separately; do not mix them into a single
   success percentage. Compare readiness filters against the original tactic,
   always-long/buy-and-hold references and a no-skill base rate after costs.
   Rank by robust out-of-sample expectancy, drawdown, sample count and stability,
   not the largest training return. Automatic "best tactic" selection, if later
   added, must select using past data only and remain frozen for each test fold.

## Probability is a separate, later measurement

Record every decision opportunity, including waits, blocked signals and missed
entries, not just executed winning trades. Preserve as-of features, model/tactic
version, market regime, hypothetical execution outcome, adverse/favorable
excursion, holding time, fees/slippage and funding for perpetuals where needed.
Use nonoverlapping chronological training, tuning, calibration and untouched
final-test blocks. Purge samples whose outcome windows cross a split boundary;
apply an embargo at least as long as the maximum outcome horizon. Do not shuffle
time-series samples or repeatedly tune on the final holdout.

Start with a simple interpretable classifier only if it beats the base rate.
Calibrate on a separate later block, then inspect reliability by score bin and
regime, alongside Brier score, log loss and net expectancy. Correlated indicators
are not independent votes. Separate long/short and holding horizons rather than
assuming short probability equals one minus long probability (flat moves and
costs matter).

Proposed research gates, to be revised and frozen before testing: at least 200
independent eligible outcomes across multiple regimes, at least 50 in each
reported high-score validation bin, and independent walk-forward/final-test
confirmation. Counts alone never certify calibration; report confidence
intervals and effective sample size for clustered trades. If evidence for a
90% success estimate is absent, do not display it. The clean primary decision
can remain LONG 94 readiness even when calibrated net-profit probability is
much lower or unavailable. Show probability and its defined horizon only in
details once adequately supported; never claim a 100% financial prediction.

## Acceptance tests

- Prefix/replay invariance and correct higher-timeframe close alignment.
- No GO before a mandatory entry gate, no duplicate GO on a stale setup.
- LONG/SHORT independent; unsupported shorts clearly unavailable.
- Costs, gaps and ambiguous stop/target bars follow the shared simulator's
  conservative execution assumptions; missing execution bars are disclosed.
- Signal and backtest entry decisions agree for identical as-of inputs.
- Paper shadow evaluation does not modify existing candidate or order state.
- No model automatically promoted based on overlapping preview/history runs.
- Mobile/desktop badge, historical popup and alert messages expose the same
  tactic identity, source, timestamp, freshness and readiness semantics.

References:
- [Probability calibration](https://scikit-learn.org/stable/modules/calibration.html)
- [Time-series splits](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)
- Local engine: core/backtest/regimeTrend.js; reports/research-campaigns/flow-monitor-20261007.md.
