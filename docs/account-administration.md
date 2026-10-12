# Account Administration

Members can use charts and indicators. Tactics users additionally see tactic tools.
Administrators have tactic access and can manage accounts at `/api/account/admin`.
The header account icon opens a dropdown with Edit account, Admin dashboard (admins
only) and Sign out. Signed-out users can choose Sign in, Register or Forgot password.
Forgot password sends the existing one-use reset link to a confirmed account email;
it does not sign the user in or remove two-factor authentication.
New registrations always receive the Member role. There is no first-user admin rule.
Use `scripts/promote-admin.py EXACT_EMAIL` locally for a deliberate bootstrap.
Changing roles or suspending/deleting an account revokes its sessions.
Administrator actions require password reauthentication and the second factor if enabled.
Self-demotion/deletion and removal of the last active administrator are blocked.
An audit trail records administrative changes without passwords or MFA secrets.

## Two-Factor Authentication

Account Settings > Security supports authenticator TOTP setup, confirmation and removal.
Setup expires after ten minutes. Recovery codes are hashed and single use.
Authenticator codes cannot be replayed. Password resets do not remove MFA.
Secrets are encrypted with Fernet; the local key is `data/accounts.mfa-key`, ignored by Git.
Back up that key securely with the database. Loss of the key prevents MFA authentication.
For deployment set `ACCOUNT_MFA_KEY` through a secret manager, and restrict local file access.

## Donations

The admin panel accepts official HTTPS hosted links for Ko-fi, Stripe, PayPal and Revolut,
and bank-transfer beneficiary, IBAN, BIC, bank and reference details.
The IBAN checksum is validated. Published details appear in the header Donate dialog.
Payments take place on provider sites, never through a locally collected card number.
This is not a webhook/payment ledger: bank transfers and hosted payments are not automatically reconciled.
Donations do not change roles or grant tactic access.

## Deployment Boundary

The existing account system deliberately accepts only localhost requests.
Do not expose this server directly as a public website. Public deployment requires HTTPS,
trusted proxy handling, public email links, current supported Python/dependencies,
secret storage, database backups, monitoring and an application-wide authorization review.
Tactic-source and backtest endpoints now enforce account roles. Existing paper scripts run
through their separate local launchers; their state and trading configuration are unchanged.
