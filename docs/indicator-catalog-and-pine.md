# Indicator catalogue and Pine support

## Current implementation

The chart catalogue is native Python, not Pine Script. `indicators.py` owns the
registration, numeric input schemas, settings validation and conversion to chart
series. `indicator_catalog.py` defines additional standard OHLCV formulas using
NumPy and pandas. `/api/indicators` calculates the selected studies over exactly
the candles displayed on the chart. `static/app.js` draws those returned series
with Lightweight Charts and saves per-chart parameters in the account/layout.
Indicator settings do not change RelativeStrengthV2 or paper research formulas.

The catalogue now contains 113 indicators, including five source-reviewed Python
ports described in `docs/community-indicators.md`. Every entry has a View Python
source action in the picker and active chart legend. `/api/indicator-source/<id>`
returns a focused single-study module with its required helper functions;
`/api/indicator-source/<id>/download` exports that module, dependencies,
licence notices and a runnable JSON-candle wrapper. No arbitrary files or user
scripts are served or executed on the server. Personal copies execute in the
existing isolated browser Python worker. Unknown indicator IDs return 404.
Public-source review does not imply
all screenshot scripts have been ported; the community document lists gaps.

The new catalogue contains 42 studies: WMA, RMA, DEMA, TEMA, HMA, VWMA, ALMA,
LSMA, Donchian, Keltner, envelope, trailing regression channel, ADX, DMI,
Aroon, Aroon oscillator, CCI, OBV, MFI, CMF, accumulation/distribution,
Chaikin oscillator, ROC, momentum, stochastic, Williams %R, awesome oscillator,
balance of power, bull/bear power, Bollinger %B and bandwidth, TRIX, TSI,
ulcer index, vortex, Elder force index, historical volatility, CMO, PVT, PVO,
squeeze momentum and WaveTrend. Existing RSI, MACD, ATR, EMA, SMA, Bollinger,
VWAP, Supertrend, stochastic RSI, volume and Cipher-style studies are retained.

Each catalogue entry discloses its source, algorithm variant and editable inputs.
No claim of bit-for-bit parity with TradingView is made: seeding, rounding,
session calendars and feed candles can differ. New EMA-based studies use the
causal pandas exponential recursion with warmup masked; RMA uses an SMA seed.
Historical volatility's annualization is bars/year, not automatically days/year.
For an hourly 24/7 series, select 8760 rather than the default daily 365.
Donchian includes the current bar. Regression channels use trailing residual
population deviation. ALMA does not floor its Gaussian center. Squeeze is an
independent standard BB/KC implementation, not an exact LazyBear script port;
its Keltner calculation uses close EMA and Wilder ATR. WaveTrend is an independent
public-formula implementation, not a copied community script.

Warmup values remain null. Missing volume is never invented for volume studies.
Tests cover every study's registration, output, finite-or-null values, parameter
changes, flat/zero-volume data, reference arithmetic and prefix invariance
(future candles cannot change an already computed historical value). Replay
keeps the shared timestamp axis with whitespace, not future indicator values.
No automatic trading or changes to paper candidates are introduced.

## Latest expansion and remaining gaps

The October 7 expansion adds 55 studies, including displaced Ichimoku cloud,
Alligator, SAR, KAMA, Ultimate Oscillator, stops and confirmed swing tools.
See `docs/indicator-expansion-20261007.md` for the full scope, variants and gaps.
This is not every item in the supplied screenshots.
Open interest, actual trade delta/CVD, market breadth, correlation with a second
instrument, relative volume by session, seasonality and other session/calendar
studies need separate data or market/session definitions. Swing tools now use
explicit confirmation-time plotting rather than repainting earlier signals.
Protected community indicators cannot
be reconstructed exactly from screenshots. SMC/UT Alerts and the remaining
community Top scripts still require source/licence review and dedicated ports.

## Pine Script

Pasting a Pine script is not supported. No editor or partial evaluator has been
added that would misleadingly imply compatibility. Pine has its own series type
system, historical references, bar-by-bar execution and realtime rollback, plus
multi-timeframe requests, drawing objects and a separate strategy emulator.

A proper future integration needs a versioned compatibility scope and a vetted
runtime, isolated execution with time/memory/output limits, read-only market-data
access, blocked arbitrary host/network access, deterministic replay tests and
clear diagnostics for unsupported syntax. It must not accept code and execute
it via Python `eval` or browser `eval`. Porting a reviewed individual Pine formula
to the current Python catalogue is possible without pretending to run Pine.

Reference documentation:
- https://www.tradingview.com/pine-script-docs/language/execution-model/
- https://www.tradingview.com/support/solutions/43000589149-hull-moving-average/
- https://www.tradingview.com/support/solutions/43000502250-directional-movement-dmi/
- https://www.tradingview.com/support/solutions/43000502253-donchian-channels-dc/
