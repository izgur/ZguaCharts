# ZguaCharts outbound email

Chart alerts and registration confirmation share `services/account_mail.py`.
Resend uses its HTTPS API directly from Python; no Node sidecar or local SMTP
server is necessary. Existing encrypted SMTP configurations remain supported.

## Configuration

Secrets belong in ignored `config/local/alert-email.json`, never in browser code
or tracked source. Alternatively set `RESEND_API_KEY`, `ZGUA_MAIL_PROVIDER=resend`,
`ZGUA_MAIL_FROM`, `ZGUA_MAIL_TO` and `ZGUA_MAIL_TEST_RECIPIENT` in the server environment.
The local file is plaintext: protect access to this Windows account and rotate
any API key shared in chat. Prefer a sending-only Resend key.

Example (no real credentials):

```json
{
  "provider": "resend",
  "api_key": "YOUR_RESEND_API_KEY",
  "from": "ZguaCharts <onboarding@resend.dev>",
  "to": "YOUR_RESEND_ACCOUNT_EMAIL",
  "test_recipient": "YOUR_RESEND_ACCOUNT_EMAIL",
  "daily_limit": 50,
  "monthly_limit": 1000
}
```

Without a verified domain, use `onboarding@resend.dev`, not `alerts@resend.dev`.
Only the Resend account owner's address can receive test emails. Account
registration for other addresses cannot complete until you verify a domain and
change `from`, for example to `ZguaCharts <alerts@your-domain.example>`.
Failed confirmation delivery does not activate or retain a new account.

## Included emails

- Chart alerts: source, symbol, timeframe, UTC trigger time, configured conditions
  and custom message. Existing alerts send only if their Email checkbox is enabled.
  The alert destination is the single configured `to` address, not per-account routing.
- Registration confirmation and resend verification: one-use 24-hour link.
  Opening the link confirms immediately and opens Sign in with a success notice.
  Local app links must be opened on the computer running ZguaCharts.
- Password recovery: one-use one-hour link. Resetting or changing a password
  revokes other sessions. Recovery requests do not disclose whether an email exists.
- Email changes: current password required; new address stays pending until a
  one-use confirmation link is opened. Resend test-mode restrictions still apply.
- Setup test: `python scripts/test_mail.py --send`. Without `--send`, it checks
  configuration only and sends nothing.

Use `python scripts/test_mail.py --preview` to generate sample HTML messages in
`reports/mail-previews` without sending anything or creating an account.

Messages have matching responsive HTML and plain-text versions. No marketing,
automatic research digest, incoming mailbox or trading actions are added.
The service can be reused for future email types.

## Safety and operational limits

Resend attempts are paced at one per second in the single server process.
Connection errors, HTTP 429 and HTTP 5xx retry up to three attempts with the same
idempotency key. Permanent errors do not retry. Alerts use their event ID;
verification uses the token digest. No raw provider errors or credentials are
logged or returned to the browser. API redirects are disabled.

Local conservative caps are 50 messages/day and 1000/month (UTC), counting even
ambiguous failed attempts. Counters persist in ignored `config/local/mail-usage.json`.
These are local safeguards, not a guarantee about your Resend account's billing;
check its plan and usage separately. Caps/rate locking assume one server process.
No plan upgrades are performed.

An alert email marked `accepted` means the provider accepted the request, not that
it reached the inbox. Failures are recorded in alert events and alert error state.
Delivery/bounce webhooks and durable background retry queues are not implemented;
after exhausted retries, inspect the provider dashboard. The alert monitor needs
the local app server running and does not backfill historical alerts.

References:
- https://resend.com/docs/api-reference/emails/send-email
- https://resend.com/docs/knowledge-base/403-error-resend-dev-domain
