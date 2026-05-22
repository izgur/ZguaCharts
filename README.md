# ZguaCharts

A local Flask dashboard for up to 8 live chart panes using TradingView Lightweight Charts.
The chart library is served from `static/` first, so the dashboard can open without waiting on a CDN.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000.

## Data sources

- Bybit crypto candles are loaded through the Flask backend for history, then updated in the browser with `wss://stream.bybit.com/v5/public/linear`.
- Hyperliquid remains available as an optional crypto source.
- Indian stocks use `yfinance` through Flask polling. No API key is required.

To add a broker, add a fetch function to `data_source.py`, register it in `PROVIDERS`, and add its symbols/timeframes in `DATA_SOURCE_CONFIG`.

## Indicators and Signals

- `/api/indicators` returns Lightweight Charts-ready overlays and lower panes.
- `/api/signals` returns a -100 to +100 technical-analysis score, badge label, components, warnings, and optional BUY/SELL marker candidates.
- `/api/backtest` runs a simple long-only signal backtest with optional ATR stop/take-profit exits and returns summary stats, trades, and chart markers.
- Edit `signals.py` to tune scoring weights and thresholds.
- Edit `strategy.py` to tune backtest presets, entry filters, exit logic, cooldowns, warmup, and trailing stops.
