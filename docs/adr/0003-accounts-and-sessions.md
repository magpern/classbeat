# ADR 0003: Accounts, sessions and sign-in

Status: Accepted

## Context

M1 adds accounts (roadmap §3, P0): email and password, Google, GitHub and Microsoft sign-in,
server-side sessions, CSRF protection, rate limits, recent authentication, and admin TOTP MFA.
Sign-in happens before a `UserContext` exists, so the account data must be readable before
the user is known, as ADR 0002 §3 anticipated (system tables reached only through
`classbeat.auth`). This ADR fixes the design so #36 to #40 need no decisions of their own.

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

Every table has an `id uuid` primary key (server default) unless a key is named. Foreign keys to
`users(id)` are `ON DELETE CASCADE`, except those marked *set null*, which are
`ON DELETE SET NULL`. The `invite_id` foreign keys of `pending_signups` and `oauth_flows` are
`ON DELETE CASCADE`.

| Table | Holds | Secrets stored as |
|---|---|---|
| `users` | `email`, `email_normalized` (unique), `email_verified_at` (not null), `password_hash` (nullable), `role` (`user`/`admin`), `disabled_at`, `created_at` | argon2id hash |
| `identities` | `user_id`, `provider`, `subject`, `email_at_link`, `created_at`; `UNIQUE (provider, subject)` | none |
| `sessions` | `user_id`, `token_hash` (unique), `created_at`, `last_seen_at`, `expires_at` (absolute), `reauth_at`, `mfa_at`, `stepup_at`, `user_agent` (truncated), `ip` | SHA-256 of the token |
| `known_devices` | `user_id`, `device_hash` (unique), `created_at`, `last_used_at` | HMAC (`device` key, §8) |
| `pending_signups` | `token_hash` (unique; null until the email is entered), `email`, `email_normalized` (both null until then; not unique: each row has its own token and the first confirmation wins), `password_hash` (nullable), `provider`, `subject`, `browser_hash` (all three set together or null), `invite_id` → `invites`, `expires_at` | SHA-256; argon2id |
| `auth_tokens` | `purpose` (`reset_password`, `change_email`), `token_hash` (unique), `user_id`, `new_email`, `email_at_issue`, `expires_at`, `used_at` | SHA-256 |
| `oauth_flows` | `flow_hash` (unique), `provider`, `purpose` (`sign_in`, `link`, `reauth`), `state_hash`, `nonce`, `pkce_verifier`, `user_id` (for `link`/`reauth`), `invite_id` → `invites`, `return_to`, `expires_at` | the flow id and state as SHA-256; the verifier and nonce are kept as given, live ≤ 10 min and are deleted on use |
| `invites` | `code_hash` (unique), `created_by` (*set null*; null for the CLI), `used_by` (*set null*), `expires_at`, `used_at` | SHA-256 |
| `mfa_totp` | `user_id` (unique), `secret_ciphertext`, `nonce`, `key_id`, `confirmed_at`, `last_step` | AES-256-GCM (§8) |
| `mfa_recovery_codes` | `user_id`, `code_hash`, `used_at` | HMAC-SHA256 (§8) |
| `rate_limits` | key `(bucket_hash, window_start)`, `count` | HMAC of the bucket key |
| `auth_events` | `user_id` (*set null*), `kind`, `ip`, `at`, `detail` (no secrets) | none |

Deleting a user (§6) thus removes all their auth rows except `auth_events`, which keep a null
`user_id` until the 30-day purge. Cascades into owned tables work under `FORCE` RLS, because
Postgres runs referential actions without row security.

- **Email normalization:** `email_normalized` is the address after trimming, with ASCII letters
  lowercased and everything else kept as typed. Full Unicode case folding would merge distinct
  addresses (`ß` and `ss`). It's used for lookup and uniqueness only; mail goes to `email` as
  typed. `CLASSBEAT_ADMIN_EMAILS` entries are compared in the same normalized form, exactly.
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
   disabled. A refused session is deleted and the cookie cleared. A token that matches no row
   is just anonymous: the cookie is **not** cleared, because it may be an in-flight request
   with a token that was just rotated.
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
- **Revocation:**
  - A password change or an email change revokes every other session of the user.
  - A reset, and a role change (CLI or the `ADMIN_EMAILS` grant at sign-in), revoke **all** of
    them; the sign-in that triggered the grant gets a new session.

  "Sign out everywhere" deletes them all, and sign-out deletes the row and clears the cookie.
- **Known devices:** a successful sign-in sets `__Host-classbeat_device` (`HttpOnly`, 32 random
  bytes, kept for 1 year), stored per user as `HMAC(device_key, user_id || token)`. A browser
  shared by several users holds one token, which can be a known device for each of them. It is
  used only to exempt the user from
  the per-account rate cap (§9): a stolen one grants nothing else. A reset deletes all of the
  user's known devices.

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
- **Confirmation** (`POST /api/auth/signup/confirm` with the token; for a password sign-up
  also **the chosen password**, typed again on the confirmation page, while a provider sign-up
  is bound to its browser instead, below). The password is checked against the row's hash
  first.
  A wrong password, like a confirmation in the wrong browser for a provider sign-up, leaves the
  row and the token untouched and counts as a sign-in failure (§9). So a victim who clicks a
  link for a sign-up someone else started can't confirm it, and an attacker can't confirm
  without the mailbox. Then, in one transaction:
  1. Consume the pending row.
  2. Consume the invite (`UPDATE invites SET used_at … WHERE used_at IS NULL AND expires_at > now()
     RETURNING`).
  3. Create the user with `email_verified_at = now()`.

  If the email was taken in the meantime, or the provider identity was linked elsewhere in the
  meantime (`UNIQUE (provider, subject)`), nothing is created and the page says to sign in. If
  step 2 fails (the invite was used by another sign-up, or expired), the transaction rolls
  back, the pending row stays, and the page says the invite is no longer valid. The browser is
  not signed in (§4).
- **Mail limits:** sign-up and confirmation mail are limited per normalized address as well as
  per IP (§9), so one address can't be flooded.

**Mail tokens** (sign-up confirmation, reset, email change)
- **Format:** 32 random bytes; only the SHA-256 is stored.
- **Single use:** a token is consumed with
  `UPDATE … SET used_at = now() WHERE used_at IS NULL AND expires_at > now() RETURNING …`, or a
  `DELETE … RETURNING` for pending sign-ups.
- **Lifetimes:** sign-up confirmation 24 h, reset 30 min, email change 1 h.
- **Transport:** the token travels in the URL **fragment** (`/reset#token=…`). Browsers never
  send the fragment to the server or to proxies. The SPA posts it to the API and then removes
  it with `history.replaceState`.
- **Reset (sessions):** completing a reset signs the user out everywhere.
- **Reset:** completing a reset also deletes the user's known devices. The reset page lists the
  linked sign-in methods (provider names) and lets the user remove any of them in the same
  step (no recent auth or step-up needed: the reset is the proof), so an identity someone
  else linked doesn't survive the reset.
- **Email change:**
  - **Request:** gives the same answer whether or not the new address is in use. Only the mail
    differs: "confirm" for a free address, "an account with this address exists" for a taken
    one.
  - **Confirmation:** needs a signed-in session of `auth_tokens.user_id`, so a victim clicking
    a link from someone else's account changes nothing. It rechecks that the new address is
    still free and that `users.email` still equals `email_at_issue`.
  - **Notice:** the old address is notified.
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
  - It takes an optional invite code for `sign_in`. A code that is given is checked as for a
    password sign-up (and gets its own error); its id goes into the flow, not consumed yet.
  - It creates the flow: `state` (32 bytes), a PKCE verifier (S256), a nonce for OIDC, and an
    expiry of 10 minutes.
  - It sets the flow id as the `__Host-classbeat_oauth` cookie (`HttpOnly`, `SameSite=Lax`,
    10 minutes).
  - It returns the authorization URL with `response_mode=query`, because a POST callback
    wouldn't carry the Lax cookie.
- **Callback:** `GET /oauth/{provider}/callback`:
  - Each provider has its own path (T38). The route is registered before the SPA catch-all.
  - The flow is taken with `DELETE FROM oauth_flows WHERE flow_hash = … AND expires_at > now()
    RETURNING …` before the code is exchanged, so a replayed callback finds nothing (T2).
  - The callback is refused unless the flow exists and the `state` and `provider` match. The
    flow cookie is cleared.
  - For `link` and `reauth`, the request's current session must belong to `flow.user_id`.
- **ID tokens (OIDC):** checked against the provider's JWKS (cached; refetched at most once a
  minute on an unknown `kid`), RS256 only (`none` and HS* refused), with `iss`, `aud`, `exp`,
  `iat` (60 s leeway) and `nonce`.
  - **Google:** the subject is `sub`. The email counts as verified only if `email_verified` is
    true.
  - **Microsoft** (`common` endpoint): `iss` must equal
    `https://login.microsoftonline.com/{tid}/v2.0` for the token's own `tid`. The subject is
    `{tid}/{oid}`. The email is **always treated as unverified**, because anyone can create a
    tenant claiming any address.
- **GitHub** (OAuth2 with PKCE): the subject is the numeric `id`, never the login name. The
  email is the primary address from `/user/emails`, and only if `verified` is true.
- **Re-authentication:**
  - **OIDC providers:** a `reauth` flow sends `max_age=0` (and `prompt=login` to Microsoft) and
    requires `auth_time` within the last 5 minutes. A missing or older `auth_time` fails
    closed. The Microsoft app registration adds `auth_time` as an optional claim.
  - **GitHub:** it can't force a re-login, so a GitHub re-authentication only proves a live
    GitHub session (an accepted risk).
  - **Order and fallback:** users with a password are offered it first. A user whose provider
    re-authentication keeps failing can set a password through a reset and re-authenticate
    with it.
- **Enabling a provider:** a provider is on only when both its client id setting and its
  `oauth_<provider>_client_secret` secret file exist. Client secrets are never logged.
- **Outbound hosts:** only the provider endpoints (T19).

**Callback outcomes** (identity key `(provider, subject)`, never email, T1). The flow purpose
is evaluated first, then the rows for `sign_in` from the top:

| Situation | Result |
|---|---|
| Flow purpose `link` | Link to the flow's user (who passed recent auth before the start); an identity already on another user is refused |
| Flow purpose `reauth` | The identity must belong to the flow's user; then `reauth_at = now()` and the session rotates |
| `sign_in`, identity exists | Sign in to its user (unless the user is disabled: the generic failure) |
| New identity, the provider verified the email, and it matches an existing user | **Nothing is created or linked.** The page says an account with this email exists: sign in to it and link from the account page. Only someone in control of that mailbox can get here, so nothing leaks |
| New identity, sign-up mode `closed`, or `invite` without a valid invite in the flow | **Nothing is created.** The page says an invite is needed (in `invite` mode, with a field to enter one and start again) |
| New identity, the provider verified the email, and no user has it | Create the user (verified) and the identity, consuming the invite with the same conditional `UPDATE` as a password sign-up, in one transaction; if the invite is gone by then, the transaction rolls back and the page says the invite is no longer valid |
| New identity, the email is unverified or missing (always Microsoft) | See the hand-off below |

**Hand-off for an unverified or missing provider email.** The provider identity never passes
through the client:
1. **At the callback**, the server creates a `pending_signups` row with `provider`, `subject`,
   `invite_id` and `browser_hash`. `email` and `token_hash` stay empty, and the row expires in
   15 minutes. It sets a random `__Host-classbeat_pending` cookie (`HttpOnly`, `Secure`, `SameSite=Lax`, 24 h) whose hash
   is `browser_hash`. A second pending sign-up in the same browser replaces the first.
2. **The email page** posts only an address. The server takes `provider`, `subject` and the
   invite **only from the row the cookie points to**, never from the request body. It fills in
   the email and token, extends the expiry to 24 h, and from then on treats it like a password
   sign-up (same responses, limits and invite rules; for an address that already has an
   account the row keeps no email or token and simply expires).
3. **The confirmation link** creates the account and the identity only in the browser holding
   that cookie. In any other browser it creates nothing, leaves the token unused, and says to
   finish on the device where the sign-in started.

So an unsolicited mail can't attach someone else's identity to the reader's address, and a
client can't submit someone else's `subject`.

- **Link notice:** every new link (sign-up with a provider, or `link`) mails the account's
  address, naming the provider.
- **Unlinking** needs recent auth. The last sign-in method can't be removed: the only identity
  of a user without a password, or the password of a user without an identity.

### 6. Recent authentication

`reauth_at` within **10 minutes** is needed to:
- link or unlink an identity;
- set or change the password, or change the email;
- enroll TOTP for the first time;
- connect or disconnect MOWL, Spotify or Swedebeat (M2, M4);
- delete the account (#37: it deletes the `users` row, and the foreign keys cascade).

Re-authentication is one of the following, and each rotates the session:
- the password (`POST /api/auth/reauth`);
- an OAuth `reauth` flow (§5);
- for admins, a TOTP code (which also sets `stepup_at`, so one code serves both).

**Step-up** means a TOTP or recovery code within 5 minutes (`stepup_at`). For a user whose
TOTP is confirmed, step-up is needed **in addition to** recent authentication for every action
in the list above, and for removing or replacing TOTP and regenerating recovery codes.
Otherwise a stolen password alone could swap in the attacker's TOTP, or move the account's
email to the attacker and reset it away from the owner.

TOTP is open to every user. For a non-admin it only adds this step-up protection; it gates
sign-in only for the admin role (§7).

### 7. Admins and MFA

- **Role:** `users.role`. An admin's role only takes effect in a session that passed TOTP within
  the last 12 hours (§2).
- **Becoming the first admin.** The commands run as `python -m classbeat …` with
  `docker compose exec web …`. That is how this ADR reads the roadmap's `app admin grant`;
  `python -m classbeat` with no subcommand still starts the server with `--host` and `--port`
  (optional argparse subparsers).
  1. `python -m classbeat invite create [--expires 7d]` prints one invite code (16 random bytes,
     128 bits, shown once; `created_by` is null). This is the only way to make invites before
     the M5 admin area.
  2. The owner signs up with an address in `CLASSBEAT_ADMIN_EMAILS` and confirms it (with the
     password, in the same browser as the sign-up for a provider sign-up, §5).
  3. At the next sign-in, that address gets `role = admin`.
     `python -m classbeat admin grant <email>` does the same for an existing user.
  4. Every admin route answers `mfa_required` until TOTP is enrolled.

  Grants (by the CLI or at sign-in) and revocations (`admin revoke <email>`) write an
  `auth_events` row and revoke all the user's sessions (§3).
  - **`ADMIN_EMAILS` and revocation:** removing an address from `ADMIN_EMAILS` doesn't demote
    anyone. `admin revoke` refuses while the address is still listed, because the next sign-in
    would grant it again.
  - **Before the first enrollment**, an admin's password alone reaches only `mfa_required`
    pages, but whoever enrolls first owns the factor. The owner enrolls right after the grant;
    an unexpected enrollment is visible in `auth_events` and mailed to the user.
  - **Lost authenticator:** use a recovery code, or `python -m classbeat admin mfa-reset
    <email>`. That removes the user's TOTP and recovery codes, writes `auth_events`, revokes all
    sessions, and mails the user a password-reset link. The owner resets and re-enrolls before
    anything else, because the account is back in the "whoever enrolls first" state.
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
- **Step-up:** sensitive admin actions (from M5: grants, codes, flags, disabling users) need
  step-up as defined in §6 (a TOTP or recovery code within **5 minutes**).
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
  - It invalidates CSRF tokens, rate-limit buckets, known devices and recovery codes.
  - For TOTP secrets it needs the M2 scheme; until then, rotation means re-enrolling TOTP.

### 9. Rate limits

Fixed windows are counted in `rate_limits` with one upsert per check. There is no Redis.
- **Bucket keys** are an HMAC of `(action, scope, value)`, so the table holds no email or IP in
  the clear. Unknown emails are bucketed by their normalized form, the same as known ones.
- **What counts:** sign-in, re-auth and MFA limits count **failures only**. Sign-up and mail
  limits count requests.
- **Reserve first:** every attempt first takes a slot with
  `INSERT … ON CONFLICT DO UPDATE SET count = rate_limits.count + 1 RETURNING count`, and is
  refused above the limit before any password check runs, so parallel attempts can't overshoot.
- **Release on success,** so only failures stay counted:
  - The success deletes its own (account, IP) bucket.
  - It decrements, by one, the exact per-account and per-IP rows it reserved (the same
    `window_start`), with `GREATEST(count - 1, 0)`. It never touches a newer window, so
    attempts that straddle a boundary gain nothing.
  - An attempt from a known device reserves no per-account slot at all.
- **IPv6:** IPv4-mapped addresses are unmapped first; IPv6 is bucketed by its /64 prefix.
- **Bounded hashing:** argon2 runs in a bounded thread pool (`CLASSBEAT_PASSWORD_HASH_WORKERS`,
  default 4). A request that waits more than 5 s for a worker gets 503.
- **When a limit trips:** the request gets 429 with `Retry-After`. There is no permanent lockout.
- **Client IP:** the peer address. When the peer is in `CLASSBEAT_TRUSTED_PROXIES` (Caddy on the
  VPS; empty by default), it is the right-most `X-Forwarded-For` entry that is not a trusted
  proxy. uvicorn's own proxy-header handling is turned off.

| Action | Per (account, IP) | Per account, all IPs | Per IP |
|---|---|---|---|
| Password sign-in and re-auth failures | 10 / 15 min | 50 / hour, except from a known device | 30 / 15 min |
| TOTP and recovery code failures | 5 / 5 min | 20 / hour | 20 / 5 min |
| Sign-up requests, incl. the email entered on the provider path | 3 / hour (per address) | — | 5 / hour |
| Reset and email-change mail | 3 / hour | 10 / hour | 10 / hour |
| OAuth start and callback | — | — | 30 / 15 min |

**Residual risk (T3).** Someone who knows a victim's email can, from many IPs, exhaust the
per-account cap. The victim can then sign in only from a known device or by OAuth, and reset
mails pause for up to an hour. This is accepted for the beta, where accounts are few and
invited; the M6 security review revisits it.

### 10. Logging

- **Never logged:** the session, device, CSRF and OAuth cookies; the `Authorization` and
  `X-CSRF-Token` headers; query strings on `/oauth/*` (our own access log replaces uvicorn's);
  request bodies on `/api/auth/*`.
- **`auth_events`** record the kind (`sign_in`, `sign_in_failed`, `mfa_failed`,
  `admin_granted`, …), never a password, token or code. A log-redaction test plants each kind
  of secret and greps the captured logs (T7).

### 11. Tests and fakes

No test reaches the network. Unit and integration tests run the providers through an
in-process fake (an httpx `MockTransport` with its own signing key and JWKS) that can issue
every bad token the tests need: wrong signature or algorithm, `aud`, `iss`, `nonce`,
`auth_time`, expiry, unverified or missing emails. E2E adds a fake OIDC provider service under
the compose profile `test`, configurable only when `CLASSBEAT_ENVIRONMENT=test`; elsewhere the
provider endpoints are the real ones. Mail is read from mailpit's API in E2E and from an
in-process SMTP fake in unit tests.

## Alternatives

- **Owned account tables with `SECURITY DEFINER` lookups:** rejected for now (§1). It needs the
  privileged migration step and adds nothing beyond hashing against injection.
- **Unverified accounts, verified later:** rejected. Whoever registers an address first could
  pre-hijack it, and a colliding second sign-up leaks which emails exist (T39, T33).
- **Signed stateless session cookies (JWT):** rejected. They can't be revoked, idle expiry
  and "sign out everywhere" need a server table anyway, and a leaked key exposes every session.
- **Starlette `SessionMiddleware` for OAuth state:** rejected. A signed client-side cookie can't
  be made single-use.
- **Linking by verified email automatically:** rejected by the roadmap (T1).
- **Lockout after N failures:** rejected, because it lets an attacker lock a victim out (§9).

## Consequences

- **The M1 split stands** (#36 to #40); each issue's scope and acceptance criteria follow this
  ADR. In particular, isolation inside `classbeat.auth` is tested through user-scoped queries
  and 404s for foreign ids (§1), and TOTP secrets are protected by AES-GCM at rest (§8), not by
  RLS. Before #38 merges, Google's and Microsoft's `auth_time` and `max_age` behaviour is
  checked against their current documentation (no live calls).
- **New dependencies** (pinned and audited): `argon2-cffi`, `authlib`, `httpx`, `pyotp`,
  `cryptography`, a QR-code SVG generator.
- **Threat model:** T1, T3, T4 and T33 cite this ADR; T34 to T39 are new.
- **Later:** M2's encryption takes over the TOTP secrets (§8) and its connect flows reuse
  `require_recent_auth`; M6 revisits the per-account denial-of-service risk (§9).
