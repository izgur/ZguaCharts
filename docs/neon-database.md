# Neon persistence

## Environments

- Development project: zguachartsdev (`bold-king-81332360`). Its branch named
  `production` is still development because it belongs to this separate project.
- Production project: zguachartsdb (`cool-tree-27722416`). Never use its connection
  for test fixtures or local experimental runs.
- `.env` is ignored by Git. Render secrets belong in its Environment settings.
- No Neon Functions deployment, bucket policy, or replacement hosting is needed.

## Setup

1. In development project's Connect dialog copy the pooled PostgreSQL URL to
   `DATABASE_URL` in `.env`, retaining TLS options.
2. Disable pooling and copy the direct URL to `DATABASE_URL_UNPOOLED`.
3. Install `pip install -r requirements.txt` with the application's Python runtime.
4. Back up `data/accounts.sqlite` and the corresponding `data/accounts.mfa-key`.
5. If existing accounts use 2FA, set `ACCOUNT_MFA_KEY` to the existing key before
   import. Do not generate a replacement key for encrypted existing secrets.
6. Run `python scripts/migrate_database.py --apply`.
7. Run `python scripts/import_local_database.py --apply`. Optionally add
   `--legacy-owner-email igorzgur@gmail.com` to explicitly assign formerly shared
   alerts/backtest history. The owner must already exist and be verified.
8. Restart local Flask. Accounts now use development PostgreSQL. The original
   SQLite database remains intact; old sessions/tokens are not imported.

The importer refuses a nonempty target account table. It uses a transaction and
preserves IDs, password hashes, roles, MFA secrets, profiles and donation settings.
Do not run import against production until development verification passes.

## Public accounts

For production set `DATABASE_URL`, `ACCOUNT_MFA_KEY`,
`PUBLIC_APP_URL=https://zguacharts.onrender.com` and `ZGUA_PUBLIC_ACCOUNTS=1`.
Schema migrations must run explicitly before serving requests. Database failures
never silently fall back to ephemeral SQLite. Application pool is limited to four
connections per process; keep Render's single-worker deployment configuration.

Local development can keep the encryption key in its ignored
`data/accounts.mfa-key` file. Hosted deployments must use the environment secret
instead; preserve the same key when moving encrypted 2FA accounts.

Public cookies are Secure, HttpOnly and SameSite Strict. CSRF/origin checks remain
active. Local research/operator API routes are blocked on hosted deployments.
Configure a verified Resend sender domain before registration for other users;
`onboarding@resend.dev` remains account-owner-only test delivery.

Run notification processing separately with `python scripts/run_alert_worker.py`.
The web service must not own a daemon that disappears during an idle spin-down.
An always-running worker needs separate hosting; PostgreSQL alone doesn't run it.
No live trading or leverage is enabled by these changes.
On Windows use `scripts/start-alert-worker.ps1` to keep one hidden local instance.
Alert events persist pending mail with an immutable message snapshot. The worker
can recover a crash before a mail claim or an already accepted receipt. Failed or
ambiguous in-flight claims require review rather than an automatic duplicate send.

## Data ownership and lifecycle

Accounts/security/admin settings use relational tables. User preferences, named
watchlists, layouts, drawings, personal source code and favourites use versioned
user_documents with a browser-compatible snapshot projection. Source revisions
are kept in user_document_versions. Profile revision checks reject stale writes.
Browser storage remains a local working copy, not a cross-device authority.

Personal backtest history includes summaries and trade lists, capped at 500 runs
per user and two MB per result. Oversized trade lists are explicitly omitted.
Research histories and running paper candidates remain separate local files.

Candle caches, raw flow recordings, generated reports and process IDs are not
imported. Optional research summary publishing is a later isolated integration,
not a change to the running paper watcher's state store. Back up important local
research separately. Delete/deactivate accounts only through the audited UI.

Operational follow-up: configure retention of expired sessions/tokens, old alert
events, mail records and old source revisions; monitor Neon storage usage and
provider backup/restore capabilities before treating the service as production.
`python scripts/cleanup_database.py` previews the retention policy; add `--apply`
only after reviewing it. Failed or ambiguous mail claims remain for reconciliation.
History lists omit trade lists to keep memory bounded; individual authenticated
results are available at `/api/account/backtests/<id>`.
