# Guest RelativeStrengthV2 backtests

Guests can simulate RelativeStrengthV2 only. They cannot save account history,
edit tactic source, select private tactics, or change the fixed 1x exposure cap.
Fees remain 0.055% per side and slippage 0.02% per side.

Editable inputs are RS lookback (2-200 bars), relative-return advantage
(-10% to 10%, stored as a decimal), long RSI bounds (1-99), ATR stop multiplier
(0.25-10), cooldown (0-200 bars), and the selected BTC/symbol trend filter.
The server independently validates inputs and caps guest history at 2500 bars.

Short mode is opt-in and adds explicit bearish V2 rules: bearish trend,
underperformance versus the same existing BTC benchmark, price below EMA20,
and the mirrored RSI window `[100-rsiMax, 100-rsiMin]`. Short exits retain
the shared ATR/execution engine and add the mirrored EMA50 close-signal exit.
Default long-only entry rules are unchanged. This does not activate shorts in
paper candidates or live trading. Borrow/funding costs are not modeled.

Zero trades are valid when no entries pass. The dialog displays engine blocker
counts rather than manufacturing trades. Counts describe rejected bars, not
independent trades or probabilities.

Hosted backtests remain serialized for memory safety. The dialog retries the
specific busy response for up to two minutes, suppresses duplicate clicks,
and abandons retries/results after the chart changes or the dialog closes.
An already-running server calculation is not canceled by closing the dialog.
