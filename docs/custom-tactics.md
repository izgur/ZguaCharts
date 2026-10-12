# My tactics

The code icon beside the chart tactic selector opens that tactic's JavaScript
entry rule and required helpers. PARAMS starts with the active preset settings.
Shared engine sizing, execution, exits and accounting are not duplicated in the
editor. These are JavaScript tactics, not Python or Pine scripts.

Backtest tests the loaded chart candles, at the selected chart execution
timeframe, with Bybit BTC 4h benchmark/regime context. It is not the existing
5-minute paper execution simulation and results should not be compared without
matching data, periods and execution settings. The editor explicitly shows the
execution interval, net account return, trades, drawdown, fees and slippage.
Modeled taker fees are 0.055% per side; slippage is 2 bps per side. Position
notional is capped to starting capital and 100% of cash. No live orders or
leverage are enabled. These assumptions are research settings, not current
exchange fee guarantees. Candle OHLC cannot establish exact intrabar ordering.

Save as new suggests a name. Saved copies appear under My tactics in the selector;
their source can be updated, renamed, copied or deleted. New opens a Simple ATR
Trend template. Libraries are local for guests and synchronized with signed-in
account preferences. Limit: 10 scripts, 100 KB per script, 750 KB library.

Custom scripts execute only in disposable browser workers with a 10-second timeout,
4 MB result limit and CSP preventing network requests and nested workers. They
never run on the server or paper watcher. There is no hard browser memory quota.
Use trusted code: arbitrary scripts can manipulate their own research results or
deliberately read future input. Custom backtests are not promotion evidence.

SimpleAtrTrendV2, PullbackReclaimV2 and BreakoutRetestV2 are exploratory built-in
choices, not newly validated winners. Existing paper candidates are unchanged.
The AST exporter uses pinned Acorn 8.15.0; reinstall with npm install --prefix
vendor/javascript --ignore-scripts. Acorn's license is included in its package.
