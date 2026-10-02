# ZguaCharts Continuation, 2026-10-02

Active repository: `D:\Codex\Codex\2026-05-21\tradingviewkiller-please-initialize-the-project-and`.

## Runtime

- Flask: `http://127.0.0.1:5000/charts`, launched from this repository.
- Paper watcher: `scripts/start-paper-watch.ps1`, one hidden instance, five-minute interval.
- Order-flow collection: `scripts/start-flow-collector.ps1`, one hidden instance, seven-day duration.
- The daily Short-flow research monitor belongs to this D chat and uses these launchers.
- Runtime process records and candidate configuration remain local under `config/local/`.

## Correctness And Continuity

The copied paper runner omitted position and daily-loss caps when calling the engine. Both are now forwarded and covered by a paper-level regression test. Existing oversized positions were preserved for exit handling rather than resized or discarded. Before migration, candidate states and configuration were copied to `reports/paper-migration-20261002-232458/`.

RS and SimpleAtrTrend paper execution now persists engine cash, pending entry/exit, position, fees, stop/trailing activation, cooldown, and UTC daily realized PnL. Execution advances through new signal candles and new protective checks without reconstructing historical trades. Retained candle context preserves indicator warmup; indicator frames are cached and shared between sizing variants. Indicators are still recalculated when new signal context arrives, rather than incrementally updating every indicator formula. Context is bounded to 50,000 signal candles; reaching that bound requires explicit rebasing rather than silent state replacement.

Restart parity tests compare checkpoint state and completed trades with an uninterrupted run. Repeated candles do not change checkpoint state. Configuration changes or missing checkpoint context fail explicitly. Corrupt state is preserved and rejected rather than silently replaced with an empty account. Refreshed ticks refresh regime and execution data, and stale execution data blocks processing.

Flask subprocesses use CREATE_NO_WINDOW on Windows while preserving existing process-group flags. The watcher launchers check saved PID, executable path, and process start time before starting an instance. Legacy shortlist startup/health scripts delegate to the same watcher launcher.

## Candidate Decision

The final bounded seven-run study is `reports/research-campaigns/rs-followup-20261002-causal-clock.json`. The earlier same-day report is preserved but superseded by the timing-corrected report.

- Under the enforced 10% cap, the 1h risk 0.02 and 0.0175 variants have identical fills and sizes in the study. After its carried position closed, risk 0.0175 was disabled; its state and reports remain intact.
- Active candidates are the existing 4h RS reference and 1h risk 0.02 reference, both with 5m hard protective checks.
- The 1h policy retains trailing activation at +1R and trailing ATR multiplier 3.7. Soft exits remain disabled.
- Historical 1h diagnostics: 39 completed trades, +1.2334% net; double costs reduce this to +0.6477%. Six gross winners become net losers after costs.
- Historical 4h diagnostics: 11 completed trades, +1.1557% net.
- Moving 1h trailing activation to 1.5R does not improve the diagnostic result. No experimental policy was promoted.
- All data before both policy selection and the final engine clock correction (2026-10-02 21:34:48 UTC) is previously examined diagnostic data, not a fresh holdout. There are no completed new forward trades yet; evidence is insufficient for a new strategy decision.

The final timing review also corrected two inherited lookahead defects: signal-close stop tightening must happen after the signal candle's earlier execution checks, and 1h regime mapping cannot read a 4h close until that candle completes. Protective checks now include the first execution candle after entry. Regression tests cover these boundaries. Earlier execution-policy reports need reinterpretation under these corrections; no claim of untouched validation is made for their historical figures.

## Next Review

Observe new paper exits and collect fresh order-flow evidence. Keep retrospective diagnostics separate from forward evidence. Investigate cost-driven losses and stop exits before launching another broad optimizer. The completed 3m/5m/15m searches have no unfinished jobs to resume.

## Verification

Paper, incremental parity/timing, regime, research, overlay alignment, and CLI exit suites passed. All 172 Python tests passed. JavaScript, Python, and PowerShell syntax checks passed. Flask health and Charts returned HTTP 200. The final hidden watcher completed a clean two-candidate cycle, with no events, no warnings, and zero old signal candles reprocessed. The collector connected and appended fresh market-flow rows. Repeated startup checks under both Windows PowerShell and PowerShell 7 reused the existing processes.
