# Production deployment: Flask, Neon and Render

This runbook prepares the existing web service. It does not provision resources,
deploy code, import data automatically, or change any trading configuration.
Do not run production migrations or imports during ordinary development/tests.

## Startup and security checks

- `services.deployment.production_mode(env=None)` recognizes `ZGUA_ENV=production`.
- `validate_environment(env=None)` returns a list of fixed failing check names,
  or `[]`. It reads only the supplied mapping (default: process environment).
  It performs no database, mail, dotenv, local config, or Flask app I/O.
- `public_registration_enabled(env=None)` defaults to false in production and
  true in development. `ZGUA_REGISTRATION_ENABLED=1` explicitly enables signup;
  `0` disables it. The registration endpoint and account metadata enforce this
  availability response, together with mail readiness. Existing-user login and
  password reset should remain available when signup is disabled.
- `database_errors()` returns fixed failing check names, or `[]`. It uses
  `services.storage.database()` and the active storage configuration, prohibits
  SQLite fallback, sets the transaction read-only, checks every migration filename
  against `public.schema_migrations`, checks required public table/column names,
  and verifies SELECT privileges/search-path resolution without reading user data.
  It does not migrate or import. Extra applied versions are tolerated for additive
  rollbacks; operators must still review schema compatibility.
- The application validates the production environment early in startup and skips
  development `.env` loading when production is set in the real process environment.
  The scripts here never load dotenv or import `app`.
- Render `startCommand`, Procfile `web`, and the Docker command use
  `python scripts/start_web.py`. The wrapper checks production configuration and
  schema before spawning `python -m gunicorn app:app --bind 0.0.0.0:$PORT
  --workers 1 --threads 2 --timeout 180 --max-requests 500
  --max-requests-jitter 50`. It validates PORT (default 10000), strips ambient
  `GUNICORN_CMD_ARGS`, forwards SIGTERM/SIGINT, and propagates the child's exit code.
- `/healthz` reports liveness; `/readyz` checks schema readiness with fixed status
  and check names. Neither includes environment
  values, URLs, mail configuration, schema contents, or raw exceptions. A cached
  readiness check can avoid repeated full schema probes. Do not expose the verbose
  local operator health endpoints publicly.

## Render configuration

Use the existing Python web service. Build with
`pip install -r requirements-production.lock && npm install --omit=dev && python scripts/install_indicator_runtime.py`, start with
`python scripts/start_web.py`, and set health check path `/healthz`.
Keep one Gunicorn worker, two threads and the existing bounded request lifetime:
storage currently allows four PostgreSQL connections per process.
Hosted requests are bounded to 5000 chart/indicator candles, 12 simultaneous
studies and 2500 signal candles per server backtest. Backtests are serialized;
concurrent runs return a retryable busy response. Local research limits are not
changed. Longer execution history may be incomplete: inspect coverage diagnostics
rather than treating a capped result as a full requested-period backtest.
Render provides `PORT`; bind on `0.0.0.0`, not localhost.
The build installs pinned Pyodide assets for personal Python indicators; those
binaries are intentionally not in Git. Startup checks their presence. Built-in
indicator calculations remain in Python on the server; personal scripts run in
restricted browser workers, not as arbitrary code on Render.
See [Render web services](https://render.com/docs/web-services) and
[health checks](https://render.com/docs/health-checks).

Set these in Render's Environment settings; do not include literal quotes around
values in the dashboard, commit secrets, or put connection strings in commands.

| Variable | Value / purpose |
| --- | --- |
| `PYTHON_VERSION` | `3.12.14` (production dependency runtime) |
| `NODE_VERSION` | `24` |
| `ZGUA_ENV` | `production` |
| `DATABASE_URL` | Production Neon pooled PostgreSQL connection string from its Connect dialog; retain `sslmode=require`, `verify-ca`, or `verify-full` |
| `PUBLIC_APP_URL` | Actual public HTTPS origin, without trailing slash, path, query, fragment or credentials; custom port is allowed |
| `ACCOUNT_MFA_KEY` | Secret valid Fernet key; preserve the existing encryption key when importing encrypted MFA accounts |
| `ZGUA_PUBLIC_ACCOUNTS` | `1` |
| `ZGUA_REGISTRATION_ENABLED` | `0` initially (also the production default); change to `1` only after verified mail is ready |
| `ZGUA_QUIET` | `1` |
| `ZGUA_CACHE_CANDLE_BUDGET` | `60000` |

Use a separate production Neon project and credentials from development. A branch
called "production" inside the development project is still development data.
Confirm the project and branch in Neon before copying either connection string.
The validator can verify syntax/TLS but cannot infer project ownership from a URL.
Authentication caps are keyed by account/subject in public mode, with a broad
transport ceiling, so users behind the same hosting proxy do not share a single
ten-attempt account cap. Client-supplied forwarded headers are never used as proof
of local access or as trusted throttle identities. Review edge/proxy configuration
before increasing public capacity.
New anonymous sessions are capped at 500 per transport peer per 15 minutes;
expired sessions/tokens are removed by the cleanup command. Monitor this ceiling
and edge traffic so a shared proxy does not silently become a scaling bottleneck.
For a private offline comparison you may supply `ZGUA_DEVELOPMENT_DATABASE_URL`;
the validator rejects the same database endpoint/name, including Neon pooled/direct
host variants. This optional guard is not proof of project isolation; don't copy
development credentials into Render. No project IDs or database URLs are hardcoded.

### Email and public signup

With signup disabled, existing users may sign in/reset passwords. Resend's
`onboarding@resend.dev` test sender may be retained for the configured owner only:
it cannot deliver password resets to arbitrary existing users. Use verified mail
for general recovery as well as signup. Main should show that limitation through
mail readiness without advertising public registration.

Before setting `ZGUA_REGISTRATION_ENABLED=1`, verify your domain with the mail
provider, test delivery to an unrelated mailbox, then set:

| Resend variable | Value / purpose |
| --- | --- |
| `ZGUA_MAIL_PROVIDER` | `resend` |
| `RESEND_API_KEY` | Secret API key authorized to send from the verified domain |
| `ZGUA_MAIL_FROM` | Explicit sender mailbox on your own verified domain, optionally with display name |
| `ZGUA_MAIL_DOMAIN_VERIFIED` | `1`, operator attestation that provider verification completed |

For SMTP instead set `ZGUA_MAIL_PROVIDER=smtp`, `ZGUA_SMTP_HOST`,
`ZGUA_SMTP_PORT` (1-65535), `ZGUA_SMTP_USERNAME`, `ZGUA_SMTP_PASSWORD`,
`ZGUA_SMTP_FROM` and `ZGUA_SMTP_SECURITY=starttls` or `ssl`, plus
`ZGUA_MAIL_DOMAIN_VERIFIED=1`. If `ZGUA_MAIL_FROM` is also set it must match
`ZGUA_SMTP_FROM` because existing mail configuration gives it precedence.
The verification flag is an attestation, not a network verification API.
The validator rejects `resend.dev` and its subdomains when signup is enabled.
It never sends a test message or reads `config/local/alert-email.json`.

## Readiness and explicit migrations

Run checks with secrets injected privately into an isolated production operator
environment. Default mode is offline; output contains only PASS/FAIL and fixed
check names. Exit code is 0 for success, 1 for failed checks, and 2 for invalid CLI
arguments (which are not echoed).

```sh
python scripts/check_production.py --offline
python scripts/check_production.py --database
```

Offline performs no network calls. Database mode refuses invalid production
configuration before connecting. It checks the schema in a read-only transaction,
not user counts or data freshness; missing schema or versions blocks web startup.
These checks are suitable for startup diagnostics, not dependency vulnerability
audits, mail delivery guarantees, or backup verification.

Before a release, create a production backup/restore point and review the migration
SQL against a separate staging database. In a dedicated operator environment set
`DATABASE_URL_UNPOOLED` to the production direct Neon string with TLS. Confirm the
target independently, then deliberately apply:

```sh
python scripts/migrate_database.py --apply
python scripts/check_production.py --database
```

The migration script accepts an explicit private file with `--env-file .env.production`.
It does not use the development file when this option is supplied. The readiness
CLI also accepts `--env-file .env.production`. Never include credential values in
command arguments. Keep the privileged direct URL in the operator
environment rather than the web service; no migration runs in `start_web.py`.
`DATABASE_URL_UNPOOLED` is optional for web runtime and validated if present.
Main's modern production dependency file is separate from legacy Python 3.7
`requirements.txt`; do not use the latter for Render.

## Initial accounts and administrator

For this project, copy `deploy/production.env.example` to ignored `.env.production`
and fill it privately with the **production** project URLs and encryption key.
Do not replace the development `.env` file. Apply the schema, then transfer current
PostgreSQL account data:

```sh
python scripts/migrate_database.py --env-file .env.production --apply
python scripts/copy_neon_database.py --source-env .env --target-env .env.production --apply --confirm-project cool-tree-27722416
python scripts/check_production.py --env-file .env.production --database
```

The transfer refuses a nonempty target and equal database endpoints. It copies
the latest development account data, script revisions, alerts, backtests and
administration settings using a consistent read snapshot, not an outdated SQLite
file. It never copies sessions or reset/verification tokens. Pause account edits
during the cutover and verify the admin login before opening registration. The
project confirmation is operator metadata, not a Neon API ownership check.
The transfer writes a production-realm marker. Production startup checks that
marker (and the configured project ID, when provided) plus a verified active
administrator. An unmarked development database or empty account database cannot
pass production readiness merely because its schema exists.
Copied alerts are paused by default, and pre-existing pending email events are
marked for review. This prevents duplicate notifications from development and
production workers during cutover. After reviewing recipients, conditions and
sender configuration, enable production alerts in the UI and disable matching
development alerts. `--enable-alerts` is an explicit override, not the default.

For older SQLite installations, the separate importer preserves
IDs, password hashes, roles, encrypted MFA, profiles and site settings. Sessions
and account tokens are not imported; users sign in again. It refuses a nonempty
target and does not merge accounts.

If local development has already moved to PostgreSQL, `data/accounts.sqlite` may
be old. Newer development account edits, roles, passwords, MFA, profile changes
and documents will NOT be included by importing that old SQLite file. Reconcile
the snapshot before proceeding; this importer is not a PostgreSQL-to-PostgreSQL
sync tool. Inventory what it actually imports; user document revision history is
not copied directly from SQLite, and raw candles/research state are excluded.

There is no first-registration administrator rule. Ensure the chosen existing
verified account has the admin role in the source snapshot before importing.
For a SQLite source only, the existing explicit bootstrap command is:

```sh
python scripts/promote-admin.py EXACT_VERIFIED_EMAIL
```

Run that locally with `DATABASE_URL` unset and the intended SQLite snapshot in
`data/accounts.sqlite`, then back it up again. The current bootstrap uses SQLite
`COLLATE NOCASE` and must not be used against PostgreSQL. Do not promote through
an unreviewed production SQL command or rely on the first signup.
Supply the original `ACCOUNT_MFA_KEY` before importing MFA users; a newly generated
key cannot decrypt existing secrets. For a clean database with no encrypted MFA,
generate a Fernet key privately and keep it with secured backups.

With the reviewed source, an empty migrated target, and the explicit direct URL:

```sh
python scripts/import_local_database.py --source /private/reviewed/accounts.sqlite --apply
python scripts/check_production.py --database
```

Only add `--legacy-owner-email EXACT_VERIFIED_EMAIL` when intentionally assigning
the checkout's shared legacy alerts/backtests to that imported verified account.
Keep signup disabled until initial admin login, MFA and account isolation have
been verified. Never import as a build/start command or copy SQLite into the image
as production persistence.

## Release, rollback and operations

After migrations/import are reviewed and completed, deploy through the existing
Render workflow. Verify health, login, recovery mail and ownership isolation using
test accounts. Keep raw startup/database/provider exceptions out of public health
responses. Monitor failed deploys, readiness failures, HTTP errors, Neon connection
and storage usage, mail failures/ambiguous claims, and backup recoverability.

Use backward-compatible additive migrations where possible. Record the prior
release, environment configuration and backup point before changing them.
Render rollback restores a prior deployment; it does not undo Neon migrations or
new user writes. Review the old code's compatibility with the current schema and
secret settings before rollback. Keep the same MFA key. If the schema cannot serve
the prior code, pause traffic and use a reviewed forward fix or database recovery
plan that accounts for newer writes. Do not delete migration-version rows, rerun
the initial import, or execute destructive down-migrations to force startup.
See [Render rollbacks](https://render.com/docs/rollbacks) for deployment-setting
behavior and dashboard controls.

Alerts require a separately hosted always-running process:
`python scripts/run_alert_worker.py`. The web service does not start it; PostgreSQL
does not execute it, and web idle spin-down cannot sustain it. Use the same
production database, MFA and mail settings in a clean environment without a
development `.env`. Review the existing worker's exception logging before public
operation; this tooling only guarantees secret-safe readiness output.
Choose separate hosting deliberately; this work provisions no worker or paid plan.
`deploy/render-alert-worker.example.yaml` is an optional example only. It is not
part of the main Blueprint; applying it would create a separately billed service.
Monitor worker liveness, pending events and mail claims. Ambiguous in-flight mail
claims require reconciliation, not blind retries. Paper watchers and research
workers remain separate local processes; do not colocate them with public Flask.
