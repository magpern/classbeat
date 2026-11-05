# ADR 0019: Passkeys with cross-device sign-in (#311)

Status: Proposed (2026-11-03, issue #311; backlog after M6. A fresh Opus Reviewer reviews it in its
PR. Acceptance covers the design and the issue split; the owner answers the questions at the end.)

**Revision 2026-11-05** (the owner said to start #311). Folds in the non-blocking points A to E of
#483's last Opus review (posted on #311) and the facts that changed on `main` since:
- **A** §12 also names ADR 0003 §6 (the guarded-action list) and §9 (the rate-limit table).
- **B** A passkey registered between the reset page's `info` and `complete` no longer survives the
  reset: `complete` takes a **keep list** and deletes every other passkey under the user row lock
  (§9).
- **C** The global sign-in-start row cap is replaced by atomic limiter buckets per address, per
  IPv6 /48 and globally, a clear 503 and a named residual (§10, T119).
- **D** One lock order: every challenge is taken in its own committed transaction before
  verification, and every transaction that writes credentials or revokes a user's sessions locks
  the `users` row first (§6).
- **E** List and remove work with the flag off (§1); W6 depends on W9 only if owner question 4 is
  yes; the `web-passkeys` service and the `database_maint_password` check (§11, W7).
- **Facts:** py_webauthn is at 3.0.1 with different dependencies (§2); the licence rule lives in
  `docs/developer-commands.md`, not AGENTS.md (§2); the owner's swag sample sends a
  `Permissions-Policy` header (Context, §11); the general per-user API limit of #626 exists (§10);
  the session anomaly signal of #632 is in development and shapes how the routes hook in (§13);
  T121 to T123 are taken on `main`, T113 to T120 stay claimed here.
- **Review of #638 (FAIL on `af710df`), fixed:** the sign-in start buckets are reserved in order and
  stop at the first refusal, since `RateLimiter.begin` takes a slot in every bucket (§10); the
  sign-in and re-auth finishes lock `users` first and do the counter update, the grant check and
  `start_session`/`rotate_session` in that one transaction (§5, §6, §8); the library gets counter 0
  and the same three algorithms as the options (§2, §7).

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
- **The CSP** (`classbeat.headers`) is `'self'` only, and web sends no `Permissions-Policy`. The
  owner's swag sample (`docs/runbooks/swag-classbeat.subdomain.conf`) sends
  `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()`. It names neither
  `publickey-credentials-get` nor `publickey-credentials-create`, whose default allowlist is `self`,
  and `usb=()` is WebUSB, not the browser's own security-key transport. So `navigator.credentials`
  works on our origin as it is, in production too (§11 keeps it so).
- **#632** (session anomaly signal, in development on `632-session-anomaly`, migration 0033) clears
  `reauth_at`, `stepup_at` and `mfa_at` when a session is used from a new coarse network or browser
  family, and sets the session's client mark in `start_session` and on every rotation (§13).

## Decision

### 1. What a passkey is here

A passkey is **another sign-in method**, beside the password and the linked providers (ADR 0003
§5). It is not a second factor: it does not set `mfa_at` or `stepup_at` (§8). A user may have up to
**10**. A passkey is always **in addition to** a password or a linked provider: there is no
passkey-first sign-up in this ADR and no route removes a password (none exists today, ADR 0003 §5),
so the last-method rule (§7) keeps ADR 0003's meaning and a passkey-only account cannot arise.

The whole feature sits behind `CLASSBEAT_PASSKEYS` (bool, default **false**; web only). Off, the
register, sign-in, re-auth and rename routes answer 404 `feature_off` and the sign-in and re-auth
pages show no passkey UI. **List and remove ignore the flag** (`GET /api/auth/passkeys` and
`DELETE /api/auth/passkeys/{id}`, with the same guards as when it is on, §7): both only show or take
away what the user already has, so the account page shows the Passkeys card whenever the user has a
passkey, with "Passkeys are switched off; you can still remove these" when the flag is off.
**Recovery ignores the flag too:** the reset page lists and removes existing passkeys and
`admin mfa-reset` deletes them whatever the flag says (§9), so switching the flag off never hides a
planted passkey that would come back when it is switched on again. Because every user keeps a
password or a provider, switching it off strands nobody.

### 2. Library: py_webauthn

**`webauthn` (py_webauthn, Duo Labs / Cisco), BSD-3-Clause**, pinned exactly (`==`, hash-locked in
`uv.lock`, covered by pip-audit and Dependabot), used for: generating options, parsing the
responses, and `verify_registration_response` / `verify_authentication_response` with
`expected_rp_id`, `expected_origin` and `require_user_verification=True`. It is the most widely used
Python server library, small, typed, focused on the relying-party side only, and maintained with
regular releases.

**Version (checked 2026-11-05 on PyPI).** The current release is **3.0.1** (2026-09-25; 3.0.0 was
2026-06-29), Python `>=3.10`. It requires `cryptography>=49.0.0` (ours is 50.0.2 in `uv.lock`),
`cbor2>=6.1.2` (MIT), `pyasn1>=0.6.2` (BSD-2-Clause), `pyasn1-modules>=0.4.2` (BSD-2-Clause) and
`pyOpenSSL>=26.3.0` (Apache-2.0); the 2.x line used `asn1crypto` instead. W1 pins `webauthn==3.0.1`
(or a later release at least two weeks old at the time), records the resolved set from `uv.lock`
in its PR. The 3.0.1 wheel was checked in #638's review: `generate_registration_options`,
`generate_authentication_options`, `verify_registration_response` and
`verify_authentication_response` keep the keyword names this ADR uses (`expected_rp_id`,
`expected_origin`, `require_user_verification`, `credential_current_sign_count`), the package has no
network code, and `pem_root_certs_bytes_by_fmt` defaults to none. **Algorithms:** verification is
given the same `supported_pub_key_algs` as the options (ES256, EdDSA, RS256; §4), never the
library's longer default, so a credential with another algorithm is refused at registration and at
sign-in.
**Counter:** both verify calls pass `credential_current_sign_count=0`; the counter rule is ours
alone (§7). py_webauthn 3.0.1 refuses a rollback itself, with the same `InvalidAuthenticationResponse`
it raises for every other failure, so passing the stored count would leave the `counter` category,
the `passkey_counter_regressed` event and the mail reachable only by matching message text. With 0,
the library's check can never fire (`new <= 0` with a stored 0 only when both are 0, which it
allows), and our conditional `UPDATE` decides.

**Licence.** The written allowed set (MIT, ISC, Apache-2.0, OFL) is in
`docs/developer-commands.md` ("Pin exact versions of every package added; licences must be …"),
in the frontend part; the backend already ships BSD-3-Clause packages at runtime (`httpx`,
`starlette`, `uvicorn`) and psycopg (LGPL-3.0). BSD-3-Clause is permissive and adds only the
no-endorsement clause. Every Python alternative is BSD too (below), so the choice does not turn on
the licence. Because the list could be read as project-wide, W1 adds this wording next to that line
in `docs/developer-commands.md` after the owner agrees (owner question 1):

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
5 minutes, so a start late in the window can need this). With #632, a move to a new network or
browser between start and finish clears `reauth_at` and `stepup_at`, so the finish fails closed
the same way (§13).

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

Finish, in three steps (the lock order of §6):
1. The challenge row is taken in its **own committed transaction** (§6); it must be `register`,
   belong to this session and user.
2. `verify_registration_response` with the RP ID, the origin and UV required (UP and UV flags set),
   outside any transaction and holding no lock.
3. The insert runs in one transaction whose **first** statement is `SELECT … FROM users WHERE id =
   :flow_user FOR UPDATE`; it then re-checks that the starting session row still exists, belongs to
   the user and is unexpired, and that the user has fewer than 10 passkeys. A reset revokes the
   sessions and deletes passkeys under the same lock (§9), so a finish in flight during a reset
   either commits before the reset takes the lock (and the reset's keep list, which cannot name it,
   deletes it) or finds its session gone and stores nothing.

The credential is always stored for the flow's user (= the session's user), never a user named by
the request. Whatever attestation statement arrives is not trusted and drives no decision. The credential
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
  field (`mediation: "conditional"`, restarted when its challenge expires, but only while the page
  is visible, so a forgotten tab does not start a ceremony every 5 minutes). The browser offers
  "use a phone" (hybrid, the QR code) on its own when no local passkey fits.
- **Finish**, in order:
  1. Take the challenge row by the flow cookie (§6); it must be `sign_in`.
  2. Look up the credential by its id (`system_session`). Unknown → 400 `passkey_unknown`. This
     names nothing about any account (credential ids are random and the caller holds the
     authenticator), and lets the SPA call `PublicKeyCredential.signalUnknownCredential` where the
     browser has it, so a passkey of a deleted account or a removed passkey stops being offered.
  3. `userHandle` must be present and equal the credential's user's handle.
  4. `verify_authentication_response` with the stored public key, `credential_current_sign_count=0`
     (§2), the RP ID, the origin, UV required and the three algorithms, holding no lock and outside
     any transaction. The backup-eligible flag must equal the stored one (it never changes).
  5. **One write transaction** (the lock order of §6), in this order:
     1. `SELECT … FROM users WHERE id = :cred_user FOR UPDATE` (the credential's user from step 2).
     2. The counter rule (§7): the conditional `UPDATE … WHERE id = :id AND user_id = :cred_user AND
        <counter rule> RETURNING id`, which also sets `last_used_at` and the backup state. No row
        means the credential is gone (removed, or deleted by a reset that committed first: the
        generic failure, nothing written) or the counter regressed (the credential still exists:
        the `passkey_counter_regressed` event is written and the transaction commits with no
        session).
     3. A disabled user gets the generic failure, like a wrong password.
     4. The `ADMIN_EMAILS` grant check and `start_session` as for a password sign-in: a new rotated
        session with `reauth_at = now()`, and an `auth_events` row `sign_in` with detail
        `{method: "passkey"}`.

     The known-device cookie (except as in owner question 4) is set on the response after the
     commit. So a reset either commits first (the credential is gone or the session is never made)
     or waits for the lock and then revokes the new session with the rest; no session made from a
     passkey outlives a reset that deletes it.
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
  now() RETURNING …` **before** verifying, in a transaction of its own that **commits before
  anything else runs**, so a replay or a second parallel finish finds nothing and the take holds no
  row lock afterwards. Missing, expired, wrong purpose, or (for `register`/`reauth`) another
  session or user: the generic failure. The cookie is cleared either way. A failure after the take
  (verification, the counter, the session re-check) leaves the challenge consumed: the user starts
  again.
- **Lock order (review point D).** Without it, a register finish holding its challenge row and
  waiting for the `users` row could deadlock with a reset holding the `users` row and deleting the
  sessions, whose cascade deletes that challenge row. The rules:
  1. A challenge take is always its own committed transaction, never combined with a later write.
  2. Every transaction that writes a `webauthn_credentials` row (insert, delete, the counter
     update), creates or rotates a session from a passkey, or revokes a user's sessions on the reset
     or `mfa-reset` path, takes `SELECT … FROM users WHERE id = … FOR UPDATE` as its **first** lock
     (after consuming a reset token, which locks only the token row and is never taken in the other
     order). That names the register finish (§4), the **sign-in finish** (§5, step 5), the
     **re-auth finish** (§8), the remove route (§7), the reset (§9) and `mfa-reset`. There is no
     exemption: a finish that updated the counter first and then inserted a session (whose foreign
     key takes `FOR KEY SHARE` on `users`) or ran the grant check (`UPDATE users`) would take the
     locks in the reset's opposite order, and one that committed the counter update separately would
     let a reset slip in between and leave a session made from a deleted passkey.
  3. Verification (CPU-bound signature checks) never runs while a lock is held.
  Tests: with a `users` row lock held by the test, a register finish, a sign-in finish, a re-auth
  finish and a reset each wait and then complete (no deadlock error); a register finish racing a
  reset ends with either the passkey deleted by the reset or no passkey stored (W2, W5); a sign-in
  or re-auth finish racing a reset that drops its passkey ends with no session that outlives the
  reset and no deadlock error (W3, W4, W5).
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

**Signature counter.** The rule is ours, not the library's (§2: the verify calls get
`credential_current_sign_count=0`). The update is one statement, run inside the finish's write
transaction after the user row lock (§5, §6):
`UPDATE … SET sign_count = :new, last_used_at = now(), backup_state = :bs WHERE id = :id AND
(sign_count < :new OR (sign_count = 0 AND :new = 0)) RETURNING id`.
- Both zero (most synced passkeys): accepted; replay protection comes from the single-use challenge.
- Otherwise the new value must be greater. An equal or lower one updates nothing. A replayed
  assertion never gets here (it fails at the single-use challenge first), so only a holder of the
  private key can cause this: a cloned authenticator, or two genuine parallel sign-ins arriving out
  of order. The conditional update also makes the second of those lose cleanly. When it returns no
  row and the credential still exists, the sign-in (or re-auth) is refused, `auth_events` gets
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
  `passkey_renamed` and `passkey_removed`; removal mails the user. The delete takes the user row
  lock first (§6).
- **With the flag off** (§1): `GET` and `DELETE` answer as above (the same guards, the same 404 for
  an unknown or foreign id); `PATCH` answers 404 `feature_off`, checked before the id, so the flag
  state is all it reveals.

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
  verification as in §5 (counter 0 to the library, the three algorithms, no lock held), then one
  write transaction that locks `users` first, runs the counter rule on `credential_id = :id AND
  user_id = :session_user`, and rotates; a session revoked meanwhile (`SessionGone`) rolls it all
  back and fails (§6). Success sets `reauth_at`, rotates the session through `sessions.rotate_session` (ADR 0003 §3; never
  a hand-written update, so #632's client mark is stored and `location_changed_at` cleared exactly as
  for a password re-auth, §13) and writes `reauth` `{method: "passkey"}`. The re-auth prompt offers it
  beside the password and the providers.
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
  added does not survive the reset. It does so **whatever `CLASSBEAT_PASSKEYS` says**.
- **A keep list, not a remove list (review point B).** `POST /password/reset/info` returns
  `passkeys: [{id, label, created_at, last_used_at}]` (it does not consume the token, as today).
  `POST /password/reset/complete` accepts `keep_passkeys: [id]`: the passkeys the user saw and left
  ticked "keep". In the transaction that consumes the token, after `SELECT … FROM users … FOR
  UPDATE` (§6), it deletes **every passkey of the token's user whose id is not in the list**, then
  revokes the sessions as today. So a passkey registered after `info` (by someone holding a session
  in the window between the two calls) was never shown, cannot be in the list, and is deleted; a
  register finish that has not committed when the lock is taken finds its session revoked and
  stores nothing (§4). An id in `keep_passkeys` that is not the user's (unknown, foreign, or
  removed meanwhile) is ignored, never an error, so the answer names nothing about other users and
  a stale page still completes; the event records `passkeys_removed` as a count. The failure mode of
  a client bug is an empty list: every passkey goes and the user, who has just set a password,
  registers again. Identities keep their existing `remove_methods` contract (ADR 0003 §5); the same
  window exists for an identity linked between `info` and `complete`, which is outside this ADR
  (named for the Architect in the #311 PR).
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
| Sign-in start | — | 60 / 5 min per address (`bucket_ip`: IPv4 as it is, IPv6 per /64); 300 / 5 min per IPv6 /48; 20 000 / 5 min for all callers together | every request (below) |
| Sign-in failures | 20 / hour (when the credential maps to a user), except from a known device | 30 / 15 min | failures, released on success |
| Register start and finish | 10 / hour per user | 30 / hour | every request |
| Passkey re-auth failures | the password re-auth buckets | the same | failures |
| Rename, remove | 30 / hour per user | — | every request |

**Sign-in start (review point C).** Each start writes a challenge row that lives 5 minutes, and
autofill starts one on every sign-in page load, so the start limits double as the bound on the
table. They are **limiter buckets** (one atomic `INSERT … ON CONFLICT DO UPDATE … RETURNING count`
per bucket, ADR 0003 §9), not a `count(*)` of live rows, so parallel starts cannot overshoot.
**Reserved in order, stopping at the first refusal (review of #638):** the address bucket first,
then (IPv6 only) the /48 bucket, then the global bucket, each only if the one before allowed the
start. `RateLimiter.begin` on `main` takes a slot in **every** bucket it is given even after a
refusal (a refused attempt counts everywhere), so one address sending starts at full speed would
fill the /48 and global buckets with nothing but 429s and lock everyone else out. W3 therefore makes
three separate `begin` calls (or adds a short-circuiting variant, `begin_in_order`, to
`ratelimit.py`) so a start refused by a narrower bucket takes no slot in a wider one. A refused
start still counts in its own bucket. So the global count over a window is at most 60 per IPv4
address and 300 per IPv6 /48 (at most five /64s' worth of allowed starts reach it), which is what
the residual below rests on. A window is 5 minutes, as long as a row lives, so at most two windows'
worth of rows are ever live: 120 per address, 600 per /48, 40 000 in all (a few MB). The /48 bucket
is a new key helper next to `bucket_ip` (IPv4 has no /48 bucket); it exists because an IPv6 /48,
65 536 /64s, is a cheap allocation. The global bucket is one hot row; at beta volume that is
fine, and W3 measures it in its test of parallel starts.
- **A tripped address or /48 bucket** answers 429 `rate_limited` with `Retry-After`; the SPA
  stops autofill and keeps the button.
- **A tripped global bucket** answers **503 `passkey_busy`** with `Retry-After`; the SPA stops
  autofill and says "Passkey sign-in is busy right now. Sign in with your password or a provider,
  or try the passkey again in a few minutes." Password and provider sign-in use other buckets and
  are unaffected.
- **Residual (T119).** About 334 IPv4 addresses (20 000 / 60), or 67 IPv6 /48s, starting
  ceremonies at full rate can make passkey sign-in starts answer `passkey_busy` for everyone, in
  windows of up to 5 minutes for as long as they keep at it. (These numbers hold only because of the
  ordered, short-circuiting reservation above; with `begin` over all three buckets one address
  would be enough.) Passwords and providers keep working
  and nothing is locked. Accepted for the beta; the owner runbook (W8) names it and what to look
  for (429s and `passkey_busy` in the access log).

The known-device exemption matches ADR 0003 §9. The signed-in passkey routes (register, re-auth,
list, rename, remove) are also counted by the general per-user API limit of #626 (ADR 0003, amendment
"a stolen session (#577)" of 2026-11-05) like every other signed-in `/api` request; the anonymous sign-in routes are
not, since it applies to signed-in requests only.

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
  Any `Permissions-Policy` that web or the owner's swag sample sends must allow both
  `publickey-credentials-get` and `publickey-credentials-create` for `self`, by naming them as
  `(self)` or by leaving them out (their default is `self`); `()` for either breaks passkeys. W8
  adds a line to `scripts/check-prod-compose.sh` that fails if the swag sample's header gives either
  feature `()` or `*`, and the runbook says why.
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
  redaction test. Added by this revision: a passkey registered **after** the reset page's `info`
  call is deleted by `complete` (B), and a foreign or unknown id in `keep_passkeys` is ignored; the
  lock-order tests of §6 (D); the sign-in start buckets refusing the 61st start from one address
  with 429, the 301st from one /48, and the global bucket answering 503 `passkey_busy` while a
  password sign-in still succeeds (C); list and remove answering with the flag off and rename,
  register, sign-in and re-auth answering 404 `feature_off` (E); and, once #632 is on `main`, a
  network move between register start and finish failing the finish with `reauth_required` (§13).
- **What the tests prove, and what they cannot.** Everything the server does is tested with the
  software authenticator and the netguard (no network, no real authenticator, no account): option
  contents, every refusal above, the counter rule, the challenge's single use and binding, the
  user binding of re-auth, the rate limits, recovery, the redaction of logs, events and errors, the
  export and the deletion cascade. The software authenticator builds real WebAuthn structures, so
  py_webauthn's parsing and signature checks run unmodified. Not provable in CI: how real
  authenticators and browsers behave (synced passkeys reporting counter 0, the platform's UV
  prompt, conditional mediation in each browser) and the hybrid leg (QR code, Bluetooth proximity,
  the phone's signature). The server cannot tell a hybrid assertion from a USB key's, which is why
  the `cross-platform` e2e case stands for it. The owner's first real-phone check after switching
  the flag on (W8 runbook: register on a computer, then sign in on another computer through
  "use a phone") is the only evidence for those, and it is the owner's own action on their own
  account.
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
  (`E2E_PASSKEY_BASE_URL`), and the e2e stack checks treat it like `web` (non-root, no model key).
  **Secrets (review point E):** `web-passkeys` mounts what web needs to start. CI's "Check
  database_maint_password is mounted on db-functions and web only" step expects exactly
  `db-functions web`; W7 either leaves that secret off `web-passkeys` (preferred, if web starts
  without it; the admin area is not used by the passkey specs) or changes the expected list to
  exactly `db-functions web web-passkeys`, names the reason in the step's comment, and gets the one
  final Opus review that Amendment 13 requires for a change to how a secret is mounted. The other specs are untouched. Local development may use `localhost` with
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
| §6, the guarded actions (review point A) | `reauth_at` within 10 minutes is needed to link or unlink an identity, set or change the password, change the email, enroll TOTP for the first time, connect or disconnect MOWL, Spotify or Swedebeat, delete the account | Adds: add a passkey (start and finish), rename a passkey, remove a passkey; with confirmed TOTP each also needs the step-up, like the rest of that list (§4, §7) |
| §9, the rate-limit table (review point A) | Password, TOTP, sign-up, mail and OAuth rows; sign-in limits count failures only | Adds five rows: passkey sign-in start (every request counts, per address, per IPv6 /48 and global), passkey sign-in failures, register, passkey re-auth failures (the password re-auth buckets) and rename/remove (§10) |
| §5, reset contract | `remove_methods: [id]` for identities | Passkeys use `keep_passkeys: [id]`: every passkey not listed is deleted under the user row lock (§9) |

### 13. Interplay with the session anomaly signal (#632)

#632 is in development in parallel (branch `632-session-anomaly`, its ADR 0003 amendment
"noticing a session used from a new network", migration 0033). It touches `start_session`,
`rotate_session`, `deps.resolve_request`, the `sessions` table and the auth middleware. This design
**depends on it only through existing interfaces** and adds nothing to it:
- **Recent authentication.** On a move #632 clears `reauth_at`, `stepup_at` and `mfa_at`. Register,
  rename and remove use `require_recent_auth` (with the step-up for TOTP users), so they need a fresh
  re-authentication after a move without any passkey code knowing about moves; the register finish
  re-checks it (§4). **Dependency:** #632 must keep clearing the columns that
  `SessionInfo.recent_auth` and `recent_stepup` read (it does on its branch), not keep a separate
  per-route list; if that changes, W2 adds the passkey routes to it.
- **Sign-in.** W3 calls `start_session` exactly as the password sign-in does after #632, passing
  what #632's signature needs to set the client mark from the request. For a cross-device sign-in
  the request comes from the computer's browser, so the mark is the computer's network and browser,
  not the phone's, and no false move follows.
- **Re-auth.** W4 rotates through `rotate_session` (§8), which on #632 stores the new mark and
  clears `location_changed_at`. A passkey re-auth on the new network therefore restores the guarded
  actions, as a password re-auth does. A stolen session on a new network cannot do this: the
  re-auth looks the credential up with the session's user (§7, T120) and needs the victim's
  authenticator with user verification.
- **Challenges.** A move does not rotate the session (#632 updates the row in place), so a pending
  register or re-auth challenge survives it; the register finish then fails on recent authentication
  and the re-auth finish succeeds only with the user's own passkey.
- **Order and conflicts.** If #632 merges first (expected), W1 takes the next free migration number
  after 0033 and W3 and W4 build on #632's `start_session` and `rotate_session`. If W3 were ready
  first, it would merge `main` again after #632 lands and get a scoped review (AGENTS.md review gate,
  item 3). This ADR PR touches only ADR 0019, a short pointer at the top of ADR 0003 (away from the
  `sessions` row, §8 and the end of the file, which #632 edits) and the ADR index, so it does not
  conflict with #632.

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

New rows, added by W8 when the code they describe is on `main`. T113 to T120 are claimed by this
ADR (T112 is the SMTP pin; T121 to T123 were taken on `main` meanwhile and are not passkeys'):

| # | Threat | Mitigation |
|---|---|---|
| T113 | Phishing a passkey, or RP ID / origin confusion through a forged `Host` or proxy header | The browser binds assertions to origin and RP ID; the server's RP ID and origin come only from settings read at start, never from request headers; exact origin match; start-up check refuses an IP or unrelated RP ID (§3) |
| T114 | Replaying an assertion, or login CSRF / a transplanted assertion signing a victim's browser into another account | 32-byte challenge, single use (`DELETE … RETURNING` in its own committed transaction before verification), 5 minutes, found only through the starting browser's `SameSite=Strict` `__Host-` cookie and, for register and re-auth, bound to the session; CSRF token and Origin check on every finish (§6) |
| T115 | A rogue passkey planted with a stolen session or password for lasting access | Registration needs recent authentication and, with TOTP, the step-up; every addition is mailed; the reset page and `admin mfa-reset` remove passkeys; at most 10; the reset deletes every passkey not on the user's keep list under the user row lock, so one registered between the reset page's `info` and `complete` does not survive, and a register finish racing the reset stores nothing once the sessions are revoked (§4, §6, §9) |
| T116 | A cloned authenticator | Counter must increase unless it is 0 on both sides; a regression refuses the sign-in, is recorded and mailed; the backup-eligible flag may not change. Residual: synced passkeys at 0 get no clone detection (accepted) (§7) |
| T117 | Credential material or library error text in logs, errors, events or exports | Request and response bodies under `/api/auth/` not logged; library exceptions mapped to categories; events hold row ids and categories only; the export leaves out the public key and counter; redaction test with planted values (§10) |
| T118 | A session left on a shared or gym computer after a cross-device sign-in; the fake-QR attack | No custom QR flow (the browser's hybrid transport checks proximity); shorter session and no known-device cookie on a cross-platform sign-in (owner question 4); "sign out" shown after it (§5) |
| T119 | Lock-out by removing the last method; account enumeration through passkey sign-in | ADR 0003's last-method rule unchanged (a password or an identity always remains; passkeys never count), under the user row lock; username-less start with empty `allowCredentials`; one generic failure. Residual: `passkey_unknown` versus `passkey_failed` tells a caller whether a credential id is registered; accepted, because ids are random and the caller must hold the authenticator. Sign-in start is bounded by atomic limiter buckets per address, per IPv6 /48 and globally, reserved in that order and stopping at the first refusal, so a refused start never counts in a wider bucket; residual: about 334 IPv4 addresses or 67 IPv6 /48s can make passkey sign-in start answer 503 `passkey_busy` for everyone in 5-minute windows while they keep at it, never password or provider sign-in (accepted for the beta, runbook) (§5, §7, §10) |
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

**What each answer blocks.** Only question 1 blocks code: W1 adds the dependency and the licence
wording, so W1 waits for a yes (a no means the Architect revisits §2, python-fido2 being the same
licence family). Question 4 decides whether W9 exists (and so whether W6 depends on it). Questions
2 and 6 block only switching the flag on in production, never a merge. Questions 3 and 5 have
defaults the issues build to; a different answer is a later amendment.

## The issues

Owner lines (AGENTS.md "Roles"; owner decision of 2026-10-28 and roadmap Amendment 13): **Opus /
Opus** = Opus Developer and a fresh Opus Reviewer, for auth, sessions, challenges, counters,
recovery and migrations on account tables; **Sonnet / Sonnet** = UI over reviewed routes, e2e and
docs. W7 gets one final Opus review on top if it changes which services mount a secret (§11). All
depend on this ADR being accepted; M6 is done technically (`docs/status.md`) and the owner said to
start #311 on 2026-11-05. #311 stays open as the tracker; each row becomes one issue ("Part of
#311").

| # | Title | Scope | Acceptance criteria (each tested) | Owner | Depends on |
|---|---|---|---|---|---|
| W1 | `feat(auth): passkey tables, settings and dependency` | migration (next free number after #632's 0033): `webauthn_credentials`, `webauthn_challenges`, `users.webauthn_user_handle`; `classbeat.auth.tables`, guard 8, `coverage`, export `INCLUDE`/`EXCLUDE`/`NOT_EXPORTED`, purge; `CLASSBEAT_PASSKEYS`, `CLASSBEAT_WEBAUTHN_RP_ID` and the start-up check; `webauthn==3.0.1` pinned and its API names confirmed (§2); the licence wording in `docs/developer-commands.md`; the IPv6 /48 bucket helper | guard 8, guard 12 and the export schema test pass with the new tables; the delete test plants a passkey and a challenge; start-up refuses an IP, a port, a trailing dot and an unrelated host; the options' RP ID and the expected origin do not change with a forged `Host` or `X-Forwarded-Host`; the purge drops expired challenges only; pip-audit clean; the resolved dependency set listed in the PR | Opus / Opus | this ADR accepted; owner question 1 |
| W2 | `feat(auth): register, list, rename and remove passkeys` | the challenge store and per-purpose cookies (§6), the user-handle update, register start/finish in the three steps of §4 with the lock order of §6, list, rename, remove (list and remove with the flag off, §1), mails and events, the register, rename and remove rate limits of §10 | the registration refusals of §11 (origin, RP ID, replayed and expired challenge, another browser's cookie, another session's flow, UV unset, duplicate credential id, an 11th passkey, finish after the session was revoked); two parallel first starts give one user handle; recent auth and step-up checked at start and finish; 404 for foreign and unknown ids; an identity unlink still refused with passkeys present and a passkey removal always allowed; the flag-off answers of §7; the lock-order test of §6 | Opus / Opus | W1; #632 merged (else a scoped re-review after it lands) |
| W3 | `feat(auth): username-less passkey sign-in` | sign-in start/finish, the counter update, `passkey_unknown`, the session through `start_session` as built by #632, the sign-in start buckets and 503 `passkey_busy` (§10), failure buckets, the redaction test | the sign-in refusals of §11 (origin, RP ID, replayed, expired and foreign-browser challenge, missing or foreign user handle, UV, signature, backup-eligible flag); counter cases (0/0 accepted, rollback, equal non-zero, non-zero to 0 refused, recorded, mailed once a day, passkey kept); a disabled user's generic failure; an admin's role still needs TOTP; the start buckets' 429 and 503 with a password sign-in still succeeding; 1 000 starts from one address leave the global count at 60 and the /48 count only counts starts its /64 bucket allowed; the sign-in finish's single write transaction (users lock, counter, grant, `start_session`) and its race test against a reset (no surviving session, no deadlock); the counter cases decided by our `UPDATE` with the library given 0; the redaction test with every planted value; no network (netguard) | Opus / Opus | W2, #632 |
| W4 | `feat(auth): re-authenticate with a passkey` | reauth start/finish bound to the session, the credential looked up with the session's user (§7), rotation through `rotate_session` (§8, §13), events | sets `reauth_at`, never `stepup_at` or `mfa_at`; another session's flow refused; **another user's valid passkey refused, its counter and `last_used_at` unchanged**; the re-auth finish's write transaction locks `users` first, and a re-auth racing a reset that drops its passkey leaves no rotated session and no deadlock; after a #632 move a passkey re-auth restores the guarded actions and clears `location_changed_at` | Opus / Opus | W3 |
| W5 | `feat(auth): passkeys in reset and mfa-reset` | `info` returns `passkeys`, `complete` takes `keep_passkeys` and deletes the rest under the user lock (§9); `admin mfa-reset` deletes them; both whatever the flag says; mail and event text | an attacker's passkey removed on reset with the flag on and off; a passkey registered after `info` deleted by `complete`; a foreign or unknown id in `keep_passkeys` ignored and the reset completes; an empty list removes all; mfa-reset leaves none; the reset-versus-register, reset-versus-sign-in and reset-versus-re-auth race tests of §6; events hold counts only | Opus / Opus | W2 |
| W6 | `feat(web): passkey UI on the account, sign-in and re-auth pages` | `webauthn.ts`, the Passkeys card (with the flag-off text of §1), sign-in button and autofill (restarted only while visible), the `passkey_busy` and 429 messages, re-auth option, the reset page's keep list, `signalUnknownCredential`/`signalCurrentUserDetails` where present; owner question 4's notice if W9 exists | component tests with a mocked `navigator.credentials`; hidden without `PublicKeyCredential`; the reset page sends every unticked passkey in `keep_passkeys`; axe clean; works under the CSP | Sonnet / Sonnet | W2, W3, W4, W5; W9 only if owner question 4 is yes |
| W7 | `test(e2e): passkeys with a virtual authenticator` | the `web-passkeys` service on `localhost` (§11), its secrets per §11 and the CI secret check, the specs of §11 | the specs pass in CI against `web-passkeys`; the other specs and their base URL unchanged; the `database_maint_password` step still passes with the list §11 names | Sonnet / Sonnet (+ one final Opus review if a secret mount changes) | W6 |
| W8 | `docs: passkeys in developer commands, threat model and runbook` | settings, routes and per-feature behaviour in `docs/developer-commands.md` (where AGENTS.md "Developer commands" sends them), T113 to T120 replacing the reservation line in the threat model, the ADR 0003 pointers of §12, the `Permissions-Policy` check in `scripts/check-prod-compose.sh` (§11), an owner runbook (RP ID choice, switching on and off, what users see, the `passkey_busy` residual, the owner's real-phone check, hybrid documented) | rows describe what is on `main`; the check refuses a swag sample that gives either publickey feature `()` and accepts today's; runbook names no secret | Sonnet / Sonnet | W7 |
| W9 | `feat(auth): owner question 4, shorter cross-device session` (only if the answer is yes; may fold into W3) | per-session absolute expiry on a cross-platform assertion, no known-device cookie | the session expires at 24 hours; no device cookie set; a later rotation keeps the shorter expiry | Opus / Opus | W3 |
