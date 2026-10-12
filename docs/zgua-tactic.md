# ZGUA Tactic

Exploratory long-only tactic, selectable in the chart strategy menu and manual backtest. It is not automatically promoted to the paper watcher or live trading.

- Enter on a confirmed bullish Zgua divergence, at the observed confirmation bar's opening.
- Exit an existing long on a confirmed bearish Zgua divergence, at that opening.
- All enabled regular, hidden, exaggerated and weak divergence types participate. Engulfing and sweep context patterns do not.
- Simultaneous bullish and bearish events: no new entry while flat; exit an existing long. No immediate re-entry on that bar.
- No ATR stop, trend filter, profit target or forced end-of-data exit. The final open position remains open and contributes marked-to-market equity separately from completed trades.
- Default modeled costs are 0.055% fees and 0.02% slippage per side, with all-cash sizing at 1x. The confirmation timeframe is the execution timeframe, not a separate 5m exit mechanism.
- Chart preview uses the chart's Zgua indicator settings, including oscillator and divergence toggles. Backtesting computes the complete historical event stream, not the display's last-80-event limit.
- Editable source exposes the actual JavaScript entry and exit decisions. Personal ZGUA tactics receive confirmed detections from the same Python detector using chart settings; the worker cannot access the network or broker APIs.

## Inspecting a Divergence

Click a Zgua arrow or its label, such as `HB (1)`. A temporary lower pane shows the contributing oscillator, the actual values at the two price pivots, and a coloured straight connector. Choose another contributing oscillator from its selector or close the inspection with the close button. Its time range and crosshair share the main chart's timeline.

The two pivot timestamps describe the geometry. The later confirmation timestamp is when the event becomes observable. The connector is drawn retrospectively after confirmation; it is not evidence that a trade could have been entered at the earlier pivot. Divergence is not a calibrated probability and profitability is not established.
