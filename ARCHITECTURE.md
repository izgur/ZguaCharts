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
- New UI view: frontend may call APIs and render returned payloads only.

If the same trading formula appears in both frontend JavaScript and backend/core code, the frontend copy should be removed.
