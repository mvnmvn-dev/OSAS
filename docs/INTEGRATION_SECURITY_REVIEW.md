# OSAS Integration and Authentication Review

## Current architecture

OSAS is a Flask application organized into route blueprints, database-backed services, models, Jinja templates, and a MySQL schema. Routes delegate most domain behavior to services such as `safety_service`, `visitor_service`, `users_service`, `rbac`, and `notification_service`. `app.py` owns blueprint registration, Flask-Login loading, CSRF, proxy handling, notification context, and the request-level RBAC gate.

## Findings addressed

1. **Authentication trust boundary**: the password route previously called `login_user` immediately, so every protected module trusted only the password factor. The route now creates a pending OTP challenge and calls `login_user` only after a successful OTP verification.
2. **OTP persistence and consistency**: OTP state is stored in `login_otp_challenges`, not in a process-local dictionary. The schema is included for new databases and the Railway bootstrap applies it as an idempotent migration to an existing database.
3. **Secret handling**: plaintext OTP values are never stored. The service stores an HMAC-SHA256 digest derived from `SECRET_KEY`, uses constant-time comparison, expires codes, invalidates codes after use, limits attempts, and binds a challenge to the requesting IP when available.
4. **Delivery integration**: SMTP delivery is configured through the existing notification settings. Production refuses to proceed without `SMTP_HOST`; development logs the code for local testing rather than silently bypassing MFA.
5. **Module integration**: all protected routes continue to use the existing Flask-Login and RBAC boundary, so dashboard, safety, patrol, visitor, profile, API, and administration modules receive the same authenticated principal after MFA. OTP endpoints are explicitly classified as pre-authentication public endpoints.

## Operational configuration

Set `OTP_ENABLED=1` and configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, and `SMTP_FROM` in production. The defaults are a five-minute lifetime, five failed attempts per challenge, and a 30-second resend cooldown. Rotate `SECRET_KEY` only with an intentional session invalidation plan.

## Verification

The complete test suite passes, including new coverage for hashed storage, IP mismatch rejection, single-use behavior, and OTP retry flow. The test command is:

```bash
python -m pytest -q
```

The current suite result after the change is **27 passed**.

## Remaining production recommendations

Use a managed transactional email provider or authenticated SMTP relay, monitor OTP request/failure events, add a reverse-proxy rate limit for `/login` and `/resend-otp`, and run a production security review for uploaded attachments and database backups. For stronger phishing resistance, WebAuthn/TOTP authenticator apps are preferable to email OTP as a future second factor.
