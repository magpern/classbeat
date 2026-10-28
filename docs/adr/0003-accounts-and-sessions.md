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
`SYSTEM_TABLES` with a reason.
- **Where they're defined:** in `classbeat.auth.tables`.
- **Who may touch them:** only `classbeat.auth` (and migrations and tests). The worker's purge
  task and the CLI call functions in `classbeat.auth`; they never touch the tables directly.
- **Guard 8** (new, next to guards 1 to 7 of ADR 0002 §6) fails on any other import of that
  module or of its table objects, and on raw SQL that names those tables outside
  `classbeat.auth`.
- **Foreign keys:** owned tables later reference `users(id)`
  (`owner_id … REFERENCES users (id) ON DELETE CASCADE`). No system table references an owned
  one.

Without RLS, isolation between users inside `classbeat.auth` rests on two rules, both tested:
- **User-scoped queries.** Every query made for a signed-in user filters on the `user_id` of
  that request's session (from `current_context`, §2), never on an id from the request alone.
  This covers listing or revoking sessions, unlinking an identity, and TOTP and recovery codes.
- **404 for foreign ids.** Every auth endpoint that takes an id (a session, an identity) answers
  404 for another user's id, the same as for an unknown one. This extends ADR 0002's guard 7
  tests to these endpoints.

Why not owned tables with `SECURITY DEFINER` lookups:
- **What RLS adds here is small.** It would catch a missing filter in those few queries, which
  the 404 tests cover too.
- **It doesn't help against SQL injection**, because the app role can set `app.user_id` itself
  (T26).
- **What protects the data instead is how it's stored:** every token only as a hash, passwords
  as argon2id, TOTP secrets encrypted (§8).
- **ADR 0002 stays unchanged**, and M1 needs no privileged migration step.

**Every user has a verified email.** An account is created only once its email has been proven:
- **By a mail link** (§5, pending sign-ups);
- **Or by a provider that states it verified the email** (Google, GitHub).

There is no unverified-account state, so nobody can create or claim an account for an address
they don't control (T39).

| Table | Holds | Secrets stored as |
|---|---|---|
| `users` | `id`, `email`, `email_normalized` (unique), `email_verified_at` (not null), `password_hash` (nullable), `role` (`user`/`admin`), `disabled_at`, `created_at` | argon2id hash |
| `identities` | `id`, `user_id`, `provider`, `subject`, `email_at_link`, `created_at`; `UNIQUE (provider, subject)` | none |
| `sessions` | `id`, `user_id`, `token_hash` (unique), `created_at`, `last_seen_at`, `expires_at` (absolute), `reauth_at`, `mfa_at`, `stepup_at`, `user_agent` (truncated), `ip` | SHA-256 of the token |
| `known_devices` | `user_id`, `device_hash`, `created_at`, `last_used_at` | SHA-256 |
| `pending_signups` | `token_hash` (unique), `email`, `email_normalized`, `password_hash` (nullable), `provider`, `subject` (both nullable), `invite_id`, `expires_at` | SHA-256; argon2id |
| `auth_tokens` | `purpose` (`reset_password`, `change_email`), `token_hash` (unique), `user_id`, `new_email`, `email_at_issue`, `expires_at`, `used_at` | SHA-256 |
| `oauth_flows` | `flow_hash` (unique), `provider`, `purpose` (`sign_in`, `link`, `reauth`), `state_hash`, `nonce`, `pkce_verifier`, `user_id` (for `link`/`reauth`), `invite_id`, `return_to`, `expires_at` | the flow id and state as SHA-256; the verifier and nonce are kept as given, live ≤ 10 min and are deleted on use |
| `invites` | `code_hash` (unique), `created_by` (nullable: the CLI), `used_by`, `expires_at`, `used_at` | SHA-256 |
| `mfa_totp` | `user_id` (unique), `secret_ciphertext`, `nonce`, `key_id`, `confirmed_at`, `last_step` | AES-256-GCM (§8) |
| `mfa_recovery_codes` | `user_id`, `code_hash`, `used_at` | HMAC-SHA256 (§8) |
| `rate_limits` | `bucket_hash`, `window_start`, `count`; primary key `(bucket_hash, window_start)` | HMAC of the bucket key |
| `auth_events` | `user_id` (nullable), `kind`, `ip`, `at`, `detail` (no secrets) | none |

- **Email normalization:** `email_normalized` is the Unicode case-fold of the whole address
  after trimming. It's used for lookup and uniqueness only; mail goes to `email` as typed.
- **Purge:** an hourly task in the worker deletes expired or used rows:
  - `sessions`, both idle-expired and absolute-expired;
  - `pending_signups`, `auth_tokens`, `oauth_flows` and `rate_limits` windows;
  - used and expired invites, after 30 days;
  - `known_devices` unused for 90 days;
  - `auth_events`, and session `ip` values, after 30 days (T22).
- **Session identifiers:** only the `id` and `token_hash` columns identify a session. The cookie
  value itself is never stored or logged.

### 2. From request to `UserContext`

`classbeat.auth` is the web issuer of `UserContext` (ADR 0002 §1). One FastAPI dependency,
`current_context`, is the only path:

1. Read the `__Host-classbeat_session` cookie (§3). Missing → anonymous.
2. Look up `sessions` by `SHA-256(token)` in a `system_session`; join `users`.
3. Refuse if the session is past `expires_at`, idle longer than the idle limit, or the user is
   disabled. A refused session is deleted and the cookie cleared.
4. Update `last_seen_at` at most once a minute.
5. Build `UserContext(user_id, role, …)`. `role` is `admin` only if the user's role is `admin`
   **and** this session passed TOTP within the last 12 hours (`mfa_at`, §7); otherwise `user`.

Routes declare what they need through dependencies built on it:
- `require_user`;
- `require_recent_auth`, within 10 min (§6);
- `require_admin`, with MFA done;
- `require_stepup`, within 5 min.

An anonymous request to a protected route gets 401. A signed-in user without the needed level
gets 403 with a machine-readable reason (`reauth_required`, `mfa_required`, `stepup_required`),
so the SPA can prompt.

### 3. Sessions and cookies

- **Token:** 32 random bytes (`secrets.token_urlsafe(32)`), server-issued only. It is never
  accepted from a URL, or from any header other than `Cookie`.
- **Cookie** `__Host-classbeat_session`:
  - `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`, no `Domain`;
  - `Max-Age` is the session's remaining absolute lifetime.
- **Local development:**
  - Chromium and Firefox accept `Secure` and `__Host-` cookies on `http://127.0.0.1` and
    `http://localhost`, so local development and Playwright (Chromium) need no exception.
  - There is no setting to drop `Secure`. Safari isn't supported for local development.
  - Loopback cookies aren't isolated by port (dev only).
- **Limits:** idle 7 days, absolute 30 days (settings, with these defaults).
- **Rotation:** a new token, in a new row with the old one deleted, after any of these:
  - sign-in;
  - MFA verification;
  - re-authentication.

  The new row keeps the old `expires_at` (rotation never extends a session past its 30 days)
  and carries over `mfa_at` and `reauth_at` unless the rotation sets them. There is no grace
  period: a request in flight with the old token gets 401 and the SPA retries once after
  refetching the session.
- **Revocation:** these revoke every other session of the user:
  - a password change or reset;
  - an email change;
  - a role change (CLI).

  "Sign out everywhere" deletes them all, and sign-out deletes the row and clears the cookie.
- **Known devices:** a successful sign-in sets `__Host-classbeat_device`, 32 random bytes kept
  for 1 year, stored as a hash per user. It is used only to exempt the user from the
  per-account rate caps (§9).

### 4. CSRF, origin and redirects

Every state-changing request (`POST`, `PUT`, `PATCH`, `DELETE`) must pass two checks. The few
`GET` callbacks (OAuth) are protected by their state instead (§5).

- **Origin check:**
  - `Origin` must equal the public origin, taken from `CLASSBEAT_PUBLIC_BASE_URL`.
  - When `Origin` is absent, `Sec-Fetch-Site` must be `same-origin`; with neither header, the
    request is refused.
  - Local development uses exactly that origin, so the Vite dev server proxies the API under
    its own origin and that origin is configured.
- **CSRF token:** `GET /api/auth/session` returns the token that applies and sets the
  anonymous cookie if missing. The `X-CSRF-Token` header must match:
  - when the request carries a valid session: `HMAC(csrf_key, session.id)`, even on routes
    that also work anonymously;
  - when it doesn't (sign-in, sign-up, reset forms): the value of the `__Host-classbeat_csrf`
    cookie (random, `Secure`, `SameSite=Strict`, readable by JavaScript), sent by double submit.
  - Rotation changes the session id and so the token; the SPA refetches it after sign-in,
    MFA and re-auth.
- **No sign-in from a mail link:**
  - Opening a link from a mail (sign-up confirmation, reset, email change) never signs the
    browser in, so a link from the attacker's own mail can't be used for login CSRF.
  - The user signs in afterwards.
- **Caching:** API responses are `Cache-Control: no-store`.
- **Redirects (T36):**
  - `return_to` (after sign-in) is accepted only as a relative path that starts with a single
    `/` and has no scheme, host or backslash.
  - It is checked again before use.

### 5. Sign-in methods

**Passwords**
- **Hashing:** argon2id via `argon2-cffi`, with memory 19 MiB, time 2 and parallelism 1 (the
  OWASP minimum). Hashes are rehashed on sign-in when these parameters change.
- **Rules:** 12 to 128 characters after NFKC normalization, with no other composition rules.
- **Breached-password check (deferred):** the HIBP range API would mean a new outbound host,
  and the beta is invite-only. This is an accepted risk, revisited at M6.
- **Sign-in failures:** every failure gets the same message. For an unknown email, or a user
  without a password, sign-in still runs one argon2 verification against a fixed dummy hash, so
  the timing matches. A disabled user gets the same message as a wrong password.

**Sign-up with a password (pending until the email is proven)**
- **Invite gate:** sign-up is invite-only while `CLASSBEAT_SIGNUP_MODE=invite` (the default;
  `closed` and `open` also exist). The invite is checked first, and a bad invite gets its own
  error.
- **Response:** with a valid invite, both branches hash the password, answer "check your inbox"
  and send one mail after the response:
  - **New email:** a `pending_signups` row is stored, and the mail holds a confirmation link
    valid for 24 h.
  - **Existing email:** nothing is stored, and the mail says the account exists and links to
    sign-in and reset.
  - No account exists yet, and the invite isn't consumed (T33).
- **Confirmation** (`POST /api/auth/signup/confirm` with the token), in one transaction:
  1. Consume the pending row.
  2. Consume the invite (`UPDATE invites SET used_at … WHERE used_at IS NULL AND expires_at > now()
     RETURNING`).
  3. Create the user with `email_verified_at = now()`.

  If the email was taken in the meantime, nothing is created and the page says to sign in. The
  browser is not signed in (§4).

**Mail tokens** (sign-up confirmation, reset, email change)
- **Format:** 32 random bytes; only the SHA-256 is stored.
- **Single use:** a token is consumed with
  `UPDATE … SET used_at = now() WHERE used_at IS NULL AND expires_at > now() RETURNING …`, or a
  `DELETE … RETURNING` for pending sign-ups.
- **Lifetimes:** sign-up confirmation 24 h, reset 30 min, email change 1 h.
- **Transport:** the token travels in the URL **fragment** (`/reset#token=…`). Browsers never
  send the fragment to the server or to proxies. The SPA posts it to the API and then removes
  it with `history.replaceState`.
- **Reset:** completing a reset signs the user out everywhere.
- **Email change:** the new address is verified and the old one notified. Confirming rechecks
  that the new address is still free and that `users.email` still equals `email_at_issue`.
- **Invalidation:** a password change, a reset or an email change deletes the user's
  outstanding reset and email-change tokens.

**Mail delivery**
- **Settings:** SMTP from `CLASSBEAT_SMTP_HOST`, port, sender and TLS mode (`starttls`, `tls` or
  `none`), with the password from the secret file `smtp_password`. `none` is refused unless the
  host is loopback or the compose `mailpit` service.
- **Links:** built from `CLASSBEAT_PUBLIC_BASE_URL`, never from the `Host` header (T37).
- **Timing:** mail is sent after the response (FastAPI background task), so the response timing
  doesn't depend on whether a mail was sent. A failed send is logged without the recipient or
  body.

**OAuth (Google, GitHub, Microsoft)**

Authlib's `AsyncOAuth2Client` (httpx) is used with our own state storage in `oauth_flows`; there
is no Starlette session middleware.

- **Start:** `POST /api/auth/oauth/{provider}/start`, CSRF-checked:
  - It creates the flow: `state` (32 bytes), a PKCE verifier (S256), a nonce for OIDC, and an
    expiry of 10 minutes.
  - It sets the flow id as the `__Host-classbeat_oauth` cookie (`SameSite=Lax`, 10 minutes).
  - It returns the authorization URL with `response_mode=query`, because a POST callback
    wouldn't carry the Lax cookie.
- **Callback:** `GET /oauth/{provider}/callback`:
  - Each provider has its own path (T38). The route is registered before the SPA catch-all.
  - The flow is taken with `DELETE FROM oauth_flows WHERE flow_hash = … AND expires_at > now()
    RETURNING …` before the code is exchanged, so a replayed callback finds nothing (T2).
  - The callback is refused unless the flow exists and the `state` and `provider` match. The
    flow cookie is cleared.
  - For `link` and `reauth`, the request's current session must belong to `flow.user_id`.
- **ID tokens (OIDC):** checked against the provider's JWKS (cached), RS256 only (`none` and HS*
  refused), with `iss`, `aud`, `exp`, `iat` and `nonce`.
  - **Google:** the subject is `sub`. The email counts as verified only if `email_verified` is
    true.
  - **Microsoft** (`common` endpoint): `iss` must equal
    `https://login.microsoftonline.com/{tid}/v2.0` for the token's own `tid`. The subject is
    `{tid}/{oid}`. The email is **always treated as unverified**, because anyone can create a
    tenant claiming any address.
- **GitHub** (OAuth2 with PKCE): the subject is the numeric `id`, never the login name. The
  email is the primary address from `/user/emails`, and only if `verified` is true.
- **Re-authentication:** a `reauth` flow sends `prompt=login` and `max_age=0` to OIDC providers
  and requires `auth_time` within the last 5 minutes. GitHub can't force a re-login, so a
  GitHub re-authentication only proves a live GitHub session; GitHub users are offered the
  password first when they have one.
- **Enabling a provider:** a provider is on only when both its client id setting and its
  `oauth_<provider>_client_secret` secret file exist. Client secrets are never logged.
- **Outbound hosts:** only the provider endpoints (T19).

**Callback outcomes** (identity key `(provider, subject)`, never email, T1):

| Situation | Result |
|---|---|
| Identity exists | Sign in to its user (unless the user is disabled: the generic failure) |
| Flow purpose `link` | Link to the flow's user (who passed recent auth before the start); an identity already on another user is refused |
| Flow purpose `reauth` | The identity must belong to the flow's user; then `reauth_at = now()` and the session rotates |
| New identity, the provider verified the email, and it matches an existing user | **Nothing is created or linked.** The page says an account with this email exists: sign in to it and link from the account page. Only someone in control of that mailbox can get here, so nothing leaks |
| New identity, the provider verified the email, and no user has it | Create the user (verified) and the identity, consuming the invite carried in the flow, in one transaction |
| New identity, the email is unverified or missing (always Microsoft) | The page asks for an email address and treats it exactly like a password sign-up: a `pending_signups` row with `provider` and `subject`, mail to that address, "check your inbox" in every case. The account and the identity are created only from the confirmation link |

- **Unlinking** needs recent auth. The last sign-in method can't be removed: the only identity
  of a user without a password, or the password of a user without an identity.

### 6. Recent authentication

`reauth_at` within **10 minutes** is needed to:
- link or unlink an identity;
- set or change the password, or change the email;
- enroll or remove TOTP, or regenerate recovery codes;
- connect or disconnect MOWL, Spotify or Swedebeat (M2, M4);
- delete the account (#37: it deletes the `users` row, and the foreign keys cascade).

Re-authentication is one of the following, and each rotates the session:
- the password (`POST /api/auth/reauth`);
- an OAuth `reauth` flow (§5);
- for admins, a TOTP code.

### 7. Admins and MFA

- **Role:** `users.role`. An admin's role only takes effect in a session that passed TOTP within
  the last 12 hours (§2).
- **Becoming the first admin.** The commands run as `python -m classbeat …` with
  `docker compose exec web …`. That is how this ADR reads the roadmap's `app admin grant`;
  `python -m classbeat` with no arguments still starts the server.
  1. `python -m classbeat invite create [--expires 7d]` prints one invite code (16 random bytes,
     128 bits, shown once; `created_by` is null). This is the only way to make invites before
     the M5 admin area.
  2. The owner signs up with an address in `CLASSBEAT_ADMIN_EMAILS` and confirms it.
  3. At the next sign-in, that address gets `role = admin`.
     `python -m classbeat admin grant <email>` does the same for an existing user.
  4. Every admin route answers `mfa_required` until TOTP is enrolled.

  Grants and revocations (`admin revoke <email>`) write an `auth_events` row and revoke the
  user's sessions. Removing an address from `ADMIN_EMAILS` doesn't demote anyone; use `admin
  revoke`.
- **TOTP** (RFC 6238 via `pyotp`):
  - **Parameters:** SHA-1, 6 digits, a 30 s period, ±1 step of tolerance, and a 160-bit secret.
  - **Enrollment:** shows a QR code (an SVG rendered server-side, no third-party host) and the
    key. It is confirmed by one valid code.
  - **Replay protection:** a code is accepted only by
    `UPDATE mfa_totp SET last_step = :step WHERE user_id = … AND last_step < :step RETURNING`.
- **Recovery codes:**
  - There are 10, each 10 base32 characters. They are shown once and stored as an HMAC.
  - Each is used once, by an atomic conditional `UPDATE … WHERE used_at IS NULL RETURNING`, and
    works in place of a TOTP code.
  - Every failed MFA attempt writes `mfa_failed`. Five in an hour mail the user.
- **Step-up:** sensitive admin actions (from M5: grants, codes, flags, disabling users) need a
  TOTP code within **5 minutes** (`stepup_at`).
- M1 ships `GET /api/admin/whoami` to prove the gate; the admin area itself is M5.

### 8. Keys

- **The secret:** one secret file, `app_secret_key` (at least 32 random bytes, created by
  `scripts/worktree.sh init`).
- **Derived keys:** HKDF-SHA256 (fixed salt `classbeat-v1`) derives separate 32-byte keys with
  the info strings `csrf`, `rate-limit`, `device`, `recovery-codes` and `totp-v1`.
- **TOTP secrets:** encrypted with AES-256-GCM (`cryptography`), a random 96-bit nonce per
  encryption stored alongside, AAD `user_id|totp`, and `key_id = "app-v1"`.
  - M2's envelope encryption (§7 of the roadmap) re-encrypts them under its scheme, and
    `key_id` tells the two apart.
- **Rotating `app_secret_key`:**
  - It invalidates CSRF tokens, rate-limit buckets and recovery codes.
  - For TOTP secrets it needs the M2 scheme; until then, rotation means re-enrolling TOTP.

### 9. Rate limits

Fixed windows are counted in `rate_limits` with one upsert per check. There is no Redis.
- **Bucket keys** are an HMAC of `(action, scope, value)`, so the table holds no email or IP in
  the clear. Unknown emails are bucketed by their normalized form, the same as known ones.
- **What counts:** sign-in, re-auth and MFA limits count **failures only**. Sign-up and mail
  limits count requests.
- **When a limit trips:** the request gets 429 with `Retry-After`. There is no permanent lockout.
- **Client IP:** the peer address. When the peer is in `CLASSBEAT_TRUSTED_PROXIES` (Caddy on the
  VPS; empty by default), it is the right-most `X-Forwarded-For` entry that is not a trusted
  proxy. uvicorn's own proxy-header handling is turned off.

| Action | Per (account, IP) | Per account, all IPs | Per IP |
|---|---|---|---|
| Password sign-in and re-auth failures | 10 / 15 min | 50 / hour, except from a known device | 30 / 15 min |
| TOTP and recovery code failures | 5 / 5 min | 20 / hour | 20 / 5 min |
| Sign-up requests | — | — | 5 / hour |
| Reset and email-change mail | 3 / hour | 10 / hour | 10 / hour |
| OAuth start and callback | — | — | 30 / 15 min |

**Residual risk (T3).** Someone who knows a victim's email can, from many IPs, exhaust the
per-account cap. The victim can then sign in only from a known device or by OAuth, and reset
mails pause for up to an hour. This is accepted for the beta, where accounts are few and
invited; the M6 security review revisits it.

### 10. Logging

- **Never logged:**
  - the session, device, CSRF and OAuth cookies;
  - the `Authorization` and `X-CSRF-Token` headers;
  - query strings on `/oauth/*`;
  - request bodies on `/api/auth/*`.
- **`auth_events`** record the kind (`sign_in`, `sign_in_failed`, `mfa_failed`,
  `admin_granted`, …) and never a password, token or code.
- **Redaction test:** a log-redaction test plants each kind of secret and greps the captured
  logs (T7).

### 11. Tests and fakes

- **No test reaches the network.**
- **Unit and integration tests** run the providers through an in-process fake: an httpx
  `MockTransport` with its own signing key and JWKS. The fake can issue every bad token the
  tests need: wrong signature or algorithm, `aud`, `iss`, `nonce`, `auth_time`, expiry, and
  unverified or missing emails.
- **E2E tests** add a small fake OIDC provider service under the compose profile `test`.
  - Its endpoints are configured only when `CLASSBEAT_ENVIRONMENT=test`.
  - In any other environment, provider endpoints are the real ones and can't be overridden.
- **Mail** is read from mailpit's API in E2E, and from an in-process SMTP fake in unit tests.

## Alternatives

- **Owned account tables with `SECURITY DEFINER` lookups.** Rejected for now (§1). It needs the
  privileged migration step, and against injection it adds nothing beyond hashing. It remains an
  option if a later need makes it worthwhile.
- **Unverified accounts that are verified later.** Rejected. They allow an account to be
  pre-hijacked by whoever registers the address first, and they leak which emails exist when a
  second sign-up collides (T39, T33).
- **Signed stateless session cookies (JWT).** Rejected:
  - A session couldn't be revoked.
  - Idle expiry and "sign out everywhere" would need a server table anyway.
  - A leaked signing key would expose every session, not one.
- **Starlette `SessionMiddleware` for OAuth state.** Rejected: it keeps the state in a signed
  client-side cookie, which the server can't make single-use.
- **Linking accounts by verified email automatically.** Rejected by the roadmap (T1).
- **Lockout after N failures.** Rejected: an attacker could use it to lock a victim out. Instead,
  failure-only windows per account and IP, and a capped per-account limit with a known-device
  exemption.

## Consequences

- **The M1 split, with these acceptance criteria:**
  - **#36:** tables and migration, guard 8, sessions and known devices, `current_context`, CSRF,
    rate limits, the purge task, the `invite create`, `admin grant` and `admin revoke` commands,
    and `app_secret_key` in `worktree.sh init`.
    - Isolation between users is tested through user-scoped queries and the 404 tests for
      foreign session ids (§1), not through RLS.
  - **#37:** email and password, pending sign-ups, reset, email change, account deletion, mail.
  - **#38:** OAuth with the fake provider, including the pending-sign-up path for unverified
    emails.
  - **#39:** TOTP, recovery codes, step-up and recent auth.
    - TOTP secrets are protected by AES-GCM at rest from M1 (§8), not by RLS.
  - **#40:** frontend pages, and E2E with the fake OIDC service. Invites for E2E come from
    `invite create`.
- **New dependencies:** `argon2-cffi`, `authlib`, `httpx`, `pyotp`, `cryptography`, and a
  QR-code SVG generator. They are pinned and audited like the rest.
- **Threat model:** T1, T3, T4 and T33 cite this ADR, and T34 to T39 are added. T2, T24, T27 and
  T28 already state what §3 to §9 implement.
- **For later milestones:** M2's encryption takes over the TOTP secrets (§8), and its connect
  flows reuse `require_recent_auth`. The M6 security review revisits the per-account
  denial-of-service risk (§9).
