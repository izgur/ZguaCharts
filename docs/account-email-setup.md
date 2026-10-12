# Local account email delivery

Registration requires first name, surname, username, email, a 12-128 character
password and matching confirmation. New accounts cannot sign in until their
email has been confirmed. Existing username-only local accounts remain usable.
Verification tokens are random, stored hashed, single-use and expire in 24 hours.
Resend requests have a 60-second per-account cooldown and share the authentication
rate limit. SMTP failure never reports success or leaves a newly registered user.

No SMTP sender is configured in the current installation. No test messages have
been sent to real addresses. Registration remains unavailable until configured.

Use the existing ignored `config/local/alert-email.json` configuration with
`host`, `port`, `username`, `password`, `from`, and `security` (`starttls` or `ssl`).
Alternatively set `ZGUA_SMTP_HOST`, `ZGUA_SMTP_PORT`, `ZGUA_SMTP_USERNAME`,
`ZGUA_SMTP_PASSWORD`, `ZGUA_SMTP_FROM`, and `ZGUA_SMTP_SECURITY` in the server's
environment. Do not commit passwords or paste them into chat. Restart the server
when changing its environment. Account mail goes to the registered address;
the alert configuration's fixed `to` recipient is not used for account mail.

Brevo offers a transactional SMTP service on its free tier. Resend also offers
a free tier. Both require a provider account and an approved sender. Gmail SMTP
can be used for low-volume testing with an app password and TLS, not the primary
Google account password. Provider quotas and sender verification rules apply.

This remains a local-only account service, not a public deployment. Verification
links point to http://127.0.0.1:5000/charts and must be opened on this computer.
Do not expose Flask publicly. Public registration requires an HTTPS domain,
review of all application API authorization, proxy and cookie configuration,
production hosting, recovery flows, and a verified sending domain.

Indicator settings are saved per chart/account and used by the chart's indicator
endpoint. Research strategy formulas and alert evaluator defaults are unchanged.
VWAP is cumulative over the loaded candles; it has no tunable length. Sources,
Cipher normalization and fixed guide levels remain disclosed in indicator metadata.
