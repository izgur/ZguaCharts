# Standard indicator expansion

The catalogue has been extended with 55 native Python studies, without a Pine
interpreter. Each entry is registered for the picker, parameters, favourites,
single-study source export and Personal copies. Info guides disclose the use
and meaning of the lines. This is not a claim of exact TradingView parity.

Added families:
- Ichimoku with conversion/base, displaced leading spans, green/red cloud fill
  and backward Chikou display; Williams Alligator with displaced smoothing.
- SAR dots, KAMA, McGinley, median, moving-average ribbon/crossover, TWAP and
  an independent volume-auto-anchored VWAP.
- Chandelier, Chande Kroll and volatility stops; completed-UTC-day ADR and
  traditional daily pivots.
- Fisher, choppiness, Ultimate Oscillator, Connors RSI, Coppock, KST, PPO,
  price oscillator, SMI, ergodic indicator/oscillator, RCI/ribbon, relative
  vigor/volatility, PMO, BBTrend, trend strength, Woodies CCI and Pring Special K.
- Ease of Movement, Mass Index, Klinger, NVI/PVI, net/up-down/24-hour volume,
  relative volume at UTC clock time, performance and causal uncentered DPO.
- Confirmed fractals/high-low pivots, RSI divergence, close-reversal Zigzag,
  Fibonacci retracement/extension, key levels, trendlines and pitchfork.

Calculation timestamps remain causal. Ichimoku/Alligator display offsets are
metadata, not changes to when their inputs became available. Projected cloud
values are known calculations, never future prices. Chikou is intentionally
backward-displayed and must not be used as knowledge available at that earlier
timestamp. Replay recalculates using visible candles only. Shifted series and
lower studies use one timestamp spine to preserve chart alignment.

Pivot-based tools are deliberately confirmation-time variants. They do not
backdate markers or redraw prior segments when new swings appear. Automatic
levels and anchor selection are independent implementations, not replicas of
proprietary TradingView algorithms. SAR's initialization, smoothing seeds,
missing-bar behavior and session definitions may differ from other platforms.
Klinger follows the documented signed-volume-force formula. Woodies exposes
the CCI pair, not every discretionary pattern rule. Pring Special K needs long
warmup (and longer for its signal); null values are preferable to invented data.

UTC session studies assume the crypto session, not stock exchange calendars.
ADR and daily pivots exclude the first possibly truncated loaded day. Intraday
relative volume needs enough matching UTC clock times. 24-hour volume remains
null until a full elapsed day is loaded; coarse bars cannot give precise
intraday totals. Net/up-down volume is a candle-direction estimate, not real
trade delta.

## Still not represented as a usable replica

- Advance/decline line, ratio/bars and cumulative volume index: market-wide
  constituent/breadth feeds, not one symbol's candles.
- Open interest and true volume delta/CVD: exchange OI or classified trade
  feeds. Existing flow collection is not fabricated into historical bars.
- Cross-instrument correlation: synchronized second-instrument input.
- Seasonality/session calendars, moon phases and session visualization:
  dedicated calendar/astronomy specifications and rendering.
- Visible-average-price and multi-period chart/visual overlays, Bollinger
  Bars and gaps: dedicated viewport/candle/region rendering and definitions.
- Protected Top/community scripts, proprietary price targets and Rob Booker
  variants without reviewed exact formulas: source/licence review required.
  Existing five public-source Python ports remain available.
- Technical Ratings: current generic signal score is not TradingView's exact
  composite. The tactic-specific successor is planned separately rather than
  relabelling that score as a probability.

These are gaps, not hidden or approximated placeholders. The screenshot list
is not claimed complete, and every public community script is not claimed
ported. No Pine support or live trading is enabled.

Primary formula references checked:
- [Ichimoku](https://www.tradingview.com/support/solutions/43000589152-ichimoku-cloud/)
- [SAR](https://www.tradingview.com/support/solutions/43000502597-parabolic-sar-sar/)
- [Choppiness](https://www.tradingview.com/support/solutions/43000501980-choppiness-index-chop/)
- [Connors RSI](https://www.tradingview.com/support/solutions/43000502017-connors-rsi-crsi/)
- [SMI](https://www.tradingview.com/support/solutions/43000707882-stochastic-momentum-index-smi/)
- [Chande Kroll](https://www.tradingview.com/support/solutions/43000589105-chande-kroll-stop/)
- [Fisher](https://www.tradingview.com/support/solutions/43000589141-fisher-transform/)
- [Klinger](https://www.tradingview.com/support/solutions/43000589157-klinger-oscillator/)
- [BBTrend](https://in.tradingview.com/support/solutions/43000726749-bbtrend/)
