# ADR 0034: classbeat-player: sign in with classbeat, and "Send to player"

Status: Proposed (owner direction 2026-11-14; issue #1054; roadmap Amendment 22)

## Context

The owner, 2026-11-14:
- "We use classbeat to produce the class, classbeat-player reads it and runs it."
- "classbeat today creates classes for MOWL format, we simply want a second format. A new
  connection to classbeat-player. Then it should work quite similar."
- "A classbeat account is required to run classbeat-player. Same user, same account, same login
  reused." Riders in a class need no account.

classbeat-player (magpern/classbeat-player) is a separate product with its own backend,
database and origin. Its side is fixed in its own ADRs, which this ADR follows and does not
restate:
- [ADR 0010, sign in with classbeat](https://github.com/magpern/classbeat-player/blob/main/docs/adr/0010-sign-in-with-classbeat.md):
  - the OIDC code flow with PKCE and `client_secret_basic` (§2);
  - the ID-token checks the player makes (§3), and its re-authentication rule (§5);
  - pairwise subjects (§4);
  - the signed events `account-deleted` and `sessions-revoked` (§8);
  - the source-link binding, `login_hint` and `account_subject` (§10).
- [ADR 0002 §7](https://github.com/magpern/classbeat-player/blob/main/docs/adr/0002-accounts-sign-in-pairing-and-tokens.md):
  - the source link (PKCE, no secret, a `cbp_lc_` code and a `cbp_src_` token);
  - publications, with their numbers, repeats, conflicts and removal markers.
- [ADR 0003](https://github.com/magpern/classbeat-player/blob/main/docs/adr/0003-class-format.md)
  and `schema/class-format/v1`: the class format (1.1 adds beat patterns) and its content-hash
  rule, with the reference converter `schema/converter/reference_converter.py`.

Today classbeat is an OAuth client only (Google, GitHub, Microsoft). It holds encrypted
connections (ADR 0004), a session with a 10-minute re-authentication and TOTP step-up (ADR 0003
§6), and writes to MOWL through jobs and external operations (ADR 0005 §7, ADR 0007 §5–9).

Every place where this ADR departs from an accepted ADR says **"Amends ADR NNNN §x"**; the
Consequences list them all.

## Decision

### 1. classbeat as an OpenID Connect provider (one client)

classbeat becomes a provider for **exactly one registered client**, the player. There is no
dynamic registration and there are no third-party clients.

**The client** is settings plus secret files:
- client id and the exact redirect address (settings);
- the client secret's hash: SHA-256 of a 32-byte random secret that only the player's operator
  holds in clear (secret file). The token endpoint compares hashes in constant time.

**The issuer** is `CLASSBEAT_PUBLIC_ORIGIN` exactly: `https://<host>`, no path, no trailing
slash. `iss`, the discovery document and `account_subject.iss` (§3) all use this one string.

**Endpoints**, under the public origin and outside `/api`:
- `GET /oidc/authorize` and `POST /oidc/authorize/consent`;
- `POST /oidc/token`;
- `GET /oidc/jwks`;
- `GET /.well-known/openid-configuration`, serving exactly:
  - `issuer`, `authorization_endpoint`, `token_endpoint`, `jwks_uri`;
  - `response_types_supported: ["code"]`, `grant_types_supported: ["authorization_code"]`;
  - `subject_types_supported: ["pairwise"]`;
  - `id_token_signing_alg_values_supported: ["ES256"]`;
  - `token_endpoint_auth_methods_supported: ["client_secret_basic"]`;
  - `code_challenge_methods_supported: ["S256"]`;
  - `scopes_supported: ["openid", "email"]`;
  - `claims_supported: ["iss","aud","azp","sub","iat","exp","auth_time","nonce","email","email_verified"]`;
  - `backchannel_logout_supported: true`, `backchannel_logout_session_supported: false`.

There is no user-info endpoint, no refresh token and no `offline_access`.

**Tables.** All are **system tables** (ADR 0002 §3): they are read before a user is known (the
token endpoint) or must outlive one (the outbox, §2). Each is listed in `SYSTEM_TABLES` with its
reason and is reached **only through `classbeat.auth.oidc`** (and `classbeat.player.outbox`),
the pattern `sessions` and `oauth_flows` already use. No `SECURITY DEFINER` function is needed,
because no owned table is read across users.
- `oidc_requests`: one pending authorization. Columns: id, `session_hash`, the validated request
  (`redirect_uri`, `state`, `nonce`, `code_challenge`, `prompt_login boolean`), `received_at`,
  `expires_at` (10 minutes). At most 3 per session. Purged hourly with the other flows.
- `oidc_codes`: `code_hash bytea UNIQUE` (SHA-256 of 32 random bytes), `user_id` (FK, cascade),
  `client_id`, `redirect_uri`, `code_challenge`, `nonce`, `auth_time`, `expires_at` (60 s),
  `used_at`. Single use; a second use is refused and writes `oidc_code_refused`.
- `oidc_grants`: one row per user and client. `user_id` (FK, cascade), `client_id`,
  `consent_at NULL`, `first_issued_at NULL`. `first_issued_at` is set when the first ID token is
  issued and never cleared: it is the durable marker of "this user has a player account" that §2
  uses. `consent_at` is cleared by withdrawal.

**Authorize** (in the user's classbeat cookie session):
- `client_id`, `redirect_uri` (exact match), `response_type=code`, `scope` holding `openid`
  (only `openid` and `email` are allowed), `state`, `nonce`, `code_challenge` and
  `code_challenge_method=S256` are required. A bad client or redirect shows an error page and
  never redirects. Any other problem redirects to the registered address with
  `error=invalid_request&state`.
- `prompt` may be absent or `login`. `none`, `consent`, `select_account` and `max_age` answer
  `error=invalid_request`, because the player never sends them.
- The request is stored in `oidc_requests`. A signed-out user signs in first and comes back to
  `/oidc/authorize/continue?r=<id>`, a relative `return_to` (ADR 0003 §4). Only the same session
  can continue a request.
- A disabled user, or one whose role forbids sign-in, is refused with `error=access_denied`.
- **Consent.** The first time per user, a consent page names the player and what it gets (a
  sign-in and the email). `POST /oidc/authorize/consent` is CSRF- and Origin-checked. Denial
  redirects with `error=access_denied&state` to the registered address only. The page sends
  `frame-ancestors 'none'`. Consent is withdrawn on the account page.
- **`auth_time`** is defined as:
  - without `prompt=login`: the later of the session's sign-in time and its `reauth_at`;
  - with `prompt=login`: the moment of a **new** re-authentication made in this flow, after
    the request's `received_at`. A `reauth_at` from before, even within the 10 minutes, does
    not count. A user with confirmed TOTP also does the step-up in the same flow. Any of the
    re-authentication methods of ADR 0003 §6 serves (password, Google re-auth, passkey, admin
    TOTP).
  - A user who has no way to re-authenticate gets `error=login_required`, and the page says to
    add a password or a passkey first.

  This matches player ADR 0010 §5, which refuses an `auth_time` older than its flow's start
  minus 60 seconds.

**Token endpoint:**
- Client authentication is `client_secret_basic` only; any other method or a bad secret gives
  `401 invalid_client` and writes `oidc_client_auth_failed`.
- It is a server-to-server call, so it is exempt from ADR 0003 §4's Origin and CSRF checks
  (**amends ADR 0003 §4**). The client secret takes their place.
- The body is capped at 8 KiB. The answer sends `Cache-Control: no-store`.
- The code is redeemed only if the PKCE verifier, client, redirect and expiry match and the
  user is still active and not disabled.
- The answer is `id_token`, `token_type: Bearer`, `expires_in: 300` and an `access_token`, a
  random value that is stored nowhere and grants nothing.

**The ID token** is a JWT with `alg` ES256, `typ` JWT and a `kid`. Its claims are:
- `iss`, `aud` (the client id), `azp`, `sub`;
- `iat` and `exp` (5 minutes);
- `auth_time` and `nonce`;
- `email` and `email_verified`.

**Pairwise subject:**
- `sub` = base64url without padding of HMAC-SHA256(pairwise key, UTF-8(client id) ‖ 0x00 ‖
  `users.id` as its 16 bytes in RFC 4122 order). That is 43 characters.
- The pairwise key is 32 random bytes in a secret file. **It never rotates.** Rotating it would
  change every `sub`, orphan every player account and allowlist entry, and make §3's binding
  refuse every link. If it is lost, every player account must be linked again by the player's
  operator. If it leaks, the holder can match subjects to classbeat user ids; it grants no
  authority.
- `python -m classbeat admin player-subject <email>` (maint profile) prints a user's subject
  for the player's operator (player ADR 0010 §4) and writes an `admin_audit` row. It is
  read-only.

**Signing keys:**
- ES256 (P-256) private keys come from secret files, each with a `kid`. Their mode and group
  are those of the other secret files (roadmap Amendment 13).
- The key set publishes the current key and, during a rotation, the next one.
  - A new key is published **at least 24 hours and 5 minutes before it signs**, because the
    player caches the key set for 24 hours.
  - A retired key stays published for 24 hours after its last signature.
- The private key is never logged, never a setting and never sent to a model.
- Rotation is the CLI command `python -m classbeat oidc keys` (list, add-next, promote, retire)
  and a runbook entry.

**Limits** (ADR 0003 §9, reserve first):
- authorize starts: 30 per user and hour, 120 per address and hour;
- consent posts: 20 per user and hour;
- token endpoint **failures** (bad secret, bad or used code): 20 per address per 10 minutes.
  Successful calls are not counted per address, since every call comes from the player's one
  server. A global ceiling of 600 calls per minute applies.
- Failures per client are only counted for an admin warning above 100 per hour. They never
  block, so nobody can lock out the one client.

**Logging** (**amends ADR 0003 §10**): no query string and no body on `/oidc/*` is logged.

**Audit.** These are `auth_events` kinds (ADR 0003 §10), not the owned security log:
- `oidc_signed_in` (a code redeemed, with the user);
- `oidc_consent` and `oidc_consent_withdrawn`;
- `oidc_code_refused` (the user when the code names one, else none);
- `oidc_client_auth_failed` (no user).

### 2. Signed events to the player

**Who gets events:** only users whose `oidc_grants.first_issued_at` is set. Nobody else's
deletion or revocation leaves classbeat.

**The outbox** `player_events` is a system table (§1), reached only through
`classbeat.player.outbox`. Columns:
- `id`, `kind` (`account_deleted` or `sessions_revoked`), `sub` (the pairwise subject), `jti`;
- `created_at` and `give_up_at` (created + 7 days);
- `state` (`pending`, `failed`), `attempt`, `available_at`;
- `lease_version`, `leased_by`, `lease_expires_at`;
- `last_error` (a category, never a body).

It has no `owner_id` and no user id, so it survives the user's deletion and the deletion scan of
ADR 0014 §4 finds nothing of the user in it.

**The token** is an OpenID Connect back-channel logout token:
- the header has `typ` `logout+jwt`, `alg` ES256 and the `kid`;
- the claims are `iss`, `aud` (the client id), `iat`, `exp` (`iat` + 5 minutes), `jti` (32
  random bytes, base64url), `sub`, and `events`, with no `nonce` and no `sid`;
- `events` is exactly one of these objects:
  - `{"http://schemas.openid.net/event/backchannel-logout": {}, "urn:classbeat:player:account-deleted": {}}`
  - `{"http://schemas.openid.net/event/backchannel-logout": {}, "urn:classbeat:player:sessions-revoked": {}}`

  These URNs are what player ADR 0010 §8 calls `account-deleted` and `sessions-revoked`. A player
  docs pull request pins the same strings before PC-2 merges.
- It is POSTed as the form field `logout_token` to the player's `/api/classbeat/events`.

**When rows are written** (in the transaction of the change):
- `account_deleted`:
  - the user's `POST /api/account/delete` (ADR 0014 §3 step 4);
  - the operator's erase from the Users screen (the `erase` path).

  In both, the grant is read and the row written **before** the `users` row goes. Any pending
  `sessions_revoked` row for that `sub` is deleted in the same transaction. No class removal is
  sent: the player deletes the whole account.
- `sessions_revoked`, on every path of ADR 0003 §3 and §7 that ends all, or all other, sessions
  of the user:
  - password change;
  - email change;
  - completing a reset;
  - a role grant (CLI or the `ADMIN_EMAILS` grant at sign-in) and `admin revoke`;
  - `admin mfa-reset`;
  - the TOTP reset of `keys wipe`;
  - admin disable;
  - sign out everywhere;
  - TOTP removed or replaced.

  Signing out one session, or one other session, writes nothing. The player's sessions are its
  own, and the user ended only one classbeat session.

  A new row is skipped when one for the same `sub` is still `pending` and not leased, since the
  effect is the same.
- **Withdrawing consent writes no event.** The player account stays and events still go to it
  (`first_issued_at` is kept). The next sign-in asks for consent again. Withdrawal does not end
  the `player` connection (§3); disconnect does that.

**Delivery** is its own worker loop, not a job (ADR 0005 jobs are owned and cascade with the
user):
- Claiming borrows ADR 0005 §3's lease and fence: `FOR UPDATE SKIP LOCKED`, a 60-second lease
  and `lease_version + 1` on claim. The outcome is written only under the same `lease_version`.
  A reaper requeues expired leases.
- Each attempt signs a fresh token with the same `jti`. A `2xx` answer deletes the row.
- Any other answer, or no answer, is retried after `min(30 s · 2^(attempt−1), 6 h)` ± 10 %,
  until `give_up_at`. That is about 35 attempts over 7 days, capped at 40.
- After that the row is `failed` and listed in the admin area (kind, `sub`, dates, last error),
  so the operator can tell the player's operator (player ADR 0010 §8, `admin account delete`).
  Failed rows are purged after 30 days.

**Egress** (**amends the rule in `egress.py` that "the list is code, not a setting"**):
- The player's host comes from `CLASSBEAT_PLAYER_BASE_URL`, because the owner hosts the player
  on a server of their own choosing and a release per host would block every deploy. At startup
  the setting must be `https`, with port 443 or none, no userinfo, no path but `/`, no query or
  fragment, and a lowercase DNS name with at least one dot. It must not be an IP literal,
  `localhost`, or end in `.local`, `.internal` or `.localhost`. Otherwise the process refuses to
  start. Unset means the player features are off.
- The host is the only entry of `PLAYER_ALLOWLIST` and is on **no urllib list**.
- Only `player_async_client()` in `classbeat.egress` reaches it:
  - `trust_env=False`;
  - no redirects; a 3xx is an error;
  - a total deadline of 10 seconds, covering the body;
  - answers capped at 64 KiB;
  - a TLS context with no key log;
  - no argument; tests use `player_test_client`, which the guard refuses inside `classbeat`.

  It carries only the source token (§3) and event tokens, and only to that host.
- **Which process calls the player:**
  - the **web** process: the link's token exchange (`POST /api/source/token`), and
    `DELETE /api/source/link` on a mismatch, a reconnect or a disconnect (§3);
  - the **worker**: event delivery, the send and removal jobs, and the reconciler (§5).

  Both processes build the same client.

### 3. The `player` connection

`connections.provider` gains `player`, under ADR 0004: one row per user, encrypted, with the same
status rules. `secret` is the `cbp_src_` token. `public` holds the player's link id.

**The flow row** is an owned table, `player_link_flows`, modelled on ADR 0009 §3:
- columns: `owner_id`, `state_hash bytea UNIQUE`, `session_hash`, `verifier` (43 characters,
  S256), `redirect_uri`, `created_at`, `expires_at` (10 minutes) and `used_at`;
- at most 3 unexpired flows per user; purged hourly;
- `NOT_EXPORTED` in the export registry (a one-time verifier).

**Connect:**
1. **Start.** Recent authentication with step-up (ADR 0003 §6; **amends ADR 0003 §6** by
   adding the player to that list), CSRF, and 10 starts per user and hour. classbeat sends the
   browser to the player's `/link/source` with:
   - `source=classbeat`;
   - the fixed `redirect_uri` (`<issuer>/connections/player/callback`);
   - `state` and an S256 `code_challenge`;
   - `login_hint` = the user's pairwise subject.
2. **Callback.** One transaction takes the flow, single use, by `state_hash`, owner and session,
   as in ADR 0009 §4. A mismatch deletes only the caller's own flow. An `error` parameter ends
   it with no call.
3. **Exchange.** classbeat exchanges the `cbp_lc_` code at `POST /api/source/token` with the
   verifier, from the web process.
4. **Binding.** The answer must hold `account_subject: {"iss": <string>, "sub": <string>}`.
   classbeat keeps the token **only if `iss` equals classbeat's own issuer (§1) and `sub`
   equals the user's pairwise subject**. Otherwise it calls `DELETE /api/source/link` with that
   token at once, stores nothing and shows "This player account belongs to someone else".

**Reconnect** (a row exists):
- After the binding holds, classbeat first ends the old link with `DELETE /api/source/link` and
  the old token, then replaces the row in one transaction.
- Like ADR 0007 §9, that transaction deletes the user's pending, failed and uncertain player
  operations. A job in flight then ends `not_connected`.
- The publication counters (§5) stay. The binding guarantees it is the same player account.
- If the old `DELETE` fails, the reconnect still completes, and the page says the old link may
  still be listed on the player's site and can be removed there.

**Disconnect** (recent authentication, CSRF):
- classbeat calls `DELETE /api/source/link` and deletes the row in any case.
- A `401` means it was already gone. A network error or `5xx` shows the same "may still be
  listed" note.
- Pending player operations are deleted, as on reconnect.

The token never goes to a model, a log or the browser.

### 4. The converter

A pure module, `player_format_model`, converts one class revision into a player class document,
format 1.1:
- the class timeline: intervals with zone, cadence, position and the cue;
- the songs of the revision's song list, as song windows, with `bpm` and `beat_anchors` where
  known;
- `music.source` set to `spotify`, `swedebeat` or `none`.

Each module has one job:
- `player_hash_calc` computes the document's content hash by the player's rule;
- `player_format_policy` refuses a revision the format cannot express, with a reason the UI
  shows.

Neither module imports I/O. Beat patterns (`interval.pattern`) are written only when the
revision has them. classbeat's model has none today; adding them to class design is a later
issue.

The player's schema, its valid and invalid vectors and the reference converter are vendored,
pinned to a player commit, under `backend/tests/player_format/`. Tests check that:
- every document is valid against the strict schema;
- its hash equals the reference converter's;
- the golden revisions convert as expected.

Updating the pin is its own PR.

### 5. "Send to player"

**Publications** are an owned table, `player_publications`:
- columns: `owner_id` (cascade), `source_class_id uuid` (**no foreign key to `classes`**, so the
  row survives the class's deletion), `last_number bigint`, `sent_revision_id NULL`,
  `sent_document_hash NULL` and `removed_at NULL`;
- unique on `(owner_id, source_class_id)`; exported with the hashes omitted.

**The approval** is the user's press of "Send to player" for one revision (roadmap §2; ADR 0007
§5):
- `POST /api/jobs` with `kind = send_to_player` and `{class_id, revision_id, content_hash}`,
  where `content_hash` is the revision's stored hash, as for Save;
- a signed-in session, CSRF and an `Idempotency-Key` are required;
- the browser sends no document and no number. Everything comes from the stored revision.

**Revalidation, at ADR 0007 §5's three points, each failing closed:**
1. **`admit`:**
   - the class and revision are the user's own;
   - the revision is of a kind Save accepts, and its stored and recomputed content hashes equal
     the sent one;
   - `player_format_policy` passes;
   - the class has no open job (`class_busy`, the index Save uses);
   - no player operation of that class is `uncertain` (`unresolved_write`);
   - the `player` connection is `connected` (`not_connected`).
2. **`prepare`:** the same checks again, from the database. The document is converted here, in
   the worker, and `player_hash_calc` gives its `document_hash`.
3. **The decide transaction** (ADR 0005 §7 step 1), under `classbeat.connections.hold` on the
   `player` row:
   - the connection generation is re-read;
   - the publication row is locked and `last_number + 1` is taken;
   - the operation is written as `pending` with:
     - idempotency key `player:<source_class_id>:<number>`;
     - request hash: the canonical JSON of `{kind, source_class_id, publication_number,
       removed, document_hash}`.

**The call** sends the envelope `source_class_id`, `publication_number`, `removed: false`,
`content_hash` (the `document_hash`) and `document`.

**Outcomes:**
- `200 applied` or `200 repeat`: `succeeded`. The publication row records the revision and the
  document hash.
- `409 stale_publication` with the player's current number N:
  - if `last_number < N < 2^62`, the counter moves to N and the job fails `player_ahead`, so
    the user presses again;
  - any other N is `internal`;
  - there is no automatic resend.
- `409 publication_conflict`: `failed` with `player_conflict`, and the page says "changed on the
  player". **It is never resent automatically with a new number**, because that could silently
  bring back a class the instructor deleted on the player. A new press sends again with a new
  number, and the dialog says it replaces the player's copy.
- `401`: the connection becomes `reconnect`, and the job fails `not_connected`.
- The request provably never left (DNS, connection refused): `failed`, retryable under ADR 0005
  §4 with the same operation.
- Another `4xx`: `failed`, not retryable.
- A timeout after sending, a lost connection, or a `5xx`: `uncertain`. There is **one automatic
  reconciliation** (ADR 0005 §7 step 4) through `GET /api/source/classes/{source_class_id}`,
  which returns numbers only:
  - the player's current number equals ours: `succeeded`;
  - it is lower: `failed` (a late arrival of this number would be stale against any later
    press);
  - no answer: the row stays `uncertain`, and the user has **Check again** or **Do it anyway**.

  **An uncertain write is never retried automatically** (ADR 0005 §7.4 holds unchanged).

What is sent is **the revision the user pressed for**. A later revision is not sent on its own:
the class page shows "the player has revision N" and offers to send the current one.

**Removal on class deletion** (**amends ADR 0005 §1**: `jobs.class_id` becomes nullable, with
`CHECK (class_id IS NOT NULL OR kind = 'player_remove')`):
- If the class has a publication row that is not removed and the connection is `connected`,
  the class-delete transaction does three things:
  - takes `last_number + 1`;
  - sets `removed_at`;
  - enqueues a `player_remove` job with `class_id NULL` and input `{source_class_id, number}`.
- The job runs the same external-operation protocol with `removed: true` and no document. Its
  revalidation is that the publication row is still removed with that number, and that the
  connection is connected under the hold.
- A send job in flight for the deleted class cascades away (ADR 0007 §5.2). A late arrival of
  its lower number is stale on the player.
- While disconnected, a class deletion sends nothing, the publication row is deleted, and the
  delete dialog says the player's copy stays.
- Deleting the whole account sends no removals. The jobs and publications cascade, and
  `account-deleted` (§2) removes everything on the player.

No model output can create any of these jobs.

### 6. UI

- The connections page gains "classbeat-player" (connect, status, reconnect, disconnect, and the
  "may still be listed" note).
- The class page gains "Send to player" beside "Save to MOWL", with its state: not sent, sent
  (revision N), sending, uncertain (Check again, Do it anyway), changed on the player, player
  ahead, failed, reconnect.
- The class-delete dialog says whether a removal will be sent.
- The account page shows the player's consent, with a withdraw button.
- Every new string goes through i18n (ADR 0023).

## Alternatives

- **Give the player classbeat's session cookie, or share the database.** They are different
  origins and products, and plan section 1 of the player forbids it.
- **A general OIDC provider with dynamic clients.** More surface for one client. Not needed.
- **Public subjects (`users.id`).** That would put classbeat's internal id in the player's URLs
  and logs. Pairwise costs one HMAC.
- **Owned tables with `SECURITY DEFINER` functions for codes and the outbox.** That needs a new
  `BYPASSRLS` role. System tables behind `classbeat.auth` are the existing pattern for
  pre-user rows.
- **Events as ADR 0005 jobs.** Jobs are owned and cascade with the user, and their backoff caps
  at 15 minutes. `account-deleted` must outlive the user and last 7 days.
- **A literal player host in code.** It is safest, but the host is not known yet and changes per
  deploy. A validated single-host setting on a dedicated client is close to it in risk.
- **Retry uncertain sends with the same number.** The player is idempotent on number plus hash,
  but ADR 0005 §7.4 has no exception today and the reconciler is cheap. Kept as a possible later
  amendment.
- **Send every revision automatically.** That would write to another product without the user's
  press, against roadmap §2.
- **Convert on the player.** The player would have to understand classbeat's model. The format
  is the contract, so classbeat produces it.

## Consequences

- **Amendments to accepted ADRs:**
  - **ADR 0003 §4:** `/oidc/token` is exempt from the Origin and CSRF checks.
  - **ADR 0003 §6:** connecting and disconnecting the player need recent authentication.
  - **ADR 0003 §10:** no query string or body on `/oidc/*` is logged, and the new
    `auth_events` kinds are added.
  - **ADR 0005 §1:** `jobs.class_id` may be null for `player_remove` only.
  - **ADR 0014:**
    - §1's "Retained by design" gains the pending `account_deleted` outbox row: pairwise
      `sub` and `jti`, at most 7 days, then 30 days if failed. The §4 scan's `RETAINED` is
      unchanged, because the row holds neither the user's id nor their address.
    - §2's `not_included` gains "pending events to classbeat-player".
    - §3's "No request goes to MOWL, Spotify or a model" becomes: and one signed
      `account-deleted` event goes to classbeat-player when the player ever issued a sign-in
      for the user.
  - **`egress.py`:** one validated single-host setting, the player's, on its own client.
  - **Roadmap §2:** the "user write" row gains "send to player" and the removal on class delete.
- **New tables:**
  - system: `oidc_requests`, `oidc_codes`, `oidc_grants` and `player_events`;
  - owned: `player_link_flows` and `player_publications`.

  `connections.provider` gains `player`, and `jobs.kind` gains `send_to_player` and
  `player_remove`. These come with migrations, RLS for the owned tables, `SYSTEM_TABLES`
  reasons, coverage entries, and export registry entries (`oidc_grants` columns in the auth
  export, `player_publications` as a Section, the rest in `NOT_EXPORTED`).
- **New secret files:**
  - the client secret's hash;
  - the pairwise key (never rotated);
  - the ES256 signing key or keys (rotated by the runbook).

  The new settings are the client id, the redirect address and `CLASSBEAT_PLAYER_BASE_URL`.
- **New CLI:** `admin player-subject` and `oidc keys`.
- **Security-critical (Opus Developer and Opus Reviewer):**
  - the provider (§1);
  - the events and their egress (§2);
  - the `player` connection (§3);
  - the send and removal jobs (§5).
- **Sonnet:** the converter (§4) and the UI (§6).
- **Tests** use a fake player backend and a fake classbeat client. Nothing touches the network
  (`tests/netguard.py`).
- **Cross-repository:** the player's ADR 0010 §8 must pin the two event URNs above, in a player
  docs PR before PC-2 merges.
- Connecting a real player, deploying it and registering the real client are the owner's
  decisions, like every live activation.
