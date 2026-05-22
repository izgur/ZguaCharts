# ZguaCharts

ZguaCharts is a local trading dashboard built with Flask, plain HTML/CSS/JavaScript, and TradingView Lightweight Charts. It runs on your machine, uses public/no-key data sources by default, and provides multi-chart monitoring, technical indicators, signal scoring, and simple strategy backtesting.

The app is intentionally modular: broker/data integrations live in `data_source.py`, indicator calculations live in `indicators.py`, signal scoring lives in `signals.py`, strategy rules live in `strategy.py`, and backtest simulation lives in `backtest.py`.

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
backtest.py                    Backtest simulation and reporting
templates/index.html           Dashboard HTML
static/app.js                  Plain JavaScript UI
static/styles.css              Dashboard styling
static/lightweight-charts...   Vendored Lightweight Charts
requirements.txt               Python dependencies
```

## Notes and Limitations

- yfinance intraday history can be limited. The backtest diagnostics show how many days were actually returned.
- Bybit history currently uses the latest candle limit returned by the public endpoint, so a request like `period=60d` may return fewer actual days depending on timeframe and limit.
- Backtests prefer candle count over requested days. The default endpoint limit is 5000 candles when the source supports it.
- Backtests are simplified research simulations. They do not include market depth, real spreads, partial fills, exchange outages, borrow costs, or broker-specific order rules.
- Fees and slippage default to `0` but are exposed in backtest results and API parameters.

## Safety

ZguaCharts does not place trades. Signal badges, markers, indicators, and backtests are technical-analysis hints and research tools, not financial advice.
