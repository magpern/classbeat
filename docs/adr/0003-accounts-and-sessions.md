# ADR 0003: Accounts, sessions and sign-in

Status: Accepted

## Context

M1 adds accounts (roadmap §3, P0): email and password, Google, GitHub and Microsoft sign-in,
server-side sessions, CSRF protection, rate limits, recent authentication, and admin TOTP MFA.
Sign-in happens before a `UserContext` exists, so the account data must be readable before
the user is known. That is the case ADR 0002 §3 anticipated: users, identities and sessions are
**system tables**, reached only through `classbeat.auth`. This ADR fixes the tables, the request
pipeline, the cookie and CSRF rules, the flows and their limits, so issues #36 to #40 can
implement them without design decisions of their own.

## Decision

### 1. Where the account data lives

All M1 tables are system tables (ADR 0002 §3): no `owner_id`, no RLS, listed in
`SYSTEM_TABLES` with a reason. They are defined in `classbeat.auth.tables` and **only
`classbeat.auth` (and migrations and tests) may import it**: a new guard test (guard 5, next to
the ADR 0002 guards) fails on any other import of that module or of its table objects, and on
raw SQL naming those tables outside `classbeat.auth`. Owned tables later reference `users(id)`
(`owner_id … REFERENCES users (id) ON DELETE CASCADE`); no system table references an owned one.

Why not owned tables with `SECURITY DEFINER` lookups: RLS guards against a forgotten owner
filter, and no code outside `classbeat.auth` can reach these tables at all (guard 5), so there is
no such filter to forget. Against SQL injection, RLS doesn't help (T26: the app role can set
`app.user_id`). What protects the data is what is stored: every token is kept only as a hash,
passwords as argon2id, TOTP secrets encrypted (§8). This keeps ADR 0002 unchanged and needs no
privileged migration step in M1.

| Table | Holds | Secrets stored as |
|---|---|---|
| `users` | `id`, `email`, `email_normalized` (unique), `email_verified_at`, `password_hash` (nullable), `role` (`user`/`admin`), `disabled_at`, `created_at` | argon2id hash |
| `identities` | `user_id`, `provider`, `subject`, `email_at_link`, `created_at`; `UNIQUE (provider, subject)` | none |
| `sessions` | `id`, `user_id`, `token_hash` (unique), `created_at`, `last_seen_at`, `expires_at` (absolute), `reauth_at`, `mfa_at`, `stepup_at`, `user_agent` (truncated), `ip` | SHA-256 of the token |
| `auth_tokens` | `purpose` (`verify_email`, `reset_password`, `change_email`), `token_hash` (unique), `user_id`, `new_email` (for `change_email`), `expires_at`, `used_at` | SHA-256 |
| `oauth_flows` | `flow_hash` (unique), `provider`, `purpose` (`sign_in`, `link`, `reauth`), `state_hash`, `nonce`, `pkce_verifier`, `user_id` (for `link`/`reauth`), `invite_id`, `return_to`, `expires_at` | the flow id and state as SHA-256; the verifier and nonce are kept as given, live ≤ 10 min and are deleted on use |
| `invites` | `code_hash` (unique), `created_by`, `used_by`, `expires_at`, `used_at` | SHA-256 |
| `mfa_totp` | `user_id` (unique), `secret_ciphertext`, `key_id`, `confirmed_at`, `last_step` | AES-256-GCM (§8) |
| `mfa_recovery_codes` | `user_id`, `code_hash`, `used_at` | HMAC-SHA256 (§8) |
| `rate_limits` | `bucket_hash`, `window_start`, `count`; primary key `(bucket_hash, window_start)` | HMAC of the bucket key |
| `auth_events` | `user_id` (nullable), `kind`, `ip`, `at`, `detail` (no secrets) | none |

- `email_normalized` is the Unicode case-fold of the whole address after trimming. It's for
  lookup and uniqueness only; mail goes to `email` as typed.
- Expired `sessions`, `auth_tokens`, `oauth_flows`, `rate_limits` rows are deleted by an hourly
  purge in the worker. `auth_events` and session `ip` values are kept 30 days (T22).
- Only the `id` and `token_hash` columns identify a session; the cookie value itself is never
  stored or logged.

### 2. From request to `UserContext`

`classbeat.auth` is the web issuer of `UserContext` (ADR 0002 §1). One FastAPI dependency,
`current_context`, is the only path:

1. Read the `__Host-classbeat_session` cookie (§3). Missing → anonymous.
2. Look up `sessions` by `SHA-256(token)` in a `system_session`; join `users`.
3. Refuse if the session is past `expires_at`, idle longer than the idle limit, or the user is
   disabled. A refused session is deleted and the cookie cleared.
4. Update `last_seen_at` at most once a minute.
5. Build `UserContext(user_id, role, …)`. `role` is `admin` only if the user's role is `admin`
   **and** `mfa_at` is set for this session (§7); otherwise `user`.

Routes declare what they need through dependencies built on it: `require_user`,
`require_recent_auth` (10 min, §6), `require_admin` (MFA done), `require_stepup` (5 min). An
anonymous request to a protected route gets 401; a user without the needed level gets 403 with
a machine-readable reason (`reauth_required`, `mfa_required`, `stepup_required`) so the SPA can
prompt.

### 3. Sessions and cookies

- Token: 32 random bytes (`secrets.token_urlsafe(32)`), server-issued only. Never accepted
  from a URL or a header other than `Cookie`.
- Cookie `__Host-classbeat_session`: `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`, no
  `Domain`. Browsers treat `http://127.0.0.1` and `http://localhost` as secure contexts, so the
  same attributes work in local development and Playwright. There is no setting to drop
  `Secure`.
- Limits: idle 7 days, absolute 30 days (settings, with these defaults).
- **Rotation:** a new token (and a new row, the old one deleted) on sign-in, on MFA
  verification, on a role change and on re-authentication. A password change or reset, and an
  email change, revoke every other session of the user. "Sign out everywhere" deletes them
  all.
- Sign-out deletes the row and clears the cookie.

### 4. CSRF, origin and redirects

Every state-changing request (`POST`, `PUT`, `PATCH`, `DELETE`) must pass both of these checks.
The few `GET` callbacks (OAuth) are protected by their state instead (§5).

- **Origin:** `Origin` equals the configured public origin (from `CLASSBEAT_PUBLIC_BASE_URL`).
  When `Origin` is absent, `Sec-Fetch-Site` must be `same-origin`; with neither header, refuse.
- **Token:** the `X-CSRF-Token` header must match:
  - signed in: `HMAC(csrf_key, session.id)`, returned by `GET /api/auth/session`;
  - anonymous (sign-in, sign-up, reset forms): the value of the `__Host-classbeat_csrf` cookie
    (random, `Secure`, `SameSite=Strict`, readable by JavaScript), sent by double submit.
- Responses to the API are `Cache-Control: no-store`.
- **Redirects:** `return_to` (after sign-in) is accepted only as a relative path starting with a
  single `/`, without a scheme, host or backslash, checked again before use (T36).

### 5. Sign-in methods

**Email and password**
- argon2id via `argon2-cffi`: memory 19 MiB, time 2, parallelism 1 (OWASP minimum),
  rehashed on sign-in when the parameters change.
- Passwords are 12 to 128 characters after NFKC normalization, with no other composition
  rules. A breached-password check (HIBP range API) is **deferred**: it's a new outbound host,
  and the beta is invite-only. Accepted risk, revisited at M6.
- A sign-in with an unknown email still runs one argon2 verification against a fixed dummy
  hash, so the timing matches. Every failure gets the same message.
- An unverified email can sign in but can't use anything beyond the verify-email page.

**Sign-up**
- Invite-only while `CLASSBEAT_SIGNUP_MODE=invite` (default; `closed` and `open` exist). The
  invite code is checked and used in the same transaction that creates the user.
- Signing up with an email that already exists gives the same response ("check your inbox").
  The mail sent then says the account exists and links to sign-in and reset, so the response
  reveals nothing (T33).

**Tokens by mail**
- Each is 32 random bytes; only its SHA-256 is stored. It is single-use: consumed with
  `UPDATE … SET used_at = now() WHERE used_at IS NULL AND expires_at > now() RETURNING …`.
- Lifetimes: verify 24 h, reset 30 min, email change 1 h.
- The token travels in the URL **fragment** (`/reset#token=…`). Browsers never send the
  fragment to the server or proxies, and the SPA posts it to the API.
- A reset signs the user out everywhere. An email change verifies the new address and notifies
  the old one.

**Mail**
- SMTP from settings (`CLASSBEAT_SMTP_HOST`, port, sender, TLS mode `starttls`/`tls`/`none`),
  with the password from the secret file `smtp_password`.
- `none` is refused unless the host is loopback or the compose `mailpit` service.
- Links are built from `CLASSBEAT_PUBLIC_BASE_URL`, never from the `Host` header (T37).
- Mail is sent after the response (FastAPI background task), so the response timing doesn't
  depend on whether a mail was sent. Send failures are logged without the recipient or body.

**OAuth (Google, GitHub, Microsoft)**
- Authlib's `AsyncOAuth2Client` (httpx), with our own state storage in `oauth_flows`. No
  Starlette session middleware.
- **Start:** `POST /api/auth/oauth/{provider}/start` (CSRF-checked) creates the flow:
  - `state` (32 bytes) and the PKCE verifier (S256), plus a nonce for OIDC;
  - an expiry of 10 minutes;
  - a flow id, set as the `__Host-classbeat_oauth` cookie (`SameSite=Lax`, 10 minutes).
  It returns the authorization URL.
- **Callback:** `GET /oauth/{provider}/callback`. Every provider has its own path, which
  prevents mix-up attacks (T38).
  - Refused unless the cookie's flow, `state`, `provider` and expiry all match.
  - The flow row is deleted before the code is exchanged, so a replayed callback finds nothing
    (T2).
- **Google** (OIDC):
  - The ID token's signature is checked against Google's JWKS (cached), along with `iss`,
    `aud`, `exp`, `iat` and `nonce`.
  - The subject is `sub`, and the email counts as verified only if `email_verified` is true.
- **Microsoft** (OIDC, `common` endpoint):
  - The same checks, except that `iss` must equal `https://login.microsoftonline.com/{tid}/v2.0`
    for the token's own `tid`.
  - The subject is `{tid}/{oid}`.
  - The email is **always treated as unverified**, because Microsoft doesn't guarantee it.
- **GitHub** (OAuth2 with PKCE):
  - The subject is the numeric `id`, never the login name.
  - The email is the primary address from `/user/emails`, and only if `verified` is true.
- A provider is enabled only when its client id setting and its `oauth_<provider>_client_secret`
  secret file both exist. Client secrets are never logged. The outbound hosts are the provider
  endpoints only (T19).

**Callback outcomes** (identity key `(provider, subject)`, never email, T1):

| Situation | Result |
|---|---|
| Identity exists | Sign in to its user |
| Flow purpose `link` (started signed in, with recent auth) | Link to that user. A conflict (identity already on another user) is refused |
| Flow purpose `reauth` | Must resolve to the signed-in user. Then `reauth_at = now()`. `prompt=login` / `max_age=0` is sent to the provider |
| New identity, the provider email is verified and equals an existing user's email | **Nothing is created or linked.** The page says an account with this email exists, and to sign in to it and link from the account page |
| New identity, otherwise | Create a user (needs a valid invite carried in the flow). The email counts as verified only if the provider verified it; otherwise a verification mail is sent |

- Unlinking needs recent auth. The last sign-in method (the only identity when the user has no
  password, or the password when they have no identity) can't be removed.

### 6. Recent authentication

`reauth_at` within **10 minutes** is needed to:
- link or unlink an identity;
- set or change the password, or change the email;
- enroll or remove TOTP, or regenerate recovery codes;
- connect or disconnect MOWL, Spotify or Swedebeat (M2, M4);
- delete the account.

Re-authentication is one of the following, and each rotates the session:
- the password (`POST /api/auth/reauth`);
- an OAuth `reauth` flow;
- for admins, a TOTP code.

### 7. Admins and MFA

- **Role:** `users.role`. An admin's role only takes effect in a session that has passed TOTP
  (`mfa_at`, §2).
- **Becoming admin:**
  - `CLASSBEAT_ADMIN_EMAILS` grants it at sign-in, only to a user whose email is verified
    (T28).
  - `python -m classbeat admin grant <email>` (run with `docker compose exec web …`) grants it
    on the command line.
  - Both write an `auth_events` row.
  - After a grant, every admin route answers `mfa_required` until TOTP is enrolled.
- **TOTP** (RFC 6238 via `pyotp`): SHA-1, 6 digits, 30 s period, ±1 step of tolerance.
  - The secret is 160 bits.
  - Enrollment shows a QR code (rendered server-side as SVG, no third-party host) and the key,
    and is confirmed by one valid code.
  - **Replay protection:** a code is accepted only if its time step is greater than `last_step`,
    which is then updated.
- **Recovery codes:** 10 codes of 10 base32 characters, shown once and stored as HMAC. Each
  can be used once and works in place of a TOTP code.
- **Step-up:** sensitive admin actions (from M5: grants, codes, flags, disabling users) need a
  TOTP code within **5 minutes** (`stepup_at`). M1 ships `GET /api/admin/whoami` to prove the
  gate; the admin area itself is M5.

### 8. Keys

- One secret file, `app_secret_key` (at least 32 random bytes, created by
  `scripts/worktree.sh init`).
- HKDF-SHA256 derives separate keys from it, with info strings `csrf`, `rate-limit`,
  `recovery-codes` and `totp-v1`.
- **TOTP secrets** are encrypted with AES-256-GCM (`cryptography`), with AAD `user_id|totp` and
  `key_id = "app-v1"`. M2's envelope encryption (§7 of the roadmap) re-encrypts them under its
  scheme, and `key_id` tells the two apart.
- Rotating `app_secret_key` invalidates the CSRF tokens and rate-limit buckets (harmless). For
  TOTP secrets it needs the M2 scheme; until then, rotation means re-enrolling TOTP.

### 9. Rate limits

Fixed windows are counted in `rate_limits` with one upsert per check. No Redis.
- **Bucket keys** are an HMAC of `(action, scope, value)`, so the table holds no email or IP in
  clear.
- **When a limit trips:** the request gets 429 with `Retry-After` and the same generic message
  as other failures. There is no permanent lockout, so an attacker can't lock a victim out
  (T3).
- **Client IP:** the peer address. `X-Forwarded-For` is used only when the peer is in
  `CLASSBEAT_TRUSTED_PROXIES` (Caddy on the VPS; empty by default).

| Action | Per account | Per IP |
|---|---|---|
| Password sign-in, re-auth | 10 / 15 min | 30 / 15 min |
| Sign-up | — | 5 / hour |
| Verification, reset and email-change mail | 3 / hour | 10 / hour |
| OAuth start and callback | — | 30 / 15 min |
| TOTP and recovery codes | 5 / 5 min | 20 / 5 min |

### 10. Logging

- Session, CSRF and OAuth cookies, the `Authorization` and `X-CSRF-Token` headers, query
  strings on `/oauth/*`, and request bodies on `/api/auth/*` are never logged.
- `auth_events` record the kind (`sign_in`, `sign_in_failed`, `mfa_failed`, `admin_granted`,
  …) and never a password, token or code.
- A log-redaction test plants each kind of secret and greps the captured logs (T7).

### 11. Tests and fakes

- No test reaches the network.
- **Unit and integration tests** run the providers through an in-process fake (an httpx
  `MockTransport`) with its own signing key and JWKS. The fake can issue every bad token the
  tests need: wrong signature, `aud`, `iss`, `nonce`, expiry, or an unverified email.
- **E2E** adds a small fake OIDC provider service under a compose profile `test`. Its endpoints
  are configured only when `CLASSBEAT_ENVIRONMENT=test`; in any other environment the provider
  endpoints are the real ones and can't be overridden.
- Mail is read from mailpit's API in E2E and from an in-process SMTP fake in unit tests.

## Alternatives

- **Owned account tables with `SECURITY DEFINER` lookups.** Rejected for now (§1). It needs the
  privileged migration step, and against injection it adds nothing beyond hashing. It is still
  open if a later need makes it worthwhile.
- **Signed stateless session cookies (JWT).** Rejected: a session couldn't be revoked, idle
  expiry and "sign out everywhere" would need a server table anyway, and a signing key would
  leak more than one session.
- **Starlette `SessionMiddleware` for OAuth state.** Rejected: it keeps state in a client-side
  signed cookie, which the server can't make single-use.
- **Linking accounts by verified email automatically.** Rejected by the roadmap (T1).
- **Lockout after N failures.** Rejected: an attacker could use it to lock a victim out.
  Rate-limit windows with `Retry-After` are used instead.

## Consequences

- **The M1 split stands:**
  - #36: tables and migration, guard 5, sessions, `current_context`, CSRF, rate limits, the purge
    task, `admin grant`, and `app_secret_key` in `worktree.sh init`;
  - #37: email and password, mail;
  - #38: OAuth with the fake provider;
  - #39: TOTP, recovery codes, step-up and recent auth;
  - #40: frontend pages and E2E with the fake OIDC service.
- **New dependencies:** `argon2-cffi`, `authlib`, `httpx`, `pyotp`, `cryptography`, and a QR-code
  SVG generator. They are pinned and audited like the rest.
- **Threat model:** T1 and T4 now cite this ADR, and T33 to T38 are added. T2, T3, T24, T27
  and T28 already state what §3 to §9 implement.
- **For M2:** the M2 encryption work takes over the TOTP secrets (§8). Its connect flows reuse
  `require_recent_auth`.
