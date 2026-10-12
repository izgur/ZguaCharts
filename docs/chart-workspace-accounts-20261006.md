# Chart workspace and local accounts

## Workspace changes

- Header height is 40px. Symbol-search icon is aligned to the field's right edge.
- Signal state precedes the wider strategy selector, after the replay separator.
- Fullscreen menus live within the chart stack, including the left-rail indicator selector.
- Selected drawings display anchor handles, receive keyboard focus, and support Delete. Locked drawings remain protected.
- Drawing settings start top-center and can be dragged using the grip.
- Active indicator lines show backend-supplied parameter descriptions. Eye hides an individual study; trash removes it. The parameter popup is currently read-only, not a formula editor.
- Named layouts support save, rename, creation, autosave and reopening. Sharing is disabled. Copy chart produces a PNG, using the clipboard when available or a download fallback.
- Replay preserves the original logical time window using future whitespace timestamps, never future prices. It freezes price bounds and updates lower studies in place. At 8x/16x, slow calculation responses are coalesced to the latest replay position.
- The strategy sidebar follows the selected chart. Watchlist scrolling is bounded so the strategy and signal sections remain accessible. Live sidebar results are dimmed during replay, not hidden.
- Added native 2h/6h/12h Bybit and Binance intervals, plus Binance 8h/3d. Calendar-month candles and Binance 1-second candles are not enabled: the existing fixed-duration history pipeline does not yet support them correctly.

## Display preferences update

Settings now includes separate interface (10-16px), chart-axis (10-18px), and compact indicator/footer (8-14px) font sizes. Changes apply immediately and are stored in the existing user/guest preference profile. The interface setting scales existing typography proportionally rather than making all text the same size.

The chart footer now begins with its live market-feed status, replacing the duplicated candle-count field; there is no separate status row. Long diagnostics can wrap within the pane.

Historical 4h/1h/15m signal details open only on the body of a candle, hollow candle, or Heikin Ashi candle, not a wick or empty chart space. The 260px popup starts at top 7px/right 72px, aligned with the chart corner controls. Its heading supports pointer dragging and arrow-key movement within its chart stack.

## Account security

Local-only accounts use SQLite at `data/accounts.sqlite`. Usernames are case-insensitive, 3-40 ASCII letters/digits/underscore/dot/hyphen. Passwords are 12-128 characters and stored as PBKDF2-HMAC-SHA256 hashes with 600,000 iterations and independent random salts.

Session tokens have 256 bits of randomness and are hashed in SQLite. Sign-in rotates the anonymous session; logout revokes it. Cookies are HttpOnly and SameSite=Strict, with Secure on HTTPS. HTTP is permitted only for loopback access. The application server now binds to 127.0.0.1.

Remembered sessions expire after 30 days; ordinary sessions after 12 hours, with a browser-session cookie. CSRF tokens protect account writes; origin checks reject cross-origin writes. Ten authentication attempts per IP in 15 minutes trigger throttling. Profiles are capped at 2 MB.

Layouts, watchlist preferences, indicator selections/heights and drawings are namespaced per account and saved in that user's profile. Guest storage is preserved separately; signing in does not silently adopt the guest's drawings. Named-layout autosave is separate from profile preference persistence.

This is not an internet-ready authentication gateway. Existing research controls, market collectors, alert notifications and paper configuration remain shared local resources, not user-scoped. No account enables real trading. Before public hosting, upgrade the legacy runtime, protect all shared API routes, add verified email/recovery, deploy HTTPS with a production server, and independently review authorization and security.

Tests create disposable accounts only in temporary SQLite databases; no real user credentials have been created.

References: [OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html), [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html), [Bybit klines](https://bybit-exchange.github.io/docs/v5/market/kline), [Binance intervals](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md).
