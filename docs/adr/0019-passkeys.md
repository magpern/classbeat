# ADR 0019: Passkeys with cross-device sign-in (#311)

Status: Proposed (2026-11-03, issue #311; backlog after M6. A fresh Opus Reviewer reviews it in its
PR. Acceptance covers the design and the issue split; the owner answers the questions at the end.)

Related: [ADR 0002](0002-tenant-isolation.md) (system tables, guard 8),
[ADR 0003](0003-accounts-and-sessions.md) (sessions, CSRF, recent authentication, step-up, admin
TOTP, identities, reset), [ADR 0011](0011-tiers-quotas-and-admin.md) (admin step-up),
[ADR 0013](0013-ui-design-night-studio.md) (UI, CSP), [ADR 0014](0014-account-export-and-deletion.md)
(export, deletion, guard 12), the [threat model](../security/threat-model.md) (T113 to T120 claimed).

## Context

The owner asked for "QR login on classbeat" (2026-10-31): signing in on a shared or gym computer by
scanning a QR code with the phone. A custom QR flow was rejected in #311: it is security-critical
code with a known attack (the attacker shows their own code and the victim approves it).

WebAuthn passkeys already do this. On a computer without the passkey, the browser offers "use a
phone or tablet": it shows a QR code, the phone scans it, checks that the computer is physically
near over Bluetooth, asks for fingerprint or face, and hands the browser a normal signed assertion.
This is the *hybrid* transport (CTAP 2.2). The browser and the phone's operating system run all of
it; the server sees the same assertion as from a passkey on the computer itself. Passkeys are also
phishing-resistant: the browser binds every assertion to the page's origin and the relying party ID
(RP ID), so a passkey cannot be used on a look-alike domain.

Facts that shape the design:

- **Sign-in happens before a `UserContext` exists.** ADR 0003 §1 keeps all account tables as
  system tables in `classbeat.auth` (no RLS, guard 8, user-scoped queries, 404 for foreign ids).
- **The deployment** is a beta at `https://classbeat.pernemark.se` behind the owner's swag/nginx
  proxy (#341, #492). Web already takes its public origin only from `CLASSBEAT_PUBLIC_BASE_URL` (CSRF origin check, mail links,
  T37), never from `Host` or `X-Forwarded-*`.
- **Browsers refuse an IP address as an RP ID**, and WebAuthn needs a secure context. Local
  development and the e2e stack use `http://127.0.0.1:<port>` today (`localhost` is refused with
  song picking on, ADR 0009), so passkeys cannot work there without a change (§11).
- **Most synced passkeys** (iCloud Keychain, Google Password Manager) always report a signature
  counter of 0.
- **The CSP** (`classbeat.headers`) is `'self'` only and no `Permissions-Policy` header is sent, so
  `navigator.credentials` works on our origin as it is.

## Decision

### 1. What a passkey is here

A passkey is **another sign-in method**, beside the password and the linked providers (ADR 0003
§5). It is not a second factor: it does not set `mfa_at` or `stepup_at` (§8). A user may have up to
**10**. A passkey is always **in addition to** a password or a linked provider: there is no
passkey-first sign-up in this ADR and no route removes a password (none exists today, ADR 0003 §5),
so the last-method rule (§7) keeps ADR 0003's meaning and a passkey-only account cannot arise.

The whole feature sits behind `CLASSBEAT_PASSKEYS` (bool, default **false**; web only). Off, the
register, sign-in, re-auth, rename and list routes answer 404 `feature_off` and those pages show no
passkey UI. **Recovery ignores the flag:** the reset page lists and removes existing passkeys and
`admin mfa-reset` deletes them whatever the flag says (§9), so switching the flag off never hides a
planted passkey that would come back when it is switched on again. Because every user keeps a
password or a provider, switching it off strands nobody.

### 2. Library: py_webauthn

**`webauthn` (py_webauthn, Duo Labs / Cisco), BSD-3-Clause**, pinned exactly (`==`, hash-locked in
`uv.lock`, covered by pip-audit and Dependabot), used for: generating options, parsing the
responses, and `verify_registration_response` / `verify_authentication_response` with
`expected_rp_id`, `expected_origin` and `require_user_verification=True`. It is the most widely used
Python server library, small, typed, focused on the relying-party side only, and maintained with
regular releases; its dependencies (W1 records the exact set from `uv.lock`) are `cryptography`
(already ours), `cbor2` and `asn1crypto` (both MIT), and in some versions `pyOpenSSL` (Apache-2.0).

**Licence.** AGENTS.md's allowed set (MIT, ISC, Apache-2.0, OFL) is written in the Frontend
section; the backend has no written rule and already ships BSD-3-Clause packages at runtime
(`httpx`, `starlette`, `uvicorn`) and psycopg (LGPL-3.0). BSD-3-Clause is permissive and adds only
the no-endorsement clause. Every Python alternative is BSD too (below), so the choice does not turn
on the licence. Because the list could be read as project-wide, W1 adds this wording to AGENTS.md
after the owner agrees (owner question 1):

> Backend runtime dependencies: MIT, ISC, Apache-2.0, BSD-2-Clause, BSD-3-Clause or PSF; psycopg
> (LGPL-3.0, used unmodified as a library) is accepted. Named by ADR 0019: `webauthn` (py_webauthn,
> BSD-3-Clause), pinned exactly, used unmodified. Any other licence needs an owner exception.

Attestation is not used (§4), so no trust anchors, metadata service or AAGUID list is shipped and
nothing is fetched: py_webauthn makes no network call in this use, and the netguard keeps it so in
tests.

### 3. RP ID and origin

- **`CLASSBEAT_WEBAUTHN_RP_ID`** (optional). Default: the host of `CLASSBEAT_PUBLIC_BASE_URL`.
  **Never derived from a request header**: not `Host`, `Origin`, `X-Forwarded-Host` or anything
  the proxy sets. Both values are read once at start.
- **Expected origin** is exactly the `CLASSBEAT_PUBLIC_BASE_URL` origin (scheme, host, port), one
  value, compared by py_webauthn against `clientDataJSON.origin`. No list, no wildcard, no related
  origins (`/.well-known/webauthn`) until a second domain exists.
- **Start-up check** (web refuses to start with `CLASSBEAT_PASSKEYS` on otherwise): the RP ID is a
  lowercase DNS name with no scheme, port, path or trailing dot; not an IP literal; equal to the
  public host or a dot-suffix of it; at least two labels unless it is `localhost` or ends in
  `.localhost`; and `CLASSBEAT_ENVIRONMENT=prod` needs an `https` base URL (already enforced).
  No public-suffix list is shipped, so a public suffix such as `github.io` passes this check;
  browsers refuse such an RP ID themselves, so the residual is a confusing operator error, not a
  weaker passkey.
- **Recommended value for the beta: `classbeat.pernemark.se`** (the exact host, the default). A
  parent such as `pernemark.se` would let any other page on that domain ask for the same passkeys.
  The RP ID is baked into every passkey: changing the domain later makes all existing passkeys
  useless (users fall back to email or a provider and register again), so the owner names the
  long-term domain before passkeys are switched on in production (owner question 2).

### 4. Registration ceremony (account page, signed in)

`POST /api/auth/passkeys/register/start` and `.../register/finish`. **Both** need
`require_recent_auth` (recent authentication within 10 minutes, plus the step-up when the user's
TOTP is confirmed, ADR 0003 §6), the CSRF token and the Origin check (ADR 0003 §4). Finish checks
them again rather than trusting start: a step-up that ran out between the two fails closed with
`stepup_required`, and the SPA prompts and starts again (the challenge and the step-up both last
5 minutes, so a start late in the window can need this).

Options (start):
- `rp = {id: RP ID, name: "classbeat"}`; `user = {id: users.webauthn_user_handle, name: email,
  displayName: email}`. The **user handle** is 32 random bytes made at the user's first
  registration and kept for the account's life (never the user's uuid or email, so the
  authenticator holds no internal id). It is created with `UPDATE users SET webauthn_user_handle =
  :new WHERE id = :user AND webauthn_user_handle IS NULL` and then read back, so two parallel
  starts end with one handle and both use it.
- `challenge`: 32 random bytes (§6). `timeout`: 300 000 ms (the challenge's TTL).
- `pubKeyCredParams`: ES256 (-7), EdDSA (-8), RS256 (-257).
- `authenticatorSelection = {residentKey: "required", requireResidentKey: true,
  userVerification: "required"}`; no `authenticatorAttachment`, so the computer, a security key or
  a phone (hybrid) may all hold it.
- `attestation: "none"`; `excludeCredentials`: the user's own credential ids (no duplicate on one
  authenticator). Refused at start with 409 `passkey_limit` at 10.

Finish: the challenge row is taken (§6); it must be `register`, belong to this session and user;
then `verify_registration_response` with the RP ID, the origin and UV required (UP and UV flags
set). The credential is always stored for the flow's user (= the session's user), never a user
named by the request. The insert runs in one transaction that first takes `SELECT … FROM users …
FOR UPDATE` and re-checks that the starting session row still exists and is unexpired; a reset
revokes sessions and lists passkeys under the same lock (§9), so a finish in flight during a reset
either lands before the list (and is shown there) or finds its session gone and stores nothing. Whatever attestation statement arrives is not trusted and drives no decision. The credential
id must be new (`UNIQUE`; a clash answers 409 `passkey_exists`, the same for one's own and another
user's). The row is stored with a label (user text, 1 to 64 characters after trimming, default
"Passkey" and the date; rendered as text). Then an `auth_events` row `passkey_added` and a mail to
the account's address ("a passkey was added", with the label and a link to the account page), as
for a new link (ADR 0003 §5).

### 5. Sign-in ceremony (username-less)

`POST /api/auth/passkeys/signin/start` and `.../signin/finish`, anonymous, CSRF double-submit and
Origin check as for the password form.

- **Start** takes no email. `allowCredentials` is empty (discoverable credentials), so the answer
  is the same for everyone and reveals no account. `userVerification: "required"`.
- **The SPA** offers a "Sign in with a passkey" button and, where
  `PublicKeyCredential.isConditionalMediationAvailable()` is true, passkey autofill on the email
  field (`mediation: "conditional"`, restarted when its challenge expires). The browser offers
  "use a phone" (hybrid, the QR code) on its own when no local passkey fits.
- **Finish**, in order:
  1. Take the challenge row by the flow cookie (§6); it must be `sign_in`.
  2. Look up the credential by its id (`system_session`). Unknown → 400 `passkey_unknown`. This
     names nothing about any account (credential ids are random and the caller holds the
     authenticator), and lets the SPA call `PublicKeyCredential.signalUnknownCredential` where the
     browser has it, so a passkey of a deleted account or a removed passkey stops being offered.
  3. `userHandle` must be present and equal the credential's user's handle.
  4. `verify_authentication_response` with the stored public key and counter, the RP ID, the
     origin and UV required. The backup-eligible flag must equal the stored one (it never changes).
  5. The counter rule (§7) in one conditional `UPDATE`, which also sets `last_used_at` and the
     backup state.
  6. A disabled user gets the generic failure, like a wrong password.
  7. `start_session` as for a password sign-in: a new rotated session with `reauth_at = now()`,
     the `ADMIN_EMAILS` grant check, the known-device cookie (except as in owner question 4), and an
     `auth_events` row `sign_in` with detail `{method: "passkey"}`.
- Every failure except `passkey_unknown` and `feature_off` answers 400 `passkey_failed` with no
  detail; the reason goes to `auth_events` (`sign_in_failed`, `{method: "passkey", reason}`) as a
  category only (§10).
- **An admin** still reaches the admin role only after TOTP in that session (§8).

**Cross-device (hybrid).** Nothing on the server is specific to it. The browser shows the QR code,
the phone proves proximity over Bluetooth and signs; the assertion arrives at our origin from the
computer's browser with `transports` possibly including `hybrid` and
`authenticatorAttachment: "cross-platform"` (client-reported, unsigned). The server stores
transports for display only and uses the attachment only to make the session stricter, never
looser (owner question 4). The fake-QR attack of a custom flow does not apply: the QR code only
moves from the page the user is on to their own phone, the phone refuses without proximity, and the
assertion is bound to our origin, so an attacker's remote page or QR code gets nothing usable.

### 6. Challenges: single use, five minutes, bound to the browser

A new system table `webauthn_challenges`, modelled on `oauth_flows` (ADR 0003 §5):

| Column | Holds |
|---|---|
| `flow_hash` | unique; SHA-256 of the flow id in the cookie below |
| `purpose` | `register`, `sign_in`, `reauth` |
| `challenge` | 32 random bytes as given (a nonce, not a secret; lives at most 5 minutes, deleted on use) |
| `user_id` | `register`, `reauth`: the user (FK `users`, cascade); null for `sign_in` |
| `session_id` | `register`, `reauth`: the session that started it (FK `sessions`, cascade) |
| `expires_at` | 5 minutes after start |

- Start creates the row and sets a cookie **per purpose**: `__Host-classbeat_passkey_signin`,
  `__Host-classbeat_passkey_register`, `__Host-classbeat_passkey_reauth` (32 random bytes,
  `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/`, `Max-Age=300`). A new start of the same purpose
  in the same browser deletes the row of the previous cookie value first, so a browser holds at most
  one flow per purpose and a re-auth never cancels a registration. Accepted: two tabs running the
  same purpose at once share the cookie, the newer start wins and the older tab's finish fails
  closed (`passkey_failed`; the SPA restarts autofill on that answer).
- Finish takes the row with `DELETE FROM webauthn_challenges WHERE flow_hash = … AND expires_at >
  now() RETURNING …` **before** verifying, so a replay or a second parallel finish finds nothing.
  Missing, expired, wrong purpose, or (for `register`/`reauth`) another session or user: the
  generic failure. The cookie is cleared either way.
- The challenge in `clientDataJSON` must equal the row's (py_webauthn checks it). An assertion made
  for one browser's challenge cannot be finished in another browser, because the row is found only
  through that browser's cookie. That is what stops login CSRF and a transplanted assertion (T114).
- A session rotation between start and finish (another tab re-authenticates) fails the flow; the
  user starts again.
- The hourly purge (ADR 0003 §1) deletes expired rows. Not stateless (a signed challenge): a signed
  value cannot be made single-use, the same reason ADR 0003 rejected it for OAuth state.

### 7. The credential table, counters and the last-method rule

**`webauthn_credentials`** is a **system table in `classbeat.auth`**, like `identities`, not an
owned table under RLS. The issue's scope text said "under RLS like other owned rows"; this ADR
changes that, for the reasons of ADR 0003 §1: sign-in must find the credential by its id before any
user is known, so RLS would need a `SECURITY DEFINER` lookup or a bypass for the most exposed query;
every other sign-in method lives in system tables; and nothing in the row is a secret (a public
key cannot sign). Isolation rests on ADR 0003's two tested rules: every signed-in query filters on
the session's `user_id`, and every route that takes a passkey id answers 404 for another user's id,
the same as for an unknown one. Guard 8 covers the table.

| Column | Holds |
|---|---|
| `id` | uuid, server default (the id the API uses) |
| `user_id` | FK `users`, `ON DELETE CASCADE`; indexed |
| `credential_id` | `bytea`, unique, at most 1023 bytes (WebAuthn's bound) |
| `public_key` | `bytea`, the COSE key from registration |
| `sign_count` | `bigint`, not null, default 0 |
| `transports` | `text[]`, only `usb`, `nfc`, `ble`, `internal`, `hybrid`, `smart-card` (others dropped) |
| `backup_eligible`, `backup_state` | booleans from the flags (shown as "synced" or "this device only") |
| `label` | text, 1 to 64 characters |
| `created_at`, `last_used_at` | timestamps |

Plus `users.webauthn_user_handle` (`bytea`, unique, nullable until the first registration).

**Signature counter.** The update is one statement:
`UPDATE … SET sign_count = :new, last_used_at = now(), backup_state = :bs WHERE id = :id AND
(sign_count < :new OR (sign_count = 0 AND :new = 0)) RETURNING id`.
- Both zero (most synced passkeys): accepted; replay protection comes from the single-use challenge.
- Otherwise the new value must be greater. An equal or lower one updates nothing. A replayed
  assertion never gets here (it fails at the single-use challenge first), so only a holder of the
  private key can cause this: a cloned authenticator, or two genuine parallel sign-ins arriving out
  of order. The conditional update also makes the second of those lose cleanly; the sign-in is refused, `auth_events` gets
  `passkey_counter_regressed` with the passkey's id, and the user is mailed (at most once a day per
  passkey). The passkey stays, so a false alarm never locks anyone out; the mail tells the user to
  remove it if they did not sign in.
- A counter going from non-zero back to 0 is a regression too.

**List, rename, remove** (account page):
- `GET /api/auth/passkeys`: signed in (`require_user`), like the identities and sessions lists on
  the same page; returns id, label, dates, transports, synced. Nothing in it is secret, and the
  issue's "list needs recent authentication" would only hide from the user what they already own.
- `PATCH /api/auth/passkeys/{id}` (label) and `DELETE /api/auth/passkeys/{id}`: `require_recent_auth`
  (and so the step-up for a TOTP user), CSRF, 404 for an unknown or foreign id. Events
  `passkey_renamed` and `passkey_removed`; removal mails the user.

**Removing the last sign-in method is refused.** ADR 0003's rule stays as it is and passkeys do
**not** count toward it: a user must keep a password or at least one identity, so unlinking the only
identity of a password-less user is refused (409 `last_sign_in_method`) even when they have
passkeys. Removing a passkey is therefore always allowed and can never leave an account without a
method. There is no password-removal route (none exists today) and this ADR adds none.

**Every lookup is bound to the expected user.** Sign-in finds the credential by its id alone (no
user is known yet) and then requires the user handle to match. Re-auth and registration never do:
re-auth finds the credential with `credential_id = :id AND user_id = :flow_user` (the flow's user,
which is the session's user, §6), so another user's credential, however valid its signature, gets
the generic failure, and its counter and `last_used_at` are not touched. `allowCredentials` in the
options is only a hint to the browser and is never relied on. Registration stores only for the
flow's user (§4). Any later use of a passkey as a step-up (owner question 3) must keep the same
binding.

### 8. Recent authentication, step-up and admins

- **A passkey re-authenticates.** `POST /api/auth/passkeys/reauth/start|finish` (signed in):
  `allowCredentials` is the user's own credentials (a hint only), UV required, purpose `reauth`
  bound to the session; the finish looks the credential up **with the session's user** (§7), so a
  stolen session cookie plus the attacker's own passkey on their own account gets nothing (T120);
  success sets `reauth_at`, rotates the session (ADR 0003 §3) and writes `reauth`
  `{method: "passkey"}`. The re-auth prompt offers it beside the password and the providers.
- **A passkey is not a step-up.** It never sets `stepup_at` or `mfa_at`. A user with confirmed TOTP
  still enters a TOTP or recovery code for the actions of ADR 0003 §6 and ADR 0011, adding a
  passkey included.
- **Admin MFA: recommendation, a passkey does not replace TOTP (for now).** An admin may sign in
  with a passkey, and the admin role still takes effect only after TOTP in that session (ADR 0003
  §2, §7). Reasons: a synced passkey lives in a cloud account (Apple, Google), so its security is
  that account's, which classbeat cannot see; without attestation the server cannot tell a
  device-bound key from a synced one except by the self-reported backup flag; TOTP stays an
  independent second device; and the admin gate's code is unchanged, which keeps this work out of
  the admin path. The cost is that TOTP is phishable and a passkey is not. A later ADR amendment
  could accept a device-bound passkey (`backup_eligible = false`) as the admin factor; that is
  owner question 3 and is not in the issue split.

### 9. Account recovery is not weakened

- Email reset (ADR 0003 §5) stays the recovery for every account: it sets a password, revokes all
  sessions and deletes known devices, as today.
- **The reset page lists the user's passkeys** beside the linked providers, each removable in the
  same step without recent authentication (the reset is the proof), so a passkey someone else
  added does not survive the reset. It does so **whatever `CLASSBEAT_PASSKEYS` says**. The session
  revocation and the listing take the user row lock (§4). API contract (W5): the reset-token
  routes return `passkeys: [{id, label, created_at, last_used_at}]` and accept `remove_passkeys:
  [id]`, ids checked against the token's user.
- `python -m classbeat admin mfa-reset <email>` also deletes the user's passkeys, whatever the flag
  says (they are the account's authenticators, untrusted after a compromise); its mail and event
  say so.
- A new passkey needs recent authentication and the step-up and is mailed (§4), so a stolen
  session alone cannot plant one, and a stolen password plus session of a TOTP user cannot either.
- Password change, email change, "sign out everywhere" and disabling a user leave passkeys as they
  are (they revoke sessions, not methods), as for identities. An email change does not update the
  name stored on the authenticator; the SPA calls `PublicKeyCredential.signalCurrentUserDetails`
  where the browser has it.

### 10. Rate limits, audit and logging

Rate limits use the existing `rate_limits` buckets (ADR 0003 §9):

| Action | Per account | Per IP | Counts |
|---|---|---|---|
| Sign-in start | — | 300 / 15 min | every request; loose, because autofill starts a ceremony on every sign-in page load and a gym or office shares one address |
| Sign-in failures | 20 / hour (when the credential maps to a user), except from a known device | 30 / 15 min | failures, released on success |
| Register start and finish | 10 / hour per user | 30 / hour | every request |
| Passkey re-auth failures | the password re-auth buckets | the same | failures |
| Rename, remove | 30 / hour per user | — | every request |

Start rows are cheap and short-lived, so instead of a tight start limit there is a cap: at most
20 000 unexpired `sign_in` rows; above it start answers 503 `busy` and the page falls back to the
button (password and providers are unaffected). The known-device exemption matches ADR 0003 §9.

`auth_events` kinds: `passkey_added`, `passkey_renamed`, `passkey_removed`,
`passkey_counter_regressed`, and `sign_in`, `sign_in_failed`, `reauth`, `reauth_failed` with
`{method: "passkey"}`. Detail holds the passkey's row id and a reason category (`challenge`,
`origin`, `rp_id`, `user_handle`, `uv`, `signature`, `counter`, `backup_flag`, `format`), never a
label, credential id, key, challenge or client data. The three passkey flow cookies join ADR 0003
§10's never-logged cookies.

**No credential material in logs.** The routes live under `/api/auth/`, whose request bodies are
never logged (ADR 0003 §10); responses are `no-store` and not logged either. py_webauthn's
exceptions can quote expected and received values (origins, RP ids, challenges), so they are caught
at the route, mapped to a category, and never logged or returned as text. The log-redaction test
(T7) gains planted values for each: credential id, public key, `clientDataJSON`,
`attestationObject`, `authenticatorData`, signature, user handle, challenge and the flow cookie.

### 11. Frontend, CSP and testing

- **No new frontend dependency.** A small `src/auth/webauthn.ts` uses the browser's
  `PublicKeyCredential.parseCreationOptionsFromJSON`, `parseRequestOptionsFromJSON` and
  `toJSON()` where present, and its own base64url conversion otherwise. The UI is hidden when
  `window.PublicKeyCredential` is missing. It works under the CSP as is (`'self'`, no inline code).
  If a `Permissions-Policy` header is ever added, it must allow `publickey-credentials-get=(self)`
  and `publickey-credentials-create=(self)`.
- **Pages**: a "Passkeys" card in the account page's sign-in methods (add, list, rename, remove;
  the guard prompts of `useGuard` for re-auth and step-up), the sign-in button and autofill, the
  re-auth option, and the reset page's list. Night Studio components, 44 px targets, axe clean.
- **Backend tests** use a software authenticator in-process (`tests/webauthnfake.py`: P-256 and
  Ed25519 keys made per run, building real `clientDataJSON`, `authenticatorData` and signatures),
  no network. They prove refusals for: wrong origin, wrong RP ID hash, replayed challenge, expired
  challenge, another browser's flow cookie, another session's `register`/`reauth` flow, a missing
  or foreign user handle, UV not set, a bad signature, counter rollback and equal non-zero counters,
  a changed backup-eligible flag, a duplicate credential id, an 11th passkey, **another user's valid
  passkey refused for re-auth** (and its counter untouched), a register finish after the session was
  revoked, two parallel first starts ending with one user handle, the last-method rule (an identity
  unlink still refused with passkeys present; a passkey removal always allowed), and reset and
  `mfa-reset` removing passkeys with the flag off; plus 404 for foreign passkey ids and the
  redaction test.
- **E2E** uses Playwright's Chromium with the CDP WebAuthn domain (`WebAuthn.enable`,
  `WebAuthn.addVirtualAuthenticator` with `protocol: "ctap2"`, `hasResidentKey`,
  `hasUserVerification`, `isUserVerified`, `automaticPresenceSimulation`): `transport: "internal"`
  for a platform passkey and `"usb"` for a cross-platform one, which is what a hybrid assertion
  looks like to the server (CDP cannot simulate the phone and the QR code; the hybrid leg is the
  browser's and is documented, not tested). Specs: register, sign out, sign in username-less,
  re-auth, rename, remove, last method refused, a removed passkey refused, two users, and the
  reset page's removal. No real authenticator, account or network.
- **The e2e origin.** `127.0.0.1` cannot be an RP ID, and the rest of the stack must stay on it
  (the fake Spotify checks `redirect_uri` against `http://127.0.0.1:${WEB_PORT}/…`, and real
  Spotify accepts http redirect URIs only on loopback IP literals). So `compose.e2e.yaml` adds a
  second web service, `web-passkeys`, from the same image on the same database, with
  `CLASSBEAT_PUBLIC_BASE_URL=http://localhost:<port>` (`localhost` is a secure context and a valid
  RP ID), `CLASSBEAT_PASSKEYS` on and song picking off; only the passkey specs use it
  (`E2E_PASSKEY_BASE_URL`), and the e2e stack checks treat it like `web` (non-root, the same secret
  mounts and no model key). The other specs are untouched. Local development may use `localhost` with
  song picking off; with an IP base URL and passkeys on, web refuses to start (§3).

### 12. Amends ADR 0003

Once this ADR is Accepted it **supersedes the parts of ADR 0003 named below** (and only those);
everything else in ADR 0003 stands. W8 adds a one-line pointer to ADR 0019 in each of those ADR
0003 sections, so neither document contradicts the other.

| ADR 0003 | What it says | What ADR 0019 changes |
|---|---|---|
| §5, unlinking | The last method cannot be removed: the only identity of a user without a password, or the password of a user without an identity | Same rule; passkeys **never** count toward it, and removing a passkey is always allowed (§7). No password-removal route exists or is added |
| §5, reset | The reset page lists the linked providers, removable in the same step | It also lists the user's passkeys, removable in the same step, whatever `CLASSBEAT_PASSKEYS` says (§9) |
| §6 | Re-authentication is one of: the password, an OAuth `reauth` flow, an admin's TOTP code | Adds a passkey bound to the session's user (§8). A passkey is not a step-up |
| §7 | `admin mfa-reset` removes TOTP, recovery codes and known devices | It also removes the user's passkeys (§9) |
| §3 | One absolute limit (30 days) for every session; a known-device cookie on every successful sign-in | Only if owner question 4 is answered yes (W9): a sign-in with a `cross-platform` assertion gets a 24-hour absolute expiry and no known-device cookie |
| §10 | The never-logged cookie list (session, device, CSRF, OAuth) | Adds the three `__Host-classbeat_passkey_*` flow cookies (§10) |
| §1 | The account tables | Adds the system tables `webauthn_credentials`, `webauthn_challenges` and the column `users.webauthn_user_handle` (§6, §7) |

## Alternatives

- **A custom QR login** (the computer shows a code, a signed-in phone approves it). Rejected in
  #311: security-critical code of our own, open to the fake-QR attack, and no phishing resistance.
- **python-fido2 (Yubico, BSD-2-Clause).** Capable, but it is client and server in one, larger, and
  its server API changed between majors. py_webauthn does the relying-party side only. Same licence
  question either way.
- **Our own verification** (CBOR, COSE keys, flags, signatures). Rejected: parsing untrusted binary
  structures for sign-in is exactly where a maintained, widely reviewed library pays for itself.
- **Owned table under RLS** (the issue's wording). Rejected (§7): sign-in needs the row before a
  user is known, and the rest of the account data deliberately lives in system tables.
- **Email first, then `allowCredentials`.** Rejected: it tells anyone which addresses have
  passkeys (T33) and adds a step; discoverable credentials are what make one-tap and the QR flow
  work.
- **Attestation `direct` with an allow-list of authenticators.** Rejected: needs the FIDO metadata
  service (a new outbound host and its upkeep), most synced passkeys give none, and the product
  does not restrict which authenticator a user may use.
- **Refusing every 0 counter, or disabling a passkey on a regression.** The first locks out most
  synced passkeys; the second would let two genuine sign-ins arriving out of order disable the
  user's own passkey. Refusing the one sign-in and mailing the user is the narrower answer (§7).
- **Passkeys counting toward the last-method rule (passkey-only accounts).** Deferred to a later
  ADR with a passkey-first sign-up: it would need a password-removal route and makes the flag a
  way to strand users (owner question 5).
- **A passkey as a step-up or admin factor now.** Deferred (§8, owner question 3).
- **An admin flag (ADR 0011 §7) instead of an environment setting.** Possible later; the RP ID is
  per environment anyway, and a setting keeps the first version simple.

## Consequences

- Users can sign in on a shared computer with their phone, with no code of ours in the QR leg, and
  every passkey sign-in is phishing-resistant.
- One new dependency (BSD-3-Clause, owner question 1), two new system tables and one column, one
  migration (the next free number at merge), and new guard 8, guard 12 and export entries:
  `coverage.USER_REFERENCES` gains `webauthn_credentials.user_id` and `webauthn_challenges.user_id`
  (cascade); the export's `sign_in_methods` gains `passkeys` (label, credential id as base64url,
  transports, synced, created and last used; `public_key` and `sign_count` are excluded as key
  material and an internal replay guard, `webauthn_user_handle` as an internal handle);
  `webauthn_challenges` is not exported (lives five minutes). Account deletion cascades.
- The RP ID ties passkeys to one domain: a domain change costs every user their passkeys (§3).
- Synced passkeys at counter 0 have no clone detection; that is accepted (T116) and inherent to
  them.
- The e2e stack gains one web service for the passkey specs; the existing specs keep their
  base URL.
- Review cost: the ceremonies, the challenge table, the counter, the last-method rule and recovery
  are auth and sessions: Opus Developer and fresh Opus Reviewer. UI, e2e and docs are Sonnet.

## Threat model

New rows, added by W8 when the code they describe is on `main` (next free ids on `main` are T113
and up; T112 is the SMTP pin):

| # | Threat | Mitigation |
|---|---|---|
| T113 | Phishing a passkey, or RP ID / origin confusion through a forged `Host` or proxy header | The browser binds assertions to origin and RP ID; the server's RP ID and origin come only from settings read at start, never from request headers; exact origin match; start-up check refuses an IP or unrelated RP ID (§3) |
| T114 | Replaying an assertion, or login CSRF / a transplanted assertion signing a victim's browser into another account | 32-byte challenge, single use (`DELETE … RETURNING` before verification), 5 minutes, found only through the starting browser's `SameSite=Strict` `__Host-` cookie and, for register and re-auth, bound to the session; CSRF token and Origin check on every finish (§6) |
| T115 | A rogue passkey planted with a stolen session or password for lasting access | Registration needs recent authentication and, with TOTP, the step-up; every addition is mailed; the reset page and `admin mfa-reset` remove passkeys; at most 10 (§4, §9) |
| T116 | A cloned authenticator | Counter must increase unless it is 0 on both sides; a regression refuses the sign-in, is recorded and mailed; the backup-eligible flag may not change. Residual: synced passkeys at 0 get no clone detection (accepted) (§7) |
| T117 | Credential material or library error text in logs, errors, events or exports | Request and response bodies under `/api/auth/` not logged; library exceptions mapped to categories; events hold row ids and categories only; the export leaves out the public key and counter; redaction test with planted values (§10) |
| T118 | A session left on a shared or gym computer after a cross-device sign-in; the fake-QR attack | No custom QR flow (the browser's hybrid transport checks proximity); shorter session and no known-device cookie on a cross-platform sign-in (owner question 4); "sign out" shown after it (§5) |
| T119 | Lock-out by removing the last method; account enumeration through passkey sign-in | ADR 0003's last-method rule unchanged (a password or an identity always remains; passkeys never count), under the user row lock; username-less start with empty `allowCredentials`; one generic failure. Residual: `passkey_unknown` versus `passkey_failed` tells a caller whether a credential id is registered; accepted, because ids are random and the caller must hold the authenticator (§5, §7) |
| T120 | A stolen session cookie plus the attacker's own passkey (on the attacker's account) used to pass re-authentication, or to register under the victim | Re-auth looks the credential up with the session's user (`credential_id` and `user_id = flow user`), so a foreign credential fails without touching its row; registration stores only for the flow's user and still needs recent authentication and the step-up; `allowCredentials` is never relied on; test "another user's valid passkey refused for re-auth" (§7, §8) |

## Owner questions (defaults the issues build to)

1. **Licence.** Accept `webauthn` (py_webauthn, BSD-3-Clause) and the backend licence wording in §2?
   Default: **yes** (BSD-3-Clause is already in the backend through httpx, starlette and uvicorn).
2. **RP ID and domain.** Is `classbeat.pernemark.se` the long-term home, or will classbeat move to
   its own domain? Default: **RP ID `classbeat.pernemark.se`, passkeys stay off in production until
   you confirm the domain**, because a later move invalidates every passkey.
3. **Admin MFA.** May a passkey satisfy the admin's TOTP? Default: **no**; TOTP stays the admin
   factor and a passkey is not a step-up (§8). A device-bound-only variant can follow as an
   amendment.
4. **Shared computers.** After a sign-in whose assertion reports `cross-platform` (a phone over QR,
   or a security key), make the session last **24 hours** (absolute) with no known-device cookie,
   and show "Signed in with your phone; sign out when you are done"? Default: **yes** (the signal is
   client-reported, so it is used only to make things stricter).
5. **Passkey-only accounts** (a passkey-first sign-up, or a provider-only user unlinking the last
   provider while keeping a passkey)? Default: **not now**; passkeys stay additional and a later ADR
   adds passkey-first sign-up if you want it.
6. **Switching on.** `CLASSBEAT_PASSKEYS` defaults to off; you switch it on per environment.
   Default: **on in the e2e stack only** until you say so for the beta.

## The issues

Review class: **Opus** = auth, sessions, challenges, counters, recovery, migrations on account
tables; **Sonnet** = UI over reviewed routes, e2e, docs. All depend on this ADR being accepted and
on M6 being done (#311). #311 stays open as the tracker.

| # | Title | Scope | Done when | Review | Depends on |
|---|---|---|---|---|---|
| W1 | `feat(auth): passkey tables, settings and dependency` | migration: `webauthn_credentials`, `webauthn_challenges`, `users.webauthn_user_handle`; `classbeat.auth.tables`, guard 8, `coverage`, export `INCLUDE`/`EXCLUDE`/`NOT_EXPORTED`, purge; `CLASSBEAT_PASSKEYS`, `CLASSBEAT_WEBAUTHN_RP_ID` and the start-up check; `webauthn` pinned; AGENTS.md licence wording (after owner question 1) | guard 8, guard 12 and the export schema test pass with the new tables; the delete test plants a passkey and a challenge; start-up refuses an IP, a port and an unrelated host; the options' RP ID and the expected origin do not change with a forged `Host` or `X-Forwarded-Host` (test); pip-audit clean | Opus | this ADR |
| W2 | `feat(auth): register, list, rename and remove passkeys` | the challenge store and per-purpose cookies (§6), the user-handle update, register start/finish under the user lock with the session re-check, list, rename, remove, mails and events, the register, rename and remove rate limits of §10 | the refusal list of §11 for registration; 404 for foreign ids; an identity unlink still refused with passkeys present; recent auth and step-up checked at start and finish | Opus | W1 |
| W3 | `feat(auth): username-less passkey sign-in` | sign-in start/finish, counter update, `passkey_unknown`, session creation through `start_session`, rate limits, redaction | the refusal list of §11 for sign-in; counter cases; a disabled user's generic failure; admin role still needs TOTP; redaction test with every planted value | Opus | W2 |
| W4 | `feat(auth): re-authenticate with a passkey` | reauth start/finish bound to the session, the credential looked up with the session's user (§7), rotation, events | sets `reauth_at`, never `stepup_at`/`mfa_at`; another session's flow refused; **another user's valid passkey refused, its counter and `last_used_at` unchanged** | Opus | W3 |
| W5 | `feat(auth): passkeys in reset and mfa-reset` | the reset routes' `passkeys` list and `remove_passkeys` (§9), under the user lock; `admin mfa-reset` deletes them; both whatever the flag says; mail text | an attacker's passkey removed on reset, with the flag on and off; a foreign id ignored; mfa-reset leaves none; events written | Opus | W2 |
| W6 | `feat(web): passkey UI on the account, sign-in and re-auth pages` | `webauthn.ts`, the Passkeys card, sign-in button and autofill, re-auth option, reset list, `signalUnknownCredential`/`signalCurrentUserDetails` where present; owner question 4's notice | component tests with a mocked `navigator.credentials`; hidden without `PublicKeyCredential`; axe clean; works under the CSP | Sonnet | W2, W3, W4, W5, W9 |
| W7 | `test(e2e): passkeys with a virtual authenticator` | the `web-passkeys` service on `localhost` (§11) and its stack checks, the specs of §11 | the specs pass in CI against `web-passkeys`; the other specs and their base URL unchanged | Sonnet | W6 |
| W8 | `docs: passkeys in AGENTS.md, threat model and runbook` | AGENTS.md settings and routes, T113 to T120, the ADR 0003 pointers of "Amends ADR 0003", an owner runbook (RP ID choice, switching on and off, what users see, hybrid documented) | rows describe what is on `main`; runbook names no secret | Sonnet | W7 |
| W9 | `feat(auth): owner question 4, shorter cross-device session` (only if the answer is yes; may fold into W3) | per-session absolute expiry on a cross-platform assertion, no known-device cookie | the session expires at 24 hours; no device cookie set | Opus | W3 |
