# ZguaCharts

ZguaCharts is a local trading dashboard built with Flask, plain HTML/CSS/JavaScript, and TradingView Lightweight Charts. It runs on your machine, uses public/no-key data sources by default, and provides multi-chart monitoring, technical indicators, signal scoring, and simple strategy backtesting.

The app is intentionally modular. Browser charting remains Flask + plain JavaScript, while automated research now runs through a reusable Node core under `/core`. The Flask UI calls the same shared engine used by the CLI, so optimization and manual chart backtests do not drift into separate logic.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the frontend/backend/core separation rules. In short: the browser renders API payloads, while indicators, signal scoring, strategy rules, rankings, and backtest metrics stay in backend/core modules.

## What It Does

- Displays 1, 2, 4, 6, or 8 chart panes in a responsive split-screen grid.
- Remembers the selected chart count and per-pane settings in `localStorage`.
- Lets each pane independently choose data source, symbol, timeframe, strategy preset, indicators, signal markers, and backtests.
- Streams live crypto candles from Bybit or Hyperliquid websockets.
- Bridges Indian stock data through Flask with `yfinance`.
- Renders TradingView Lightweight Charts without React or a heavy frontend framework.
- Shows live price ticker badges that flash green/red on price changes.
- Provides overlay and lower-pane technical indicators.
- Computes technical-analysis signal scores from `-100` to `+100`.
- Shows chart badges such as `STRONG BUY`, `BUY`, `NEUTRAL`, `SELL`, and `STRONG SELL`.
- Optionally plots signal BUY/SELL markers.
- Runs simple local long-only backtests and plots backtest trade markers.
- Shows diagnostics so you can see whether the requested data period was actually returned.
- Runs UI-independent research from the command line with `npm run backtest` and `npm run optimize`.
- Exports optimizer reports as CSV and JSON.

Signals and backtests are technical-analysis research tools only. They do not place trades and are not financial advice.

## Run Locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

The chart library is also vendored in `static/lightweight-charts.standalone.production.js`, so the browser does not need to wait on a CDN before rendering charts.

## Deploy On Render

ZguaCharts is still designed for local research first, but the Flask dashboard can run on Render as a web service. The app uses Python for Flask/yfinance and Node for the shared backtest engine, so the Render build installs both Python packages and the small Node package metadata.

### Recommended Render Settings

Create a new Render Web Service from the GitHub repository and use:

```text
Language: Python 3
Build Command: pip install -r requirements.txt && npm install
Start Command: gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 180
Health Check Path: /healthz
```

Environment variables:

```text
PYTHON_VERSION=3.11.9
NODE_VERSION=20
ZGUA_QUIET=1
```

The repository also includes `render.yaml`, so you can deploy it as a Render Blueprint instead of typing the commands manually.

`pandas-ta` is intentionally not required on Render. The backend indicators use pure pandas/numpy fallbacks, which avoids deploy failures when beta `pandas-ta` wheels are unavailable for the Render Python environment.

### Docker Fallback

If Render's native Python environment does not provide Node for the CLI backtest bridge, deploy with the included `Dockerfile`. It installs Python, Node, Flask dependencies, and starts Gunicorn on Render's assigned port.

## Data Sources

Default sources:

- `bybit`: crypto history through Bybit V5 REST, live candles through `wss://stream.bybit.com/v5/public/linear`.
- `hyperliquid`: optional crypto history and live websocket candles.
- `yfinance`: Indian stock history through the Flask backend.

No API keys are required for the default sources.

### Add Another Broker

Add one fetch function in `data_source.py` that returns a list of dictionaries shaped like:

```python
{
    "time": 1710000000,
    "open": 100.0,
    "high": 105.0,
    "low": 99.0,
    "close": 103.0,
    "volume": 12345.0,
}
```

Then register it in:

- `PROVIDERS`
- `DATA_SOURCE_CONFIG`

All indicators, signals, and backtests consume that shared OHLCV shape.

## Dashboard Features

### Chart Grid

The top selector supports:

- `1`: full screen
- `2`: side-by-side
- `4`: 2x2
- `6`: 3x2
- `8`: 4x2

The selected layout is persisted in the browser.

### Per-Pane Controls

Each chart pane has:

- data source selector
- symbol selector
- timeframe selector
- strategy preset selector
- Backtest button
- Indicators menu
- signal badge
- live ticker bar

Each pane can be configured independently.

## Indicators

Endpoint:

```text
GET /api/indicators?source=bybit&symbol=BTCUSDT&timeframe=5m&indicators=ema,rsi,macd,bbands,atr,supertrend,vwap
```

Response shape:

```json
{
  "overlays": [],
  "panes": []
}
```

Implemented indicators:

- EMA: 9, 21, 50, 200
- SMA: configurable period, default 20
- VWAP
- Bollinger Bands: 20 period, 2 stddev
- RSI 14
- MACD 12/26/9 with histogram
- ATR 14
- Supertrend 10/3
- Stochastic RSI
- Volume moving average 20

Overlay indicators render on the candle chart. RSI, MACD, ATR, Stochastic RSI, and volume render in lower panes.

## Signal Scoring

Endpoint:

```text
GET /api/signals?source=bybit&symbol=BTCUSDT&timeframe=5m
```

Signal scores range from `-100` to `+100`.

The score uses:

- EMA trend: EMA50 above/below EMA200
- EMA short momentum: EMA9 above/below EMA21
- Supertrend direction
- RSI above/below 50 plus overbought/oversold handling
- MACD line vs signal line
- price vs VWAP
- Bollinger Band squeeze/expansion
- volume spike above volume MA20
- ATR-based volatility warning

Badge labels:

- `STRONG BUY`
- `BUY`
- `NEUTRAL`
- `SELL`
- `STRONG SELL`

Signal markers:

- BUY when score crosses above `+60`
- SELL when score crosses below `-60`

Signal marker visibility is persisted per pane.

To tune scoring weights, edit:

```text
signals.py
```

## Backtesting

The reusable research engine lives in:

```text
core/backtest
core/strategies
core/indicators
core/optimizer
core/data
core/reporting
```

Its programmatic entry point is:

```js
runBacktest({
  symbol,
  interval,
  from,
  to,
  strategy,
  params
})
```

It runs without browser interaction and returns deterministic JSON:

```json
{
  "totalReturn": 0,
  "trades": 0,
  "winRate": 0,
  "averageWin": 0,
  "averageLoss": 0,
  "maxDrawdown": 0,
  "profitFactor": 0,
  "sharpeRatio": 0,
  "avgBarsHeld": 0,
  "equityCurve": [],
  "tradeList": []
}
```

The Flask `/api/backtest` route fetches candles through existing Python broker adapters, then calls this same Node engine. The browser only visualizes returned results and markers.

Endpoint:

```text
GET /api/backtest?source=bybit&symbol=BTCUSDT&timeframe=15m&period=60d
```

Optional query params:

```text
preset=conservative_trend
fee_pct=0
slippage_pct=0
```

The backtester is local and simple by design. It simulates long-only trades, does not connect to a broker, and never places trades.

### Default Strategy

Default preset:

```text
Conservative Trend
```

The Conservative Trend preset uses:

- warmup of 250 candles
- score confirmation above `+70`
- EMA50 > EMA200
- close > VWAP
- MACD line > MACD signal
- RSI between 50 and 68
- volume > volume MA20 * 1.1
- ATR percent filter
- minimum hold before score/EMA/MACD exits
- cooldown after exits
- ATR stop
- ATR take profit
- trailing ATR stop

### Strategy Presets

Available presets:

- Conservative Trend
- Momentum Scalping
- Pullback Trend
- Mean Reversion
- Current Original Strategy

To tune strategy presets, filters, entries, exits, warmup, cooldown, or trailing stops, edit:

```text
strategy.py
```

### Backtest Results

The modal returns:

- total return %
- number of trades
- win rate
- average win
- average loss
- max drawdown
- profit factor
- trade list
- buy/sell chart markers

Diagnostics include:

- actual first candle date
- actual last candle date
- number of candles loaded
- timeframe
- requested period
- effective source period
- actual days returned
- warmup candles skipped
- warmup percentage
- average ATR %
- average volume
- trades per day
- average bars held
- backtest reliability: LOW, MEDIUM, or HIGH
- fee percent per side
- slippage percent per side
- raw latest score
- smoothed latest score
- source period warnings
- skipped trade reason counts

Skipped trade reasons include:

- trend filter failed
- VWAP filter failed
- RSI filter failed
- volume filter failed
- ATR filter failed
- chasing filter failed
- pullback filter failed
- candle confirmation failed
- Bollinger filter failed
- cooldown active
- warmup active
- confirmation missing

The Backtest modal includes:

- preset selection
- recommended timeframe for the selected preset
- candle limit selection
- fee and slippage inputs
- optional short-side toggle, disabled by default in normal use
- `Run Backtest` for one preset
- `Test presets` to compare all presets on the same pane

Preset comparison shows return, trades, win rate, max drawdown, profit factor, and average bars held, with best return and best profit factor highlighted.

## CLI Research

Install Python dependencies for the dashboard as usual. The Node research core is dependency-free, so no `npm install` is required for the current scripts.

Run one backtest:

```powershell
npm run backtest -- --symbol BTCUSDT --interval 1h --days 365 --strategy ConservativeTrend
```

Validate the engine mechanically with the deliberate test strategy:

```powershell
npm run backtest -- --symbol BTCUSDT --interval 1h --days 7 --strategy AlwaysLongTest --limit 300 --debug
```

Debug Conservative Trend entries:

```powershell
npm run backtest -- --symbol BTCUSDT --interval 1h --days 60 --strategy ConservativeTrend --limit 1000 --debug
```

Prove the trend framework can generate trades with relaxed gates:

```powershell
npm run backtest -- --symbol BTCUSDT --interval 1h --days 60 --strategy ConservativeTrendLoose --limit 1000 --debug
```

Expected validation result:

- `trades > 0`
- `tradeList` is not empty
- `equityCurve` is not empty
- `reports/debug-last-backtest.json` is written

Run with explicit params:

```powershell
npm run backtest -- --symbol BTCUSDT --interval 15m --days 90 --strategy MomentumScalping --params "{\"rsiMin\":48,\"rsiMax\":70,\"stopAtr\":1.8}"
```

Run optimization:

```powershell
npm run optimize -- --symbol BTCUSDT --interval 1h --days 365 --strategy ConservativeTrend --output reports
```

Run optimization with a custom grid:

```powershell
npm run optimize -- --symbol BTCUSDT --interval 1h --days 365 --strategy ConservativeTrend --ranges "{\"emaFast\":[10,20,30],\"emaSlow\":[50,100,200],\"rsiMin\":[45,50,55],\"rsiMax\":[60,68]}"
```

Optimizer outputs:

```text
reports/optimization-results.csv
reports/optimization-results.json
reports/ranked-summary.json
```

CSV columns:

```text
symbol, interval, strategy, params, totalReturn, maxDrawdown, profitFactor, winRate, trades, sharpeRatio
```

The optimizer uses a default 70/30 train/test split. Ranking is based on training performance, and each row includes unseen test metrics for out-of-sample validation.

The ranked summary also includes a simple walk-forward section for the best parameter set. Each fold expands the training window and tests on the next unseen segment, which gives a quick stability check before you trust an optimized result.

If every tested parameter combination produces zero trades, optimization now stops early and tells you to run a debug backtest first.

The summary highlights:

- best by profit factor
- best by Sharpe ratio
- best by drawdown-adjusted return

## Strategy Research and Paper Simulation

This project includes a strategy research lab and a forward paper-simulation layer. These tools are for local research only. They are not financial advice, they do not make any profitability claim, and they do not place real orders. There is no exchange order execution, no broker account connection, and no API-key trading path.

The current audited research candidate is:

```text
strategy: SimpleAtrTrendV2
regimeMode: looseBtcBull
fillModel: next-open
```

The candidate config lives at:

```text
config/paper-candidate.json
```

Paper simulation is disabled by default:

```json
{ "enabled": false }
```

### Paper Simulation Commands

Initialize baselines without importing historical trades:

```powershell
npm run paper:init -- --config config/paper-candidate.json
```

Refresh latest Bybit candles and write freshness diagnostics:

```powershell
npm run paper:refresh -- --config config/paper-candidate.json
```

Check current paper status:

```powershell
npm run paper:status -- --config config/paper-candidate.json
```

Enable simulated paper processing after initialization:

```powershell
npm run paper:enable -- --config config/paper-candidate.json
```

Run one safe forward paper tick:

```powershell
npm run paper:tick -- --config config/paper-candidate.json --refresh-first
```

Disable paper simulation:

```powershell
npm run paper:disable -- --config config/paper-candidate.json
```

Dry-run mode previews processing without mutating state or appending journals:

```powershell
npm run paper:tick -- --config config/paper-candidate.json --dry-run --refresh-first
```

Paper state and outputs:

```text
data/paper-state.json
reports/paper-status.json
reports/paper-summary.json
reports/paper-freshness.json
reports/paper-journal.csv
reports/paper-journal.jsonl
```

The journal files are local generated outputs and are ignored by git through the `reports/` ignore rule.

### Research Commands

```powershell
npm run backtest
npm run optimize
npm run strategy-lab
npm run strategy-lab:v2
npm run optimize:v2
npm run validate:candidate
npm run test:paper
npm run test:research
npm run test:regime
npm run test:cli-exit
npm run test:overlays
```

## Strategy Plugins

Strategies register themselves in `core/strategies/index.js`:

```js
registerStrategy({
  name: "ConservativeTrend",
  params: {},
  entry(ctx) {},
  exit(ctx) {},
  risk(ctx) {}
})
```

This shape is intentionally small so future modules can add multi-symbol testing, portfolio simulation, Monte Carlo analysis, paper trading, or live trading integration without rewriting the simulator.

`AlwaysLongTest` is included only to validate the engine. It enters on candle 10, exits on candle 30, and repeats every 50 candles. Do not use it for trading research.

## API Endpoints

### App

```text
GET /
```

Serves the dashboard.

### Config

```text
GET /api/config
```

Returns available sources, symbols, timeframes, indicators, strategy presets, and defaults.

### Candles

```text
GET /api/candles?source=bybit&symbol=BTCUSDT&timeframe=1m&limit=240
```

Returns OHLCV candles.

### Indicators

```text
GET /api/indicators?source=bybit&symbol=BTCUSDT&timeframe=1m&indicators=ema,rsi,macd
```

Returns Lightweight Charts-ready overlays and lower panes.

### Signals

```text
GET /api/signals?source=bybit&symbol=BTCUSDT&timeframe=1m
```

Returns current signal score, label, tone, scoring components, warnings, and optional signal markers.

### Backtest

```text
GET /api/backtest?source=bybit&symbol=BTCUSDT&timeframe=15m&period=60d&preset=conservative_trend
```

Returns summary stats, diagnostics, trades, skipped reasons, and trade markers.

## Project Structure

```text
app.py                         Flask routes
data_source.py                 Broker/source adapters
indicators.py                  Indicator calculations
signals.py                     Signal scoring
strategy.py                    Backtest strategy presets and rules
backtest.py                    Legacy Python backtest kept for reference/fallback
core/backtest                  Shared Node backtest engine
core/strategies                Plug-in strategy registry
core/indicators                Dependency-free indicator engine for research
core/optimizer                 Grid search, train/test split, ranking
core/data                      Node candle loading and cache
core/reporting                 CSV/JSON exports
cli/backtest.js                npm run backtest entry point
cli/optimize.js                npm run optimize entry point
templates/index.html           Dashboard HTML
static/app.js                  Plain JavaScript UI
static/styles.css              Dashboard styling
static/lightweight-charts...   Vendored Lightweight Charts
requirements.txt               Python dependencies
package.json                   Node CLI scripts
```

## Notes and Limitations

- yfinance intraday history can be limited. The backtest diagnostics show how many days were actually returned.
- Bybit history uses paginated `limit=1000` requests with caching and throttling.
- Backtests prefer candle count over requested days. The default endpoint limit is 5000 candles when the source supports it.
- Backtests are simplified research simulations. They do not include market depth, real spreads, partial fills, exchange outages, borrow costs, or broker-specific order rules.
- Fees and slippage default to `0` but are exposed in backtest results and API parameters.

## Developer Checks

```powershell
python -m py_compile app.py data_source.py indicators.py signals.py strategy.py backtest.py
python -m unittest discover -s tests
npm run test:research
```

## Safety

ZguaCharts does not place trades. Signal badges, markers, indicators, and backtests are technical-analysis hints and research tools, not financial advice.
