# Calendar Timeframes

Sources with daily OHLC support Monthly (`1M`, displayed `M`) and Yearly (`1y`,
displayed `Y`) chart views. Enable them in Appearance > Header timeframes.
They are unchecked by default and the selection persists with UI preferences.

Daily candles are aggregated at UTC calendar boundaries, with summed volume,
first open, highest high, lowest low and last close. Month lengths and leap
years are handled exactly. Complete historical buckets are required; the current
forming period includes today's forming daily candle. Missing daily history is
not fabricated. Historical/closed queries exclude the unfinished calendar period.

Initial daily history retrieval is bounded at 12,000 days. Available monthly/yearly
candle count depends on exchange listing and provider history coverage, not the
requested chart count. Long-warmup indicators might lack enough yearly bars.
These are chart/indicator views; tactic execution/backtesting is not yet validated.
