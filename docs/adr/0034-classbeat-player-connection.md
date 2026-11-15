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
  - the OIDC code flow with PKCE;
  - the ID-token checks the player makes;
  - pairwise subjects;
  - the signed events `account-deleted` and `sessions-revoked`;
  - the source-link binding (`login_hint` and `account_subject`).
- [ADR 0002 §7](https://github.com/magpern/classbeat-player/blob/main/docs/adr/0002-accounts-sign-in-pairing-and-tokens.md):
  - the source link (PKCE, no secret, a `cbp_lc_` code and a `cbp_src_` token);
  - publications, with their numbers, repeats, conflicts and removal markers.
- [ADR 0003](https://github.com/magpern/classbeat-player/blob/main/docs/adr/0003-class-format.md)
  and `schema/class-format/v1`: the class format (1.1 adds beat patterns) and its content-hash
  rule, with the reference converter `schema/converter/reference_converter.py`.

Today classbeat is an OAuth client only (Google, GitHub, Microsoft). It holds encrypted
connections (ADR 0004), a session with a 10-minute re-authentication and TOTP step-up (ADR 0003
§6), and writes to MOWL through jobs and external operations (ADR 0007 §6–8).

## Decision

### 1. classbeat as an OpenID Connect provider (one client)

classbeat becomes a provider for **exactly one registered client**, the player. It is not a
general provider. There is no dynamic registration and no third-party clients.

- **The client** is settings plus secret files:
  - client id;
  - exact redirect address;
  - events address;
  - the client secret's hash (SHA-256 of a 32-byte random secret that only the player's operator
    holds in clear).
  The token endpoint compares hashes in constant time.
- **Endpoints**, all under classbeat's public origin:
  - `GET /oidc/authorize`;
  - `POST /oidc/token`;
  - `GET /oidc/jwks`;
  - `GET /.well-known/openid-configuration`.
  There is no user-info endpoint, no refresh tokens and no `offline_access`.
- **Authorize:**
  - It runs on classbeat's own cookie session; a signed-out user signs in first and comes back.
    `return_to` stays a relative path (ADR 0003 §4).
  - `client_id`, `redirect_uri` (exact match), `response_type=code`, `scope` containing `openid`,
    `state`, `nonce`, `code_challenge` and `code_challenge_method=S256` are required. A bad
    client or redirect shows an error page and never redirects.
  - `prompt=login` requires a re-authentication (ADR 0003 §6, with step-up for a user whose TOTP
    is confirmed) before the code is issued.
  - The first time per user, a consent page names the player and what it gets (a sign-in, the
    email). Consent is stored per user and can be withdrawn on the account page.
- **Codes:**
  - 32 random bytes, stored as SHA-256 in an owned table, valid at most 60 seconds, single use;
  - bound to client, redirect, PKCE challenge, nonce, user and the authentication time;
  - a second use of a code is refused.
- **The ID token** is a JWT with `alg` ES256, `typ` JWT and a `kid`. Its claims are:
  - `iss` (the public origin);
  - `aud` (the client id);
  - `azp`;
  - `sub`;
  - `iat` and `exp` (5 minutes);
  - `auth_time`;
  - `nonce`;
  - `email` and `email_verified`.
  The answer also holds an `access_token`, a random value that is stored nowhere and grants
  nothing.
- **Pairwise subject:** `sub` = base64url(HMAC-SHA256(pairwise key, client id ‖ `users.id`)).
  The pairwise key is a secret file. So no classbeat user id leaves classbeat.
- **Signing keys:**
  - ES256 (P-256) private keys come from a secret file, each with a `kid`.
  - The key set publishes the current key and, during a rotation, the next one.
  - The private key is never logged, never in a setting and never sent to a model.
  - Key rotation is a CLI command and a runbook entry.
- **Limits** (ADR 0003 §9): authorize starts, token calls and bad client secrets are counted per
  address and per client.
- **Security log** (ADR 0003, amendment of 2026-11-05):
  - player sign-in granted;
  - consent given and withdrawn;
  - code refused;
  - client authentication failed.

### 2. Signed events to the player

An outbox table, owned and read through `UserContext`, holds events for the player. The worker
delivers each one as an OpenID Connect back-channel logout token:
- the token is typed `logout+jwt`, signed like the ID token, and carries `iss`, `aud`, `iat`,
  `jti`, `sub` and an `events` claim, with no `nonce`;
- it is POSTed to the events address as `logout_token`.

The events are:
- `account-deleted`: written in the same transaction as an account deletion (ADR 0014), so the
  outbox row must outlive the user row. It is the one outbox kind kept after the user is gone,
  holding only `sub` and `jti`;
- `sessions-revoked`: written on "sign out everywhere", an account disabled by an admin, a
  password reset, and a TOTP removed or replaced.

Delivery follows the job rules of ADR 0005: leases and fencing, retry with backoff for up to
7 days, and a fresh signature per attempt with the same `jti`. A `2xx` answer ends it. After 7
days the event fails visibly in the admin area. The player's host joins the egress allowlist on
the worker (`egress.py`, a security-critical change).

### 3. The `player` connection

`connections.provider` gains `player`, under ADR 0004: one row per user, encrypted, with the
same status rules. The connection uses the player's source link (player ADR 0002 §7) and ADR
0010 §9a:
1. **Connect** needs a recent authentication (ADR 0003 §6, like the other connections). classbeat
   sends the browser to the player's `/link/source` with:
   - `source=classbeat`;
   - the registered `redirect_uri`;
   - `state`;
   - an S256 `code_challenge`;
   - `login_hint` = the user's pairwise subject.
2. The callback checks `state` and exchanges the `cbp_lc_` code at the player's
   `POST /api/source/token` with the verifier. It keeps the source token **only if the
   answer's `account_subject` equals the user's pairwise subject and the player's issuer**.
   Otherwise it ends the link at once with `DELETE /api/source/link` and shows "This player
   account belongs to someone else".
3. The `cbp_src_` token is the secret payload. `public` holds the player's link id. Disconnect
   calls `DELETE /api/source/link`, then deletes the row.

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
pinned to a player commit, under `backend/tests/player_format/`. Tests check that every document
is valid against the strict schema, that its hash equals the reference converter's, and that the
golden revisions convert as expected. Updating the pin is its own PR.

### 5. "Send to player"

A `send_to_player` job writes one publication through external operations (ADR 0007 §6–8):
- the class's player publication number is a counter per class, increased in the same
  transaction that writes the operation;
- the envelope is `source_class_id` (the class id), `publication_number`, `removed`,
  `content_hash` and `document`.

The player's answers map to outcomes:
- `200 applied` or `200 repeat`: done;
- `409 stale_publication`: classbeat moves its counter above the number the player returned
  and retries once;
- `409 publication_conflict`: an error category, retried with a new number;
- `401`: the connection needs reconnecting;
- a network error or `5xx`: uncertain, so retry the same number. A repeat is idempotent on the
  player.

What is sent is **the current revision** when the user presses "Send to player". A later
revision is not sent on its own; the class page shows "the player has revision N" and offers to
send the current one. Deleting a class in classbeat sends a removal (`removed: true`) if the
class was ever sent. A disconnected player connection sends nothing.

The approval rule of roadmap §2 applies: the user's press is the approval, bound to that
revision and its content hash. The backend authorizes it against the authenticated user, and no
model output can trigger it.

### 6. UI

- The connections page gains "classbeat-player" (connect, status, disconnect).
- The class page gains "Send to player" beside "Save to MOWL", with its state: not sent, sent
  (revision N), sending, failed, reconnect.
- The account page gains the player's consent (withdraw).
- Every new string goes through i18n (ADR 0023).

## Alternatives

- **Give the player classbeat's session cookie, or share the database.** Different origins and
  products, and plan section 1 of the player forbids it.
- **A general OIDC provider with dynamic clients.** More surface for one client. Not needed.
- **Public subjects (`users.id`).** That would put classbeat's internal id in the player's URLs
  and logs. Pairwise costs one HMAC.
- **Send every revision automatically.** That would be a write to another product without the
  user's press, against roadmap §2.
- **Convert on the player.** The player would have to understand classbeat's model. The format
  is the contract, so classbeat produces it.

## Consequences

- New owned tables:
  - OIDC codes;
  - consents;
  - the event outbox;
  - per-class publication counters.
  `connections.provider` gains `player`. These come with migrations, RLS and guard-list updates.
- New secret files:
  - the client secret's hash;
  - the pairwise key;
  - the ES256 signing key or keys;
  - the player's base address and the egress host.
  The runbooks gain rotation.
- **Security-critical (Opus Developer and Opus Reviewer):**
  - the provider (§1);
  - the events and their egress (§2);
  - the `player` connection (§3);
  - the send job (§5).
- **Sonnet:** the converter (§4) and the UI (§6).
- Tests use a fake player backend and a fake classbeat client. Nothing touches the network
  (`tests/netguard.py`).
- Connecting a real player, deploying it and registering the real client are the owner's
  decisions, like every live activation.
