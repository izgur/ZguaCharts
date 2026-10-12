# Source-reviewed community indicators

These Python adaptations are in `community_indicators.py`, under MPL-2.0.
They were reviewed against public source on 2026-10-06. TradingView's default
licence for public scripts is documented at:
https://www.tradingview.com/pine-script-docs/writing/publishing/
Explicit source licences take precedence. Native indicators remain separate.
The source editor and downloads include the selected study's actual formula,
its required helpers and attribution. ZIP exports include this notice.
Editable copies are covered in custom-indicators.md. This is not a Pine interpreter.

## Available Ports

- UT Bot, QuantNomad (version 1; credits HPotter and Yo_adriiiiaan):
  https://www.tradingview.com/script/n8ss8BID-UT-Bot-Alerts/
  SHA256 d17fdc1d34a20107dbb853316886ac8c27803b159f379e66c685810d7e10410c.
  ATR-stop formula, OHLC4 Heikin Ashi close option, buy/sell crossover markers.
  Trail line is added; candle recolouring and original alertcondition names are
  not reproduced. Live candles can change markers before closing.
- Squeeze Momentum, LazyBear (published legacy source, version -1):
  https://www.tradingview.com/script/nqQ1DT5a-Squeeze-Momentum-Indicator-LazyBear/
  SHA256 dc81681173d8afa6223bc6886f1358aa5735a510d205e598929471ec99977795.
  Uses SMA Keltner basis/range and regression momentum. Default BB multiplier
  follows the author's correction linked at https://pastebin.com/UCpcX8d7;
  legacy_bb=1 reproduces the original multiplier typo. Squeeze states are a
  separate +1/-1/0 series rather than coloured zero-line crosses.
- WaveTrend, LazyBear (version -1):
  https://www.tradingview.com/script/2KE8wTuF-Indicator-WaveTrend-Oscillator-WT/
  SHA256 839bba8a402ec5d74bcf119dae706ad1988bd618f549df8e4d878f0cad3e1935.
  HLC3 normalization, recursive EMA, SMA signal, difference and level guides.
  Difference is a line rather than filled area. The author's source asks users
  of original/modified code to drop them a note; no external message was sent.
- Williams Vix Fix, ChrisMoody / Larry Williams (version -1):
  https://www.tradingview.com/script/og7JPrRA-CM-Williams-Vix-Fix-Finds-Market-Bottoms/
  SHA256 5ffae712a1ab9933f48044b841a9a6cc63448a70a34f1f0f12756bbb3fcdc646.
  Distance from rolling close high, Bollinger upper and extreme-based ranges.
  These are rolling extreme multipliers, not actual quantiles or the VIX index.
  Range/upper-band guides are always displayed in our renderer.
- Supertrend, KivancOzbilgic (version 3; credits everget / Alex Orekhov):
  https://www.tradingview.com/script/r6dAP7yi/
  SHA256 eb300e693cc5901ac46dec5e86372e8951b03cd351abe2ebb6c393872b81346a.
  Original trend recurrence, Wilder/SMA ATR choice, HL2/close choice, reversal
  markers. Chart timeframe only; background fills and original MTF UI omitted.

Original authors retain their rights. These are attributed formula ports, not
endorsed products or verified bit-for-bit TradingView replicas. EMA initialization,
early-bar handling and exchange data can differ. Tests check arithmetic,
prefix invariance, settings, warmup, zero-range data and serialization.

## Reviewed but Not Ported

- LuxAlgo SMC and Support/Resistance with Breaks: explicit CC BY-NC-SA 4.0
  headers. Excluded pending permission/product-use decisions; not assumed to be
  suitable for unrestricted distribution. No restricted code is bundled.
- CM MACD MTF (ChrisMoody): source retrieved; needs proper confirmed higher-
  timeframe alignment. It uses an SMA signal, unlike our standard MACD EMA.
- Market Structure Break & Order Block (EmreKb): MPL-2.0; source retrieved;
  needs persistent boxes/zigzag lifecycle and explicit repainting semantics.
- Support Resistance Channels (LonesomeTheBlue): MPL-2.0; source retrieved;
  needs pivot confirmation and zone lifecycle/rendering tests.
- Remaining earlier screenshot entries (ICT Killzones + Pivots, Trendlines
  with Breaks, Bollinger + RSI strategy, GainzAlgo Suite): not yet source-reviewed
  or ported. No protected code will be retrieved or reconstructed.

Review results and permitted original source snapshots are saved in
`reports/indicator-source-review/manifest.json` and adjacent `.pine` files.
This is a finite first set, not every public indicator on TradingView.
