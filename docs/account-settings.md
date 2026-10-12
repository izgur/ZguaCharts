# Local accounts and settings

The user icon opens Profile, Security and Appearance tabs when signed in.
Profile supports first name, surname, username and avatar upload/removal.
Images are resized/cropped to 128px PNG in the browser. The server checks PNG
dimensions, bit depth, CRC and bounded decompressed pixel data, rejects executable
formats, and discards ancillary metadata. Avatars stay in the local account DB;
no image-hosting service is used.

Security includes password change, email change and sign-out of other sessions.
Password changes require the current password, use a fresh salt and existing
PBKDF2 settings, invalidate recovery tokens and rotate the current session.
Email changes require the current password and a confirmation link sent to the
new address. The old address remains active until confirmed. Confirmation of a
new address signs out existing sessions. Forgot password sends a hashed-token,
one-hour single-use link without disclosing whether an address exists. Successful
reset invalidates sessions and returns to Sign in; it does not automatically log in.
Existing same-origin/CSRF/local-host guards and attempt limits cover these routes.

Appearance exposes seven font groups: interface, chart axes, indicator labels /
footer, header, watchlist, dialogs / panels and backtest / data tables. Preferences
apply immediately and persist with the account's existing layout/profile system.
The Settings page exposes the same controls for guests. Font reset does not
change layouts, indicators, trading configuration or research candidates.

Registration now distinguishes duplicate email and username errors. This is a
localhost-only application; a publicly deployed service should use generic
registration responses to avoid account enumeration and stronger deployment
controls. The application is not a hosted identity provider: MFA, social sign-in,
account deletion/export and detailed device-session history are not implemented.
No existing passwords or user profiles are modified by the migration.

Saved alerts are visible above the alert form, active first, with source/symbol /
timeframe and conditions in the name. Clicking loads the saved market and values;
Save changes updates its existing ID instead of creating another alert. Edits
restart observation without backfilling historical triggers. Active can be
unchecked to pause an alert. Alerts and their email recipient are still shared
local-server state, not isolated per account. Notifications never place orders.

"On-screen popup" is the former "Toast" option: a small temporary message inside
the chart window, not a browser push notification or an email. The page must stay
open for popups/sound. Email alerts need the server running, not the chart tab.
