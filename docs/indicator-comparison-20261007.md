# Our indicators versus TradingView

Code-based assessment, 7 October 2026. All 58 indicators below are implemented.
None has a completed, identical-input, bar-by-bar TradingView parity certification.
Standard formula means the mathematical family is implemented, not that every
default, input, seed, missing-value rule, plot, alert or live-bar behaviour matches.
Source-reviewed ports refer to specific public script versions, not every script
with that name. Personal edited indicators are excluded because their code varies.

| Our indicator | Relationship to TradingView | Important qualification |
|---|---|---|
| EMA 9/21/50/200 | Standard formula; parity unverified | Optional pandas-ta versus pandas fallback can change initialization. |
| SMA | Standard formula; parity unverified | Close-based rolling average; source/options must match. |
| VWAP | Known different implementation | Cumulative loaded history; not TradingView's configurable session/period anchoring. |
| Bollinger Bands | Standard formula; parity unverified | Close SMA and population deviation; not full TradingView option set. |
| RSI | Known initialization/edge differences | Fallback recursively smooths gains/losses without SMA seeding; zero losses become NaN rather than a defined upper extreme. Optional library path differs. |
| MACD | Standard formula; parity unverified | EMA initialization inherited from our EMA; not all MA-type options. |
| ATR | Known initialization difference | Recursive alpha=1/length smoothing without explicit SMA seed. |
| Supertrend | Independent version; parity unverified | Our ATR seed and initial bullish state can affect trail and reversals. |
| Stochastic RSI | Standard formula; parity unverified | Inherits our RSI differences; zero stochastic range becomes missing. |
| Volume + MA20 | Standard formula; parity unverified | Actual exchange volume units, colours and smoothing options matter. |
| Cipher B-style Oscillator | Different, independent indicator | Public WaveTrend/estimated-flow/RSI combination; NOT proprietary Market Cipher B. |
| Weighted Moving Average | Standard formula; parity unverified | Linear weights on close. |
| Smoothed Moving Average | Standard formula; parity unverified | Wilder smoothing with SMA seed; compare warmup and missing values. |
| Double EMA | Standard formula; parity unverified | 2 EMA minus double-smoothed EMA; nested warmup handling matters. |
| Triple EMA | Standard formula; parity unverified | Triple exponential combination; nested warmup handling matters. |
| Hull Moving Average | Standard formula; parity unverified | Half-length and square-root rounding choices need comparison. |
| Volume-weighted Moving Average | Standard formula; parity unverified | Rolling close-times-volume divided by volume. |
| Arnaud Legoux Moving Average | Standard formula; parity unverified | Gaussian weights; offset/floor settings need exact comparison. |
| Least Squares Moving Average | Standard formula; parity unverified | Trailing regression endpoint; fixed zero offset. |
| Donchian Channels | Standard formula; parity unverified | Current bar included; offsets/options need matching. |
| Keltner Channels | Specific variant; parity unverified | Close EMA with Wilder ATR bands and separate ATR length. |
| Moving Average Envelope | Specific variant; parity unverified | Close SMA and fixed percentage bands. |
| Linear Regression Channel | Independent rolling version | Trailing fitted close trend with residual population-deviation bands. |
| Average Directional Index | Standard formula; parity unverified | Wilder smoothing; seed and zero-range conventions need comparison. |
| Directional Movement Index | Standard formula; parity unverified | +DI/-DI/ADX with Wilder smoothing. |
| Aroon | Standard formula; parity unverified | Length+1 window, latest equal high/low wins. |
| Aroon Oscillator | Standard formula; parity unverified | Aroon up minus down; inherits tie/window conventions. |
| Commodity Channel Index | Standard formula; parity unverified | HLC3, mean absolute deviation and 0.015 constant. |
| On-balance Volume | Standard family; parity unverified | Initial volume seed can create a constant offset. |
| Money Flow Index | Standard formula; parity unverified | HLC3 times volume; zero-flow cases need comparison. |
| Chaikin Money Flow | Standard formula; parity unverified | Rolling close-location volume ratio. |
| Accumulation / Distribution | Standard formula; parity unverified | Zero-range bars contribute zero; cumulative starting point matters. |
| Chaikin Oscillator | Standard formula; parity unverified | EMA difference of accumulation/distribution; seed matters. |
| Rate of Change | Standard formula; parity unverified | Percentage close change over configured length. |
| Momentum | Standard formula; parity unverified | Close minus historical close. |
| Stochastic | Standard formula; parity unverified | SMA-smoothed K/D; zero-range conventions need comparison. |
| Williams Percent Range | Standard formula; parity unverified | Close location within rolling high/low, -100 to 0. |
| Awesome Oscillator | Standard formula; parity unverified | Fast/slow SMA difference of HL2. |
| Balance of Power | Specific smoothed version | SMA of candle balance; smoothing/defaults may differ. |
| Bull Bear Power | Standard formula; parity unverified | High/low minus close EMA; EMA seed matters. |
| Bollinger Bands %B | Standard formula; parity unverified | Position inside our Bollinger bands; zero-width cases need comparison. |
| Bollinger BandWidth | Standard formula; parity unverified | Width divided by SMA basis, expressed as percent. |
| TRIX | Standard family; parity unverified | Triple-EMA percentage change plus our EMA signal line. |
| True Strength Index | Standard formula; parity unverified | Double-smoothed momentum/absolute momentum plus signal. |
| Ulcer Index | Formula implementation; parity unverified | Rolling-high drawdown RMS; window conventions need comparison. |
| Vortex Indicator | Standard formula; parity unverified | Cross-bar movement divided by rolling true range. |
| Elder Force Index | Standard formula; parity unverified | EMA of close change times volume. |
| Historical Volatility | Specific annualization convention | Annualization input is bars/year, default 365; intraday and market calendars require adjustment. |
| Chande Momentum Oscillator | Standard formula; parity unverified | Rolling gain/loss balance. |
| Price-volume Trend | Standard formula; parity unverified | Cumulative percentage close change times volume. |
| Percentage Volume Oscillator | Standard formula; parity unverified | Fast/slow volume EMA ratio and EMA signal. |
| Squeeze Momentum, native | Independent version | EMA/Wilder-ATR Keltner construction; not the LazyBear port. |
| WaveTrend, native | Public-formula implementation; parity unverified | HLC3 normalization and EMA/SMA oscillator; compare seed and warmup. |
| UT Bot, QuantNomad port | Source-reviewed Python port | Added trail line; original candle recolouring/alert names not reproduced; no numerical parity certification. |
| Squeeze Momentum, LazyBear port | Source-reviewed Python port | Default follows author-corrected BB multiplier; legacy flag restores published typo; squeeze-state rendering differs. |
| WaveTrend, LazyBear port | Source-reviewed Python port | Difference drawn as a line rather than filled area; numerical parity unverified. |
| Williams Vix Fix, ChrisMoody port | Source-reviewed Python port | Range guides always displayed; numerical parity unverified. |
| Supertrend, Kivanc port | Source-reviewed Python port | Chart-timeframe calculation; original MTF UI and background fills omitted; numerical parity unverified. |

## What was not complete at this audit

The subsequent 55-study expansion is documented in
`docs/indicator-expansion-20261007.md`; it implements the standard examples below.
This earlier comparison remains a historical audit, not a parity certificate.
At audit time, examples still absent included
Ichimoku, Parabolic SAR, Ultimate Oscillator, Connors RSI, Coppock Curve,
Fisher Transform and Kaufman's Adaptive Moving Average. Public community
scripts such as CM MACD MTF and some market-structure/support-resistance scripts
also remain unported. Some require confirmed higher-timeframe alignment,
pivot/zone lifecycle tests or licence decisions. Protected scripts are not copied.
There is no full Pine Script interpreter.

## Required for an exact comparison

Use identical OHLCV data, exchange/product, timeframe, session/timezone,
parameters, anchor and historical prefix. Export TradingView values from closed
bars, compare against our output with a stated numeric tolerance, and test early
bars, flat prices, zero volume, missing bars, equal extrema and live-bar changes.
Current arithmetic/regression tests do not replace this reference-data audit.

## Sources

- Local implementation: indicators.py, indicator_catalog.py, community_indicators.py.
- Specific public-port versions and differences: docs/community-indicators.md.
- [TradingView RSI definition](https://www.tradingview.com/support/solutions/43000502338-relative-strength-index-rsi/) specifies Wilder RMA gains/losses.
- [TradingView VWAP definition](https://www.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap/) documents anchor/reset options absent from our current VWAP.
- [TradingView Technical Ratings](https://www.tradingview.com/support/solutions/43000614331-technical-ratings/) describes indicator-agreement ratings, not calibrated trade probabilities.
