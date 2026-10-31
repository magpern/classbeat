# ADR 0009: Spotify connection (BYOK), tokens and the Spotify API

Status: Proposed

## Context

M4 (roadmap §2, §3, §10, M4 row) lets a user connect **their own Spotify developer app** (bring your
own Client ID, BYOK), so classbeat can search tracks and manage private playlists in the user's
Spotify account. [ADR 0008](0008-song-picking.md) is the song-picking design built on this
connection (pool and final playlists, stages, jobs, approval). This ADR is the connection itself:
the wizard and OAuth flow, token storage and refresh, error categories, the pool consent, egress,
the Spotify API migration and what classbeat needs from a new `mowl-workouts` release. The owner
accepted the Spotify-terms risks for the invite-only beta in [ADR 0010](0010-live-activation-permissions.md);
nothing here is run live by an agent.

What exists and is relied on unchanged:

- `connections` (ADR 0004 §1): the `provider` check already allows `spotify`; `public jsonb` holds the
  Client ID and Spotify user id; the encrypted payload holds `{access_token, refresh_token,
  expires_at}`; `classbeat.connections` is the only module that decrypts and returns a redacting
  `Credentials`; library failures leave it as `ConnectionFailure(category)` with no chained exception
  (ADR 0004 §5). ADR 0004 §3 lists the requirements a Spotify refresh must meet; §6 below meets them.
- OAuth flow rows for sign-in (`oauth_flows`, ADR 0003 §5): state as SHA-256, the PKCE verifier kept
  as given, at most 10 minutes, deleted on use, the flow cookie. Recent authentication (10 minutes,
  `require_recent_auth`, ADR 0003 §6), CSRF, and the rate-limit mechanism (reserve first, 429 with
  `Retry-After`, ADR 0003 §9).
- `mowl-workouts` 0.5.0 (vendored): `spotify_web` has `create_playlist(creds, name, uris)` (private,
  non-collaborative, always new), `replace_playlist_items(creds, id, uris)` (exact id), `search_tracks`,
  `my_playlists`, and still reads `GET playlists/{id}/tracks`. Its `call()` sleeps up to 30 s on a 429
  (twice), its `_access_token()` refreshes lazily through `urllib` and passes the new tokens to
  `Credentials.on_spotify_token`, its `connect()` runs a local callback server (CLI only), its
  `SCOPES` include `playlist-modify-public`, and a `MowlError` text embeds up to 200 bytes of the
  response body.
- The worker's outbound allowlist is `api.mowl.com` and `api.anthropic.com` (`classbeat.egress`, ADR
  0006 §9); the sign-in providers' calls are web-only and call `refuse_in_worker`.
- Role settings: `statement_timeout` 30 s, `lock_timeout` 5 s, `idle_in_transaction_session_timeout`
  60 s (ADR 0002 §5).

Facts about the Spotify API this relies on, and how sure they are:

- **From secondary sources only, so assumptions to verify at the first live step** (ADR 0008 §11
  steps 1 to 3). They come from the ADR 0010 research and a search summary of the Feb 2026 changelog;
  the developer pages themselves could not be fetched from the design session, so none of this is
  checked: `/playlists/{id}/tracks` is renamed `/items`, the response field `tracks` becomes `items`
  and nested fields follow the pattern, playlist contents are returned only for playlists the user
  owns or collaborates on, `POST /me/playlists` replaces the user-id form, search `limit` is at most
  10, Development Mode allows 5 users per Client ID via an allow-list and the app owner needs Premium.
  The design relies on them as the most likely shape; the fakes encode them and a wrong one is an edit
  of the reader and the fake (§11).
- **Further assumptions, each marked where used and checked by the first live run** (§11 of ADR 0008): the
  playlist item object names the track `item` (a legacy `track` may remain); `/items` pages are at
  most 50; the 403 for a user missing from the allow-list or an app owner without Premium arrives at
  the first `GET /me`; the token endpoint answers a dead refresh token with HTTP 400 and the OAuth
  error code `invalid_grant`; a refresh may or may not return a new refresh token.

## Decision

### 1. Scopes and what is asked

Exactly `playlist-read-private playlist-modify-private`. Not `playlist-modify-public` (the library's
default includes it; classbeat makes nothing public), not `user-read-private` or `user-read-email`
(`GET /me` returns the account's `id` without them; classbeat then has no email, country or product
field to hold). The consent screen therefore shows only playlist access. No Client Secret is ever
requested, stored, logged or accepted (AGENTS.md): the form has no such field and a test posts one and
expects 422. The granted `scope` in the token answer must contain both scopes (a smaller grant is
`scope_mismatch`, nothing stored).

### 2. The wizard and its endpoints

The page (roadmap §3, §8) walks the user through: (1) open the Spotify Developer Dashboard and create
an app (one sentence: the app owner needs Spotify Premium; a Development Mode app has an allow-list of
5 users, so add your own Spotify account under *User Management*; endpoint and rate limits apply);
(2) copy classbeat's **fixed callback URL** shown with a copy button; (3) register it in the app's
*Redirect URIs*; (4) paste the **Client ID**; (5) press **Connect Spotify**; (6) consent to the
managed pool playlist (§9). The callback URL is `CLASSBEAT_PUBLIC_BASE_URL + /oauth/spotify/callback`
(locally `http://127.0.0.1:<port>/…`, the loopback form Spotify accepts; a `localhost` base URL is
refused at startup when song picking is on, because Spotify does not accept it). It is built from
settings, shown for confirmation only, and **never taken from request input**.

| Route | Does |
|---|---|
| `GET /api/connections/spotify` | status: `not_connected` / `connected` / `needs_reconnect`, `client_id` (the user's own public value), `last_error` category, the callback URL, pool consent state, `song_picking_enabled`. Never a token, a Spotify user id or a display name |
| `POST /api/connections/spotify/start` `{client_id, confirm_account_change?}` | recent auth, CSRF, rate limits (§4). Validates the Client ID, creates a flow, returns `{authorize_url}`; the page navigates to it |
| `GET /oauth/spotify/callback?code&state` or `?error&state` | the redirect target, outside `/api` as roadmap §3 writes it; **top-level GET** carrying the session cookie (`SameSite=Lax` sends it). It changes state only after §4's checks and always ends in a 302 to `/account/spotify?result=connected` or `?result=<category>` |
| `DELETE /api/connections/spotify` | Disconnect (§7) |
| `PUT` / `DELETE /api/connections/spotify/pool-consent` | give or revoke the standing consent (§9) |

Client ID validation: after trimming, `^[0-9a-fA-F]{32}$`, stored lower case. Anything else is 422
`invalid_client_id` with no echo. The ID is public data in the OAuth sense; it is still only ever
shown to its owner. `song_picking_enabled` is the setting `CLASSBEAT_SONG_PICKING` (default **off**:
Amendment 1 has the owner switch live features on); with it off every route above answers 404
`feature_off`.

### 3. The flow row (`spotify_oauth_flows`)

Owned table (ADR 0002 §2), one migration with ADR 0008's tables (issue SP1). Columns: `id`,
`owner_id`, `state_hash bytea` (SHA-256 of the 32-byte `state`; `UNIQUE`), `session_hash bytea`
(SHA-256 of the session id that started it), `client_id text` (32 hex), `redirect_uri text` (the exact
string sent), `verifier text` (43 characters from 32 random bytes, S256), `confirm_account_change
boolean`, `created_at`, `expires_at` (now + `CLASSBEAT_SPOTIFY_FLOW_TTL_SECONDS`, default 600),
`used_at NULL`. The verifier is kept as given like ADR 0003's, for the same reason and with the same
limits: useless without the one-time `code` that only the user's browser receives, alive at most 10
minutes, and deleted when used or expired (the hourly purge of `classbeat.auth` also removes expired
Spotify flows). At most 3 unexpired flows per user (a fourth start deletes the oldest).

### 4. Starting, and the callback's rules

**Start.** Recent authentication (step-up for users with TOTP, ADR 0003 §6), CSRF, then rate limits
(reserve first): 10 starts per user and hour, 30 per IP and hour; a completed connection releases its
slot. `authorize_url` is `https://accounts.spotify.com/authorize` with `client_id`, `response_type=code`,
`redirect_uri` (the fixed one), `scope` (§1), `state`, `code_challenge_method=S256`, `code_challenge`,
`show_dialog=true` (so a user logged into another Spotify account sees the account chooser). If the user
already has a connection whose Client ID differs, nothing is deleted at start; §7 does it at the
callback.

**Callback.** One transaction takes the flow atomically and **single-use**:

```sql
UPDATE spotify_oauth_flows SET used_at = now()
 WHERE state_hash = :h AND owner_id = :u AND session_hash = :s AND used_at IS NULL AND expires_at > now()
RETURNING client_id, redirect_uri, verifier, confirm_account_change
```

On a mismatch the callback changes nothing and **deletes only the caller's own flow**: the row whose
`state_hash`, `owner_id` and `session_hash` all match the request (`DELETE … WHERE state_hash = :h
AND owner_id = :u AND session_hash = :s`), which exists only when that own flow is expired or used,
or when the request's own host or path is not the fixed callback. A `state` that is unknown, or that
names **another user's or another session's** flow, deletes nothing: that flow is not the caller's
and stays valid for its owner. **The `redirect_uri` sent to the token endpoint is the stored one,
re-checked equal to the setting**, never anything from the request. `?error=access_denied` (the user
pressed Cancel) or any `error` ends with `result=access_denied`, deletes the caller's own flow the same
way, and makes no further call. All these mismatches give the same answer (no distinction leaks); the
mix-up test in §13 runs both users concurrently and asserts that the other user's flow still
completes. The mix-up risk the roadmap names (a callback
connecting a different account or app) is closed by three bindings: user and session (who may
finish), Client ID (the token request names the stored one), redirect URI (stored, fixed).

After the update: token exchange (§6, `accounts.spotify.com/api/token`, `grant_type=authorization_code`,
`code`, stored `redirect_uri`, stored `client_id`, `code_verifier`; **no `client_secret` parameter,
ever**), then verification.

### 5. Verification and storage

1. `GET https://api.spotify.com/v1/me` with the new access token. The answer's `id` (string, 1 to 100
   printable characters) is the **Spotify user id**. A 403 here is `not_allowlisted` (§8); a 401 is
   `invalid_grant`; any other failure is its category. Nothing is stored on a failure; the tokens are
   dropped (Spotify has no app-side revoke call, the page says where to remove the app's access:
   spotify.com/account/apps).
2. If the user has a connection already (one rule, §7's table): same Spotify user id and **same
   Client ID** (a reconnect) → the row is rewritten in place, `generation` + 1, pool consent and rows
   kept; same Spotify user id and a **new Client ID** → the row is rewritten in place, `generation` + 1,
   and `forget_pool` (ADR 0008 §2) deletes the pool row and the pool operation rows in the same
   transaction, so the consent is asked again; the final `class_playlists` rows stay attached (same
   account, same playlists). A **different** Spotify user id needs the flow's
   `confirm_account_change`, otherwise the callback ends `account_changed` and stores nothing; with it,
   `forget_account` (§7) runs in the same transaction.
3. Storage uses `classbeat.connections` only (ADR 0004 §4's write path: `SELECT … FOR UPDATE`, new DEK,
   `UPDATE` or `INSERT`). `public = {"client_id": "...", "spotify_user_id": "..."}`; the payload
   `{access_token, refresh_token, expires_at}` (epoch seconds, the token answer's `expires_in` minus 60
   s); The column `expires_at` stays `NULL`. A token answer without a refresh token is
   `scope_mismatch`-like: `invalid_response`, nothing stored.
4. **`connections.generation bigint NOT NULL DEFAULT 1`** (issue SP1). ADR 0004 guards every status
   flip with `wrapped_dek`, which changes on every write. A Spotify refresh also rewrites the row (a
   new DEK for the new tokens), which would make every running job's `hold` and every conditional flip
   fail after each refresh. So for **every provider** the guard becomes `generation`: it is increased
   only by a **connect, reconnect or Client ID change** (a user's act), never by a refresh or a rewrap,
   and `hold`, `mark_needs_reconnect` and `record_result` compare it. For MOWL nothing observable changes
   (its row is written only by connect and rewrap; rewrap leaves `generation` alone, so a rewrap no
   longer invalidates a running job's hold, which is an improvement). The `OpenConnection` carries the
   generation it read.

### 6. Using the tokens, and refreshing

**`classbeat.spotify.access.spotify_call(factory, keyring, ctx, fn)`** is the only way to call the
Spotify API with a user's token. Per call:

1. Read the row (`open_connection`). If `expires_at - now < CLASSBEAT_SPOTIFY_REFRESH_MARGIN_SECONDS`
   (default 120): refresh (below), then re-open.
2. Build `Credentials` with the Client ID and the **access token only**: `spotify_refresh_token` is
   empty and the library option `spotify_refresh_enabled=False` (§12), so the library **never**
   contacts the token endpoint and **never** persists anything. ADR 0004 §3's "the library's
   synchronous callback reaches the database only through that path" therefore holds because there is
   no callback. A token that expires inside a call (clock skew) surfaces as 401 → one refresh and one
   repeat of the **read** (writes are never repeated: their recovery is ADR 0008 §2).
3. Run `fn` through `call_opened` in a thread (`run_library_call`, categories of §8).

**Refresh** (the requirements of ADR 0004 §3, one by one):

- *One at a time per user, the lock covering the refresh itself:* a short transaction `SELECT … FOR
  UPDATE` on the connection row; the token request runs **inside it** with a total deadline of
  `CLASSBEAT_SPOTIFY_REFRESH_TIMEOUT_SECONDS` (default 10, below the 60 s idle-in-transaction limit and
  inside the 30 s statement limit; the egress watchdog enforces the total deadline, ADR 0006 §9). A
  second caller waits on the row lock for at most `lock_timeout` (5 s), then reads again; if it still
  times out it fails retryably (`upstream_unavailable`) instead of refreshing in parallel.
- *After the lock, re-read and reuse:* if `expires_at` is now beyond the margin another caller has
  refreshed; use it, no request.
- *Rotation is persisted before the lock is released:* the new `access_token`, the new
  `refresh_token` when the answer has one (else the old one stays), and `expires_at` are encrypted
  (new DEK, same `id`, the same `generation`) and written **in that transaction**; a failed write fails
  the call and the lock is released by the rollback. A refresh token Spotify rotated but we failed to
  store is lost, and the next refresh then answers `invalid_grant` → reconnect; the page already says a
  reconnect can happen at any time.
- *Conditional on the version read:* the `UPDATE` is `WHERE id = :id AND generation = :g` (a reconnect
  or Client ID change in between wins; zero rows is `not_connected`).
- *Failure:* HTTP 400 with error code `invalid_grant` in the JSON body → `invalid_grant`: status
  `needs_reconnect`, `last_error = 'invalid_grant'`, tokens kept (nothing to gain by deleting; a
  reconnect overwrites them). `invalid_client` (wrong or deleted app) → `app_misconfigured`, also
  `needs_reconnect`. 5xx, timeout, connection error → `upstream_unavailable`; 429 → `rate_limited`
  with `Retry-After`. The error **code** is read from the token endpoint's JSON `error` field, a short
  enumerated OAuth code; classbeat's own token client (not the library) reads it, so no category comes
  from message text (ADR 0004 §5 stands). The body is parsed and discarded: never logged, never in an
  exception.

> **Amendment, 2026-10-31 (Architect; issue #198 item 2):** the token endpoint's app-setup codes are
> `invalid_client`, `invalid_scope`, `unauthorized_client` and `unsupported_response_type` (plus
> `invalid_request`, which §8 already lists). Each is `app_misconfigured` and flips the connection to
> `needs_reconnect`, like `invalid_grant`; the flip rule below covers all of them. A token-endpoint 400 or
> 401 with **no recognised error code** (no JSON, no `error`, or a code not listed) is
> `upstream_unavailable`: it is retried and does not flip the connection
> (`backend/src/classbeat/spotify/errors.py`, `APP_ERROR_CODES` and `by_oauth_code`).

**Status-flip rules for Spotify** (they narrow ADR 0004 §4 and the generic "a 401 or 403 (the status
flips)" of ADR 0005 §4's category table for this provider). Because a refresh does not change `generation`, `generation`
alone cannot tell a stale 401 from a current one, so a flip to `needs_reconnect` happens **only** in
these two cases:

- *The token endpoint* answers `invalid_grant` or `invalid_client` to a refresh. The flip is written in
  the refresh transaction itself, under the row lock and after the re-read, conditional on `id` and
  `generation`; it therefore always concerns the refresh token the row holds now.
- *A 401 on an API call* whose access token is still the row's current one. A 401 first forces one
  refresh (under the lock; if the re-read shows another caller already refreshed, no request), then
  the read is repeated once. Only a 401 on that repeat flips, conditional on `id`, `generation` **and**
  the `wrapped_dek` read when the repeat opened the row: any refresh, rewrap or reconnect after that
  read changes it, and the flip writes zero rows. A 401 sent with a pre-refresh access token therefore
  never flips a freshly refreshed connection; it only causes the (reused) refresh and the repeat. A
  **write** is never repeated (§6 step 2): its 401 is a refusal (`failed`, nothing written), forces the
  refresh, does not flip, and the job's next attempt (ADR 0005 §4) writes with the new token.

`forbidden`, `not_found`, `not_allowlisted` (connect only, nothing is stored), the playlist 403s and
every ownership refusal of ADR 0008 §2 **never** flip the status; they fail the job or the call with
their category and leave the connection `connected`. A flip writes `status`, `last_error` and
`updated_at` only, never `secret`, `wrapped_dek`, `key_id` or `generation`.

The token endpoint is called by `classbeat.spotify.http` only (a guard test), from web (connect) and
worker (refresh) alike, through `classbeat.egress` (§10).

### 7. Disconnect, a changed Client ID and a changed account

`classbeat.spotify.forget_account(db, ctx)` is one transaction, called by Disconnect and an account
change. It **deletes** the user's `spotify_pool` row, all their `spotify_create_playlist` and
`spotify_replace_items` operations (pool and final, any state; an undecided one is gone, as an
undecided MOWL attach is, ADR 0006 §3; this includes what `forget_pool` deletes), and the connection's
tokens; it **detaches** (`detached_at = now()`) every `class_playlists` row (history, not reused; ADR
0008 §2). `forget_pool` (ADR 0008 §2) is its narrower part, run alone in the transaction of a consent
revoke (§9), a `pool_missing` and a Client ID change. Neither touches MOWL links (they belong to the
MOWL account) or any playlist in Spotify.

| Action | Effect |
|---|---|
| **Disconnect** (`DELETE`, recent auth, CSRF) | `forget_account` and the row deleted. "Revokes locally": Spotify has no app-side revocation, so the page says classbeat stopped using the tokens and where to remove the app's access in Spotify |
| **Client ID change** (a start with another Client ID than the row's) | nothing changes at the start, and **the old tokens stay in use until a new flow succeeds**. At the callback, once the new tokens are verified for the **same** Spotify user: the old tokens are replaced (generation +1) and `forget_pool` runs in the same transaction, so the user is forced through the rest of the wizard (the pool consent is asked again); class playlists stay attached. A different user id needs `confirm_account_change` and runs `forget_account`. A failed or abandoned flow leaves the old connection untouched; the page shows it as "reconnecting" |
| **Reconnect** (same Client ID, same account, e.g. after `needs_reconnect`) | rewritten in place (generation +1); pool consent and rows kept |
| **Account deletion**, `keys wipe` | the row cascades or is deleted; `spotify_pool` and the rest cascade (ADR 0004 §2, ADR 0003 §6) |

A job in flight when the generation changes ends `not_connected` at its next guard and writes nothing
more (ADR 0008 §2); the call already sent went to the account whose token it opened.

### 8. Errors in plain words

`classbeat.spotify.errors.classify` maps an exception to a category from its type and its `__cause__`
(HTTP status, and for the token endpoint the OAuth `error` code of §6), most specific first, never from
message text. It extends ADR 0004 §5's table (the new codes join the `connections.last_error` check and
the three category checks of ADR 0007 §1):

| Category | From | Retry | What the page says |
|---|---|---|---|
| `invalid_grant` | token endpoint 400 `invalid_grant`; or 401 on the repeat of a read after one refresh (the only two that flip the status, §6) | no (reconnect) | "Spotify no longer accepts the connection. Reconnect Spotify." |
| `app_misconfigured` | token endpoint `invalid_client` / `invalid_request`; `redirect_uri` mismatch reported by Spotify as `error` on the callback | no | "Spotify does not recognise this app setup. Check the Client ID and the redirect URI in your Spotify app." |
| `not_allowlisted` | 403 on the first `GET /me` of a connect | no | "Spotify refused this account. In your Spotify app's *User Management* add this account's name and email (Development Mode allows 5 people), and make sure the app's owner has Spotify Premium." |
| `forbidden` | any other 403 | no | "Spotify refused this request for your account or playlist." (the ownership checks of ADR 0008 §2 run first, so this is rare) |
| `rate_limited` | 429 with `Retry-After` ≤ 3600 s | yes, waits at least the hint | "Spotify asks to slow down; trying again in about N minutes." |
| `quota_exhausted` | 429 with `Retry-After` > 3600 s, or 3 `rate_limited` retries in a row on one job | no | "Your Spotify app has used up its allowance for now. Try again in about N hours." |
| `upstream_unavailable` | 5xx, timeout, connection error, DNS | yes | "Spotify is not answering." |
| `not_found` | 404 on a playlist classbeat addressed by id | no | the caller words it (ADR 0008 §2: "the working playlist is gone") |
| `scope_mismatch`, `invalid_response` | §1, §5 | no | "Spotify did not give the access classbeat needs. Connect again and allow it." |
| `account_changed` | §5 | no | "This is a different Spotify account than before. Confirm to switch (classbeat forgets the working playlist)." |
| `access_denied` | the user pressed Cancel on Spotify's page | no | "Spotify was not connected." |

Only `invalid_grant` and `app_misconfigured` (from the token endpoint) flip the connection to
`needs_reconnect`, by the rules of §6; every other category leaves the status alone. `last_error`
stores the category; a provider message is never stored or shown. The admin area shows the
last category only (roadmap, "Admin area"). **Quota:** besides what Spotify answers, classbeat caps its
own use: `CLASSBEAT_SPOTIFY_CALLS_PER_JOB` (default 300 requests per job, counted in the fenced usage
note of the stage; over it the job fails `limit_exceeded`) and the library never sleeps on a 429
(§12), so a worker thread is not parked for a minute and the job's retry/backoff (ADR 0005 §4) honours
`Retry-After` instead. `rate_limited` retries use the existing attempts and the 1-hour cap.

### 9. The standing consent for the managed pool playlist (roadmap §2)

Consent is a row, not a flag on the connection: `spotify_pool` (ADR 0008 §2) with `consented_at` and
`consent_version` (the wording's version). `PUT /api/connections/spotify/pool-consent` (recent auth,
CSRF, needs `connected`) inserts it with `playlist_id NULL` and the connection's Spotify user id. The
wizard's last step shows the sentence: "classbeat manages one private working playlist in your Spotify
account, named *classbeat working copy*, to ask for song candidates and have MOWL analyse them. It is
overwritten on each pick; anyone with its link could open it." (the wording T25 requires about
non-public playlists). `DELETE` revokes, in **one transaction**: the row is deleted and `forget_pool`
(ADR 0008 §2) deletes the user's pool operation rows, the succeeded create that holds the old id and
the replace rows whose hash covers it included. The playlist id is therefore forgotten and **never
touched again** (no row is left from which a later pick could reuse it; the page tells the user the
playlist stays in their Spotify and how to delete it), and song picking needs consent again (a new
pool playlist is created on the next pick, under that job's own create key and a new `ref`). Every write to the
pool checks the row exists at the operation's decide step (ADR 0008 §2); a pick job without consent
fails `consent_missing` before any call. Consent is logged as `job_events`-free audit: an
`auth_events` row `spotify_pool_consent` / `spotify_pool_revoked` (ids only), through `classbeat.auth`
(guard 8).

### 10. Egress, URLs and SSRF

- **Hosts.** The worker allowlist gains `api.spotify.com` and `accounts.spotify.com` (https, 443, every
  redirect hop, as ADR 0006 §9). The web process gets its own allowlist of the same two hosts for
  `classbeat.spotify.http` (the token exchange and `GET /me` at connect); the sign-in providers keep
  theirs. The E2E fakes answer under these names inside the compose network.
- **Only `classbeat.egress` builds clients** (ADR 0006 §9, guard test): `classbeat.spotify.http` uses its
  `httpx` factory with `trust_env=False`; the library keeps using the worker's process-wide `urllib`
  opener. No new HTTP client construction elsewhere; the guard test is extended, not relaxed.
- **No URL from Spotify is ever fetched by classbeat's servers.** Pagination uses `offset` and `total`, never the `next` URL;
  `href`, `external_urls`, `images`, `preview_url` and `uri` values from responses are not requested,
  stored or shown (URIs are rebuilt from validated 22-character ids: `^[0-9A-Za-z]{22}$`). *Amended by
  ADR 0013 §9 (2026-10-30), one exception: the album image URL (`image`) on M-1 and M-5 track
  entries.*
  - It is never requested by the server.
  - It is stored only as `cover` in the `song_list` content, after
    `re.fullmatch(r"https://i\.scdn\.co/image/[0-9a-f]{40}", url)`, checked again before it is
    returned, and otherwise becomes `null`.
  - It is never taken from model output and never goes to the model.
  - Only the browser loads it, through `CoverArt`, with no referrer, behind the CSP's `img-src`.
  - Every other URL stays unrequested, unstored and unshown.

  A fake that
  answers a `next` pointing at an unlisted host must produce no request to it (and the allowlist would
  refuse it anyway). Every id from a response is validated before it is used in a path.
- **TLS** contexts never write a key log (`egress.tls_client_context`); the token endpoint's request and
  response bodies are not logged by any layer; the redaction list of ADR 0004 §7 gains `code`,
  `code_verifier` and `state`, and a caplog test plants each.

### 11. The Spotify API migration (roadmap §10)

Every Spotify call classbeat makes uses the current shape; nothing calls `/tracks`:

| Purpose | Call |
|---|---|
| verify | `GET /me` |
| search | `GET /search?type=track&q=…&limit=10` (the maximum; default market, none sent) |
| owner check, privacy | `GET /playlists/{id}?fields=id,name,description,public,collaborative,snapshot_id,owner(id),items(total)` |
| create | `POST /me/playlists` `{name, description, public: false, collaborative: false}` |
| write | `PUT /playlists/{id}/items` (first 100 URIs, an empty list clears) and `POST /playlists/{id}/items` (the rest, 100 each) |
| read back | `GET /playlists/{id}/items?limit=50&offset=…` |
| listing (reconcile only) | `GET /me/playlists?limit=50&offset=…`, at most 20 pages |

**Pagination** is by `offset` until `offset >= total` or an empty page, never by `next`, with a hard
cap of 1000 items and 20 listing pages (over the cap is a failure, not a truncation). **Reading
items** accepts the entry's `item` object and, if absent, the legacy `track`; any entry that is
`null`, not `type == "track"`, `is_local`, or lacks a valid id is returned as an **unplayable marker** and
makes a verification fail (a playlist classbeat wrote contains exactly the tracks it wrote). Tests run
the paginated reader against fake pages of 50 with 120 items in both shapes and assert the exact ids
and order. Playlist contents are readable only for playlists the user owns or collaborates on, which
is all classbeat reads: it never reads a followed playlist of someone else (the existing "use my own
Spotify playlist" path reads through MOWL, not Spotify). Unknowns are assumptions, checked at the first
live run (ADR 0008 §11); the fakes encode the shapes above and a change is an edit of the reader and the
fake, not of any flow.

### 12. What classbeat needs from `mowl-workouts` (v0.6.0)

The library lives in the private repo `magpern/mowl-workouts`; the design session cannot reach it. The
**local Architect session** files the issues there from this list, does them under that repo's
`CLAUDE.md` rules (CLI, MCP server and plugin keep working; its name-based `save_playlist` stays for
them), and releases `v0.6.0`. Each item is acceptance-tested in that repo and by the classbeat issue L1.

| # | Item | Why |
|---|---|---|
| M-1 | `spotify_web.playlist_items(creds, playlist_id) -> list`: `GET /playlists/{id}/items`, `limit` 50, offset pagination by `total`, never `next`; each entry `{id, uri, name, artists, album, release_date, duration_ms, type, is_local}` from `item` (else `track`), plus `image` from 0.7.0 (ADR 0013 §9); non-tracks/null/local entries returned as markers; `playlist_tracks` becomes an alias of it; `find_playlist_tracks` stays for the CLI only | §11 |
| M-2 | `create_playlist(creds, name, uris=(), description="") -> id`: `uris` may be empty (create only), `public: false` and `collaborative: false` fixed, no `public` parameter; `add_items(creds, id, uris)`; `replace_playlist_items(creds, id, uris) -> snapshot_id` (PUT the first 100, an empty list clears, POST the rest, returns the last `snapshot_id`; the id is validated) | create, then fill, as two requests so the id exists between them (ADR 0008 §2) |
| M-3 | `get_playlist(creds, id) -> {id, name, description, public, collaborative, snapshot_id, owner_id, total}`; `me(creds) -> {id}`; `my_playlists` returns `{id, name, description, owner_id, public, collaborative}` | owner check, reconcile |
| M-4 | `Credentials` gains `spotify_refresh_enabled: bool = True` (False: never call the token endpoint, never call `on_spotify_token`; an expired token raises `MowlError` with no HTTP cause) and `spotify_max_retry_wait: float = 30.0` (0: never sleep, raise the 429 `MowlError` with its `HTTPError` cause so `Retry-After` is readable); `SCOPES` constant split: `SCOPES_PRIVATE = "playlist-read-private playlist-modify-private"` for hosted use | §6, §8 |
| M-5 | `search_tracks(creds, query, limit=10, market=None)` returns the structured fields `_track` already builds plus `duration_ms` and `release_date` (and `image` from 0.7.0, ADR 0013 §9); `spotify.track_uri(id)` validating the id; `unknown_bpm` etc. unchanged | pool |
| M-6 | Nice to have, not blocking: `MowlError` gains `status` and `retry_after` attributes (ADR 0004's note) so classification no longer depends on `__cause__` | |

Items M-1 to M-5 are required; the classbeat tests use the real library against the fake Spotify, so
none of the classbeat issues that call Spotify can merge before the wheel is pinned. **Pinning:**
`scripts/build-mowl-wheel.sh` (needs `gh` access to the private repo, which the local session has)
is run against the `v0.6.0` tag by the local Architect: it checks the tag's commit, that the bundled
Swedebeat key file is absent and that every other file and the `Requires-Dist` lines match the release
wheel; the result is `backend/vendor/mowl_workouts-0.6.0-py3-none-any.whl`, `uv lock` is refreshed, and
the header constants of the script (tag commit, release asset digest, wheel SHA-256) are updated, all in
classbeat issue L1's PR. The old 0.5.0 wheel is removed in the same PR. Whether MOWL re-reads a
Spotify playlist whose content changed is a MOWL fact the library cannot give (ADR 0008 §2, §11).

### 13. The fake Spotify and tests

**`tests/spotifyfake.py`** (in-process, installed as a urllib handler like `mowlfake.py`, plus an
`httpx` transport for `classbeat.spotify.http`) and **`e2e/fake-spotify/server.py`** (answers as
`api.spotify.com` and `accounts.spotify.com` inside the compose network, throwaway CA trusted like the
fake MOWL's). Both implement: the authorize page (a test GET that "approves" and returns the redirect),
the token endpoint (PKCE verification, `client_id` per app, rotating refresh tokens, `expires_in`), `/me`,
search over an invented catalogue (10 per page), `POST /me/playlists`, `GET /playlists/{id}`,
`PUT`/`POST`/`GET /playlists/{id}/items` in the new shape (and a switch for the legacy `track`),
`/me/playlists`, per-app state keyed by Client ID (users, allow-list, tokens, playlists), and **failure
injection** by request number or path as the fake MOWL has: refuse before send, drop after send
(effect applied), answer without an id, 400 `invalid_grant`, 400 `invalid_client`, 401, 403 (allow-list
and other), 404, 429 with `Retry-After` (short and over an hour), 5xx, a trickling response, a `next`
URL to an unlisted host, an item that is local, an episode or null, duplicates, a partial `PUT`. It
records which `Authorization` and `client_id` each request carried, so tests assert that nothing
crosses users.

Tests, each named in an issue's criteria:

- **Flow**: a happy path; every mismatch of §4 (other user, other session, expired, used, unknown
  state, tampered `redirect_uri`, callback from another user's flow while theirs is still valid, a
  Client ID swapped between start and callback); no Client Secret accepted anywhere; the verifier and
  `code` never in logs, events or errors; state single-use under two concurrent callbacks.
- **Tokens**: refresh before expiry; two concurrent refreshes make one request and keep the rotated
  refresh token; a failed write after a rotation fails the call; `invalid_grant` flips the status once
  and a stale call from before a reconnect does not (generation); a 401 sent with a pre-refresh access
  token, answered after another caller refreshed, does not flip the refreshed connection (`wrapped_dek`
  changed); `forbidden`, `not_found` and the playlist 403s never flip; a refresh does not invalidate a
  running job's `hold`; `pg_dump` has ciphertext only; `repr` redaction; the library never reaches the token
  endpoint (fake records zero token requests from a library call).
- **Every category** of §8 from the fake, on connect and on use, with the sentence the API returns.
- **Account/app changes**: Client ID change, other Spotify account with and without confirmation,
  disconnect, consent revoke: tokens, pool, operations and detach exactly as §5 step 2, §7 and §9,
  nothing else touched; after a revoke and a new consent the next pick sends no request to the old
  pool id. A wrong-user or wrong-session callback leaves the other flow usable.
- **Isolation**: two users on **different Client IDs** with their own tokens, tracks, pool and
  playlists, concurrently: every fake request carries the right user's token and Client ID; 404 on the
  other's ids; a model-chosen or browser-sent playlist id is never used (ADR 0008).
- **Egress**: a host off the list, a redirect off it, and a `next` URL are refused; the guard test sees no
  new client construction; the web process cannot call the worker-only paths and vice versa.

### 14. Threat model

This ADR's rows in [the threat model](../security/threat-model.md) are T66, T67, T71 to T73, T77 and
T78: callback mix-up and code injection (T66, extends T14), token theft and refresh (T67, T40/T41
extended), BYOK misconfiguration (T71), quota and rate-limit abuse (T72), SSRF through Spotify URLs
(T73), a stale connection used after a reconnect or account change (T77, §5, §6), and playlist privacy
drift (T78, shared with ADR 0008). ADR 0008 adds T68 to T70 and T74 to T76 for the playlists, the
stages and the approval.

## Alternatives

- **Authlib for the Spotify flow.** It is already in the web process for sign-in. Rejected: the flow here
  has no OIDC, the bindings of §4 are classbeat's own (user, session, Client ID, redirect), and a
  direct `httpx` token client through `classbeat.egress` keeps the allowlist and the error-code reading
  in one reviewable module.
- **Letting the library refresh** (with `on_spotify_token` writing to the database). Rejected: a
  synchronous callback inside a worker thread cannot hold the row lock or fail the call cleanly (ADR
  0004 §3), the library's error text embeds response bytes, and its 429 sleep parks a thread.
- **A Client Secret flow.** Forbidden (AGENTS.md); PKCE needs none.
- **`wrapped_dek` as the connection version for Spotify.** Rejected: a refresh rewrites it (§5).
- **Storing the verifier encrypted.** Rejected for now: same exposure as ADR 0003's sign-in flows, a
  10-minute life and no use without the code; revisited at M6 with the rest.
- **Scope `user-read-private` for `market=from_token` and a Premium check.** Rejected: more data and
  power for a convenience; MOWL's availability data already drops unplayable tracks (ADR 0008 §3).
- **Treating the pool consent as a connection flag.** Rejected: it has its own lifecycle and wording
  version, is revocable alone, and carries the playlist id.

## Consequences

- New: `classbeat.spotify` package, `connections.generation`, three owned tables (flows here; pool and
  class playlists in ADR 0008), two worker/web allowlist hosts, a feature flag, eight settings
  (`CLASSBEAT_SPOTIFY_*`, `CLASSBEAT_SONG_PICKING`), an `auth_events` pair.
- Everything Spotify-specific that the owner must do live (a dashboard app, the allow-list, the first
  real connect) is in ADR 0008 §11 and stays unrun until authorized.
- The vendored library is replaced by 0.6.0; until then no Spotify call is made by any test against
  the real library code (L1 gates the other issues).
- Every `connections` consumer that compared `wrapped_dek` now compares `generation` (SP1 changes
  `hold` and the flips; its tests cover MOWL unchanged).
- Threat model T66, T67, T71 to T73, T77, T78.
- ADR 0004 §4, ADR 0005 §3 and ADR 0007 §9 still describe the `wrapped_dek` guard, and ADR
  0005 §4's category table the generic 401/403 flip; on acceptance §5.4 and §6 supersede them, recorded
  under "ADR amendments owed" in `docs/status.md`.
