# ZguaCharts Architecture

ZguaCharts keeps browser rendering separate from trading calculations. The UI can request and display data, but it must not duplicate indicator formulas, signal scoring, strategy rules, optimization rankings, or backtest metrics.

## Frontend / UI

Files:

- `templates/index.html`
- `static/app.js`
- `static/styles.css`

Responsibilities:

- Render layout, controls, charts, markers, modals, tables, diagnostics, and paper-simulation status.
- Manage UI state such as chart count, selected source, symbol, timeframe, indicators, signal marker visibility, and selected strategy preset.
- Call API routes such as `/api/config`, `/api/candles`, `/api/indicators`, `/api/signals`, `/api/backtest`, and `/api/strategy-ranking`.
- Render returned candles, indicator series, signal payloads, trade markers, backtest metrics, diagnostics, optimizer summaries, and strategy ranking rows.
- Maintain live websocket candle display for Bybit/Hyperliquid as transport and rendering only.

Frontend code must not calculate EMA, RSI, MACD, ATR, VWAP, Supertrend, Donchian channels, signal scores, strategy entries/exits, optimizer rankings, or backtest metrics.

## Flask Backend

Files:

- `app.py`
- `data_source.py`
- `indicators.py`
- `signals.py`
- `backtest.py`
- `strategy.py`

Responsibilities:

- Serve the dashboard and API routes.
- Fetch, cache, normalize, and bridge candle data from configured data sources.
- Calculate Python-side indicators and signal payloads.
- Provide JSON shaped for Lightweight Charts.
- Call the shared Node backtest engine for strategy research and UI backtests.
- Own `/api/strategy-ranking`, including matrix execution, metric collection, ranking score calculation, validity flags, and ranking cards.
- Own `/api/strategy-optimize`, including parameter search, train/test scoring, overfit warnings, and optimized candidate summaries.
- Own persistent research memory through `/api/research/runs`, `/api/research/best-candidate`, and `/api/research/suggest-candidate`.
- Own manual candidate promotion through `/api/candidate/promote`; browser actions may request promotion, but only the backend writes the ignored runtime config `config/local/paper-candidate.json` after explicit confirmation.
- Own candidate validation and paper enablement gates through `/api/candidate/validate`, `/api/candidate/enable-paper`, and `/api/candidate/disable-paper`.
- Own paper candidate health scoring and degradation detection through `/api/candidate/health`; the browser renders the returned status and expected-vs-paper metrics only.
- Own replacement suggestions through `/api/research/suggest-replacement`; the browser can ask for a suggestion and manually promote it, but it must not auto-promote or disable paper simulation.
- Own learning-cycle execution and reports through `/api/learning/*`; reports are recommendations only, promotion remains manual, and paper enablement remains manual.
- Own scheduled learning due checks and file-backed learning locks; local scheduling should call `python scripts/learning_tick.py` and only the backend decides whether a cycle is due.
- Own automatic learning v1 decisions. Auto-promotion may only write a paper candidate config with `enabled=false`; paper simulation enablement remains manual, auto-enable paper is intentionally blocked, and real trading is not implemented.
- Own automatic learning decision logs through `/api/learning/decisions` and `/api/learning/decision-summary`; every learning recommendation, scheduled tick, eligibility check, rejection, and auto-promotion must be auditable from backend-owned records.
- Own ops diagnostics through `/api/system/health` and `/api/system/health/quick`; health checks are read-only, never expose secrets, and must not mutate candidate config, paper state, research memory, or learning settings.
- Own market-data maintenance through `/api/market/*`; Bybit symbol validation, cache inspection, and historical prefetch are backend-owned and must never run automatically on app startup.

## Config Files

Tracked config files are defaults/templates only:

```text
config/learning-runner.default.json
config/paper-candidate.default.json
```

Runtime/user-modified config is ignored by Git:

```text
config/local/learning-runner.json
config/local/paper-candidate.json
```

The backend loads defaults first, overlays local runtime config when present, and writes only to `config/local/`. Candidate promotion, paper enable/disable, learning config changes, scheduler updates, and auto-promotion must never mutate tracked default files. Backups live under ignored `config/backups/`.

## Core Strategy / Research Engine

Folders:

- `core/backtest`
- `core/strategies`
- `core/indicators`
- `core/optimizer`
- `core/data`
- `core/reporting`
- `core/paper`

Responsibilities:

- Implement reusable backtest execution, trade auditing, strategy rules, optimizer runs, reporting, and paper-simulation logic.
- Produce deterministic JSON results for CLI tools, Flask API routes, and future automation.
- Own strategy ranking and validation decisions. Frontend analysis pages must display backend/core ranking payloads without recalculating scores.

## Change Rule

When adding a feature:

- New indicator formula: backend/core only.
- New signal scoring rule: backend/core only.
- New strategy, backtest, optimizer, paper simulation, or ranking rule: `core` or backend strategy modules only.
- New candidate selection or promotion behavior: backend endpoint only; frontend may render the candidate and ask for explicit user approval.
- New candidate validation, pass/warn/fail rule, or paper-enablement decision: backend/core only.
- New optimization range, scoring rule, or overfit penalty: backend/core only.
- New research-memory comparison or learning suggestion rule: backend/core only; frontend may only start runs, load records, and request explicit manual promotion.
- New paper-performance health rule, degradation threshold, or replacement suggestion rule: backend/core only.
- New learning-runner schedule, cycle step, candidate comparison, or recommendation rule: backend/core only.
- New auto-promotion rule or eligibility check: backend/core only, and it must not enable paper simulation or place trades.
- New automatic-learning decision record or audit summary: backend/core only; frontend may render the records but must not invent reasons, scores, checks, or outcomes.
- New operational diagnostic or health status: backend/core only; frontend may render PASS/WARN/FAIL checks and details returned by the API.
- New market-data validation, cache-status, or prefetch rule: backend/core only; frontend may trigger explicit actions and render returned status.
- New UI view: frontend may call APIs and render returned payloads only.

## Scheduled Learning

Run scheduled learning locally with:

```bash
python scripts/learning_tick.py
```

On Windows Task Scheduler, run that command every 15 or 60 minutes. The script reads
`config/learning-runner.default.json` plus ignored `config/local/learning-runner.json`,
runs only when due, writes recommendation reports, and never promotes candidates or enables paper simulation. On Render/free hosting, repeated
execution needs an external cron/ping or a paid background worker.

If the same trading formula appears in both frontend JavaScript and backend/core code, the frontend copy should be removed.
