# ADR 0035: classbeat talks to the SwedeBeat gateway, not to SwedeBeat

Status: Accepted (owner direction 2026-11-15; issue #1075; roadmap Amendment 23; reviewed in #1076 and implemented on fakes in #1082, #1083, #1084 and #1085).

Supersedes: [ADR 0015](0015-swedebeat-connection.md) §4 (the app key) and §7 (outbound), the
`device_in_use` question of §3 and §5, §9's owner steps 1 to 3 (the `SWEDEBEAT_APP` secret) and its
"`expires_at` stays `NULL`". Also the SwedeBeat host and catalogue-client parts of
[ADR 0016](0016-swedebeat-music.md) §4 and §5. The rest of both ADRs stands: the flag, the
preference, the mixes through MOWL, the match revision, the replace, the hand-off, retention and
isolation.

Related: [ADR 0004](0004-connections-and-encryption.md) (connections),
[ADR 0006](0006-agent-runs.md) §9 (egress), [ADR 0010](0010-live-activation-permissions.md), the threat
model (T19, T23, T104 to T107, T110), the gateway contract `docs/api.md` of
`magpern/sb-gateway-service` (authoritative where this ADR quotes it).

## Context

The owner built a private service, the **SwedeBeat gateway** (`magpern/sb-gateway-service`). It sits
between classbeat and SwedeBeat and holds every SwedeBeat secret: the AES login key, the S3 key id
and its own Spotify app credentials. It runs on the owner's home server (docker compose, port 7355),
**the same host as classbeat's production stack**. It is bound to the LAN and reached as
**`http://192.168.1.222:7355`**: plain HTTP on a private address (owner decision, 2026-11-15). An
nginx name, `sb.classbeat.eu`, was discussed first. The owner chose the LAN address. Every request
except `/health` needs a client key in `X-API-Key`. The owner issued classbeat one and stored it as
the repository secret `SB_CLIENT_API_KEY`.

The owner's direction: classbeat must **no longer call SwedeBeat at all and must hold no SwedeBeat
secret**. Today it does both:

- **Connect** (ADR 0015) calls `mowl.swedebeat.login` with SwedeBeat's AES key. The key is baked
  into the release image from `SWEDEBEAT_APP`, or comes from a `swedebeat_app` secret file, in web
  only. Connect stores `{token, device_id}`.
- **Match** (ADR 0016 B1) sends one `cloud.swedebeat.se/track/extendedsearch` request per song with
  the user's token, from a worker-only client.
- **Plumbing** exists only because classbeat held the key: settings, the worker check, the
  Dockerfile mount, the release step, the key guard test, compose secrets, the e2e fake SwedeBeat,
  log redaction names, threat model rows and runbooks.

Gateway facts this ADR relies on (`docs/api.md`, and the source for the first two):

1. `POST /auth/login {email, password, device_id?}` answers
   `{bearer, token_type, expires_in, customer_id}`.
   - It **always sends SwedeBeat `force: true`**, so every login signs the account out of the
     SwedeBeat phone app.
   - It has no "device in use" answer and no refresh.
   - The bearer is an opaque sealed token that lives `SESSION_TTL_SECONDS` (default 24 h).
   - The request model ignores unknown fields (pydantic's default).
2. Order of checks: 413 → `X-API-Key` (`401 {"detail":"unknown client"}`) → bearer
   (`401 "missing, invalid or expired bearer; POST /auth/login"`) → schema 422 → … .
   - Errors are `{"detail": str}`, except the schema 422, whose `detail` is a list.
   - A login that SwedeBeat refuses is a `401` with SwedeBeat's own message relayed.
   - An **unreachable** SwedeBeat is a `502`. But on every route that needs a login, the gateway
     first asks SwedeBeat whether the token is still valid (`checktoken.php`; a positive answer is
     cached for 300 s).
     - **Any non-200 answer from that check becomes the bearer `401`** (`deps.py`
       `current_session`, `swedebeat/auth.py` `check_token`).
     - Inside a route, an upstream 403 becomes a 401 too (`swedebeat/client.py` `parse_json`).
     - So a failing SwedeBeat can look like an expired login.
     - Raised as magpern/sb-gateway-service#5; §5 handles it until then.
3. `POST /catalog/match {tracks: [{title, artist}] (at most 200), include_unsure}` answers `items[]`,
   each `{title, artist, status: found|unsure|missing, score, track: {reference, title, artist, bpm,
   duration} | null}`.
   - The gateway's own `found` cut is 0.78.
   - The call takes seconds; the contract asks for a client timeout of at least 120 s.
   - A track whose search fails comes back `missing`, so **a SwedeBeat outage looks like
     "all missing"**.
4. The gateway also offers playlist writes (create, `PATCH`, publish, merge-publish), off by default.
   SwedeBeat has no delete.

## Decision

### 1. One gateway origin, a setting with a strict policy

- **The setting.** `CLASSBEAT_SWEDEBEAT_GATEWAY_URL` (`Settings.swedebeat_gateway_url`) is the
  gateway's origin. Unset means SwedeBeat is unavailable on this server (§2's "available").
  - Production's intended value is `http://192.168.1.222:7355`. It goes in `compose.prod.yaml`'s
    example environment, `.env.example`/`prod.env.example` and the runbook, never in code.
- **It is a setting, not a literal.** The address belongs to the owner's deployment, like the
  player's host (ADR 0034 §2). That amends the rule in `classbeat.egress` that "the list is code",
  for this one origin.
- **The policy** is a pure module, `classbeat.swedebeat_gateway.url_policy`, modelled on
  `player/base_url_policy.py`. The value must be an origin: no userinfo, no path but `/`, no query
  and no fragment. Then **one** of these must hold:
  - **`http` to a loopback or private IP literal**: IPv4 `127.0.0.0/8`, `10.0.0.0/8`,
    `172.16.0.0/12` or `192.168.0.0/16`, or IPv6 `::1` or `fc00::/7`, with an explicit port.
    - A DNS name is never allowed over plain http, because it could resolve anywhere.
    - Link-local addresses (`169.254.0.0/16`, `fe80::/10`, the cloud metadata range) are refused.
    - So are `0.0.0.0`, multicast and broadcast.
  - **`https` to a public lowercase DNS name** (the player's name rules), on 443 or an explicit
    port. This is for a deployment where the gateway is on another machine behind TLS.

  Anything else is refused at start with `ValueError`. The message names the rule, never the value,
  and the process refuses to start (both web and worker validate it).
- **Plain HTTP on the LAN is the owner's accepted risk** (see Consequences).
  - The SwedeBeat password (at connect), the bearer and the client key travel unencrypted from the
    classbeat containers to the host's own address.
    - Traffic from a container to its own host's address stays inside the host's kernel and does
      not go on the LAN wire.
    - The real exposure is the open port: anything on the LAN can reach `192.168.1.222:7355`,
      guarded only by the client key.
  - The gateway's own compose binds to `127.0.0.1` by default and says to use HTTPS beyond
    localhost. Reaching it on the LAN address means the owner set `GATEWAY_BIND`, and accepts this
    for the LAN.
  - If a DHCP change hands `192.168.1.222` to another device, that device would receive passwords
    in clear. So the address must be reserved (§10).
  - The policy keeps plaintext off any public address, and off any DNS name that could be
    repointed.
  - Mitigations the owner may add: bind the gateway to the docker bridge instead of the LAN, or put
    TLS in front. Either is a change of the setting only.
- **Exact origin, no redirects.** The gateway client's list is exactly the configured `(scheme,
  host, port)`.
  - A request to any other scheme, host or port is refused before it is sent. Redirects are never
    followed: a 3xx comes back as a response, to be refused.
  - The client's transport therefore checks a full origin, not only a host: the existing
    `Allowlist` is https/443-only, so this client gets its own origin check in `classbeat.egress`.
  - For `http` no TLS is used. For `https` the system trust store is used.
- **This amends ADR 0006 §9 and threat model T19's "https on 443 only"** for this one configured
  origin. Every other client keeps the rule. SG-4 rewrites T19.
- **No port obscurity** (owner question, 2026-11-15). A non-standard port hides nothing from a
  scanner. The protection is the client key, the LAN binding and, if exposed later, TLS with an
  nginx address allowlist (§10).
- The list is on **no urllib list**: neither `WEB_URLLIB_HOSTS` nor `WORKER_HOSTS` holds it.
  - One dedicated client builder, `swedebeat_gateway_async_client(total_seconds)`, is used in **both
    web (login) and worker (match)**. It is the only client that reaches the gateway.
  - It is built like the catalogue client of ADR 0016 §4:
    - no redirect at all (`follow_redirects=False`; a hop to another host is refused before it is
      sent, and a same-host 3xx comes back to be refused);
    - `trust_env=False`, and the system trust store for `https`;
    - no default header: a request carries only what the call site sets (`Accept`, `Content-Type`,
      `X-API-Key`, and `Authorization` for the match);
    - a total deadline per request, body included;
    - the response body read raw, with any `Content-Encoding` other than identity refused;
    - a size cap per call (login 16 KB, match 1 MB).
  - It takes the deadline as its only argument. Unlike the other dedicated clients, which have
    one deadline, login (30 s) and match (150 s) need different ones; the builder clamps the value
    to 1 to 150 s.
    - The origin is installed once at process start from the validated setting
      (`egress.install_swedebeat_gateway_origin`, called by web and worker start-up), never per
      call. Until it is installed, the builder refuses.
    - A transport and an origin of the test's own come only from tests, through
      `swedebeat_gateway_test_client`, which the egress guard refuses inside `classbeat`.
  - The deadline is 30 s for login. For the match it is `SWEDEBEAT_GATEWAY_MATCH_SECONDS = 150`
    (the contract asks for at least 120).
- The web's login and the worker's match each build one client per call (`async with`). Neither the
  cookie jar nor the connection pool outlives that call.
- **No request goes to `www.swedebeat.se` or `cloud.swedebeat.se` any more.** Both constants and the
  `cloud` client are deleted (§8), and no list in classbeat names a SwedeBeat host.
- In e2e, the fake gateway runs on the compose network. The stack sets
  `CLASSBEAT_SWEDEBEAT_GATEWAY_URL` to the fake's https name with its throwaway CA, as the other
  fakes do (§9).
  - The backend tests cover the `http` private-IP origin (fake transport) and every refusal of the
    policy.
  - The netguard still blocks every non-loopback connect in tests.

### 2. The client key: a secret file in web and worker

- A new Docker secret **`swedebeat_gateway_key`**, read like the other secret files. It is
  `Settings.swedebeat_gateway_key: SecretStr | None`, from `/run/secrets/swedebeat_gateway_key` or
  `CLASSBEAT_SWEDEBEAT_GATEWAY_KEY_FILE`, **never the environment's value**. An empty file means no
  key.
  - A non-empty value must be 16 to 256 printable ASCII characters without whitespace. Otherwise
    the start is refused, and the message names the file, never the value.
  - It is mounted on **web and worker** and on nothing else: not on `db-functions`, maint, the
    second e2e web, or any helper. A guard test and the CI compose check pin this, replacing the
    `swedebeat_app` "web only" checks.
- The value is the key the gateway lists in its `CLIENT_API_KEYS` for classbeat.
  - The owner keeps it as the GitHub secret `SB_CLIENT_API_KEY` for their own record. **No workflow
    reads that secret**: CI and e2e use fakes, and the release image never holds the key.
  - On a server the owner writes the value into `secrets/swedebeat_gateway_key`.
    - `scripts/worktree.sh` creates the empty placeholder for development. The deploy runbook
      (`docs/runbooks/vps-deploy.md`, the image-only install of ADR 0020) and the prod compose
      check scripts (`scripts/check-prod-compose*.bash`) are updated **in SG-1**, which adds the
      mount.
    - `docs/runbooks/swedebeat-connect.md` says how to fill the file.
  - **A server must have the file, empty or filled, before it runs a release that carries SG-1**:
    compose refuses to start a service whose secret file is missing. That goes in the release
    notes and the owner steps.
- The key is sent only in `X-API-Key`, only to the configured gateway origin, and only by the client of §1.
  - It is never logged: the header and the setting are on the redaction lists, and httpx's request
    log stays off.
  - It is never put in a URL, a response, an event, a job, a revision or a model input.
  - The M6 secrecy tests search for it in every log, response and database dump of the fake flows.
- **"Available"** (the account page's `swedebeat.available`, ADR 0015 §1) now means the gateway URL
  is set **and** the key file holds a key.
  - The flag stays as it is: `CLASSBEAT_SWEDEBEAT_ENABLED`, the admin flag, stricter wins (ADR 0015
    §6), and the per-user preference (ADR 0015's amendment).
  - The SwedeBeat features run only when the flag, the preference, the URL and the key are all
    present.

### 3. Connect: `POST /auth/login`, store the bearer

`classbeat.connections.swedebeat_account.connect_swedebeat` keeps its shape (ADR 0015 §1). The route
is unchanged: recent authentication and step-up, CSRF, rate limits, events. The sign-in becomes:

1. **The URL and the key are checked first.** Either one missing: `SwedebeatRefusal("app_key_unavailable")` (503) before the
   rate limit is spent and before anything is sent.
2. **Login.** One `POST {gateway}/auth/login` with:
   - the body `{"email", "password", "device_id", "force": true}`;
   - the device id of ADR 0015 §2 (the user's HMAC device id, 16 hex characters, within the
     gateway's 64);
   - the headers `X-API-Key`, `Content-Type: application/json` and `Accept: application/json`.

   `force` is sent explicitly: today's gateway ignores it, and §4 explains why it is there.
3. **The answer.** It is read through a field allowlist:
   - `bearer`: a string of 16 to 4096 printable ASCII characters;
   - `expires_in`: an integer from 60 s to 400 days;
   - `customer_id` and `token_type` are not kept.

   Any other shape is `unknown`.
4. **Store.** `save(..., "swedebeat", {"bearer": bearer}, public={"email": email},
   expires_at=now + expires_in)`.
   - The payload fields of `swedebeat` become `{"bearer"}` (`PAYLOAD_FIELDS`).
   - **The bearer is never library credentials.** `build_credentials` refuses `swedebeat` as it
     refuses `player`, so `mowl-workouts` code can never hold or send it.
     - Only `classbeat.connections.swedebeat_account.open_bearer` opens it, through `UserContext`
       (as `player_account` opens its token). It returns the bearer with the row's id, versions
       and `expires_at`, and only the gateway client takes it.
     - This also closes the old path: the old match job reads `Credentials.swedebeat_token`, which
       no row can fill any more. It can never send a bearer to `cloud.swedebeat.se`. SG-2 replaces
       that job in the same PR anyway (§11).
   - The password is discarded, as today. It is never stored, logged, put in a URL or a response,
     and never chained on a failure.
   - The device id is not stored either (ADR 0015 §2: derived on each connect).
5. **Status from the answer, never from the relayed message** (no message is read, stored or
   logged):

   | Gateway answer | Connect result |
   |---|---|
   | `200` with a valid body | connected |
   | `401` whose `detail` is exactly `unknown client` | `SwedebeatRefusal("app_key_unavailable")`, 503. The operator log names the gateway and the category. Nothing is stored, the user is not asked again, and the rate-limit slot is released |
   | any other `401` | `invalid_credentials`, 400 |
   | `403` (the gateway's login never answers 403, so it is a proxy or deny in front of it: configuration) | `app_key_unavailable`, 503, as for `unknown client` |
   | `422` | `invalid_credentials`. The form already bounds the lengths to the gateway's (email 3 to 254, password 1 to 256), so a 422 is an email or password the gateway would not take |
   | `413`, another `4xx`, a non-JSON or oversized body, a redirect | `unknown`, 502 |
   | `429` | `rate_limited` with `Retry-After` (a proxy in front may limit; the gateway itself does not) |
   | `502`, `5xx`, a timeout, a refused connection | `upstream_unavailable`, 503 |

   `device_in_use` (409) is no longer produced.
6. **Legacy rows.** A stored `{token, device_id}` row from the direct-SwedeBeat era does not have the
   new fields. Opening it is `needs_reconnect`: the row flips to that state, no request is made, and
   the page shows the reconnect form.
   - No live SwedeBeat connect has been logged (status.md, "Live activation log"), so production
     should have none.
   - No migration: the categories exist and the payload is encrypted.

### 4. The sign-out consent, until the gateway can ask first

The gateway always takes the session from the phone, so ADR 0015 §3 (ask SwedeBeat with
`force=false`, and ask the user only when another device holds the session) cannot be kept. The
owner has not agreed to drop the consent, so classbeat keeps it and moves it to the start:

- The connect form (`SwedebeatDialog`) shows, above the button, a plain sentence: **connecting signs
  this Swedebeat account out of the Swedebeat app on other devices, such as your phone**. It adds a
  required checkbox ("I understand", unchecked on every open; the button is disabled until it is
  ticked). The reconnect form shows the same.
- The page sends `"force": true` only with the box ticked.
  - The route **refuses a body without `"force": true`**: 409 `sign_out_required`, before the rate
    limit and before anything is sent. So an API client cannot sign a phone out without the same
    explicit field.
  - This replaces `device_in_use` as the 409's reason. The page's "Sign in here anyway" question is
    removed.
- **Future-proofing.** The gateway issue magpern/sb-gateway-service#3 asks for an optional `force`
  (default `true`, so existing callers keep their behaviour) and a fixed `409 "device in use"`
  answer. The client function already takes `force: bool` and sends it in the body.
  - When the gateway ships that, a reviewed change can send `force: false` first, map the gateway's
    409 to `device_in_use`, and bring back the two-step question.
  - That change needs no rework of the client, the storage or the egress.
- If the owner decides the consent is not needed, removing the checkbox is a product change, the
  owner's decision. This ADR does not make it.

### 5. Bearer lifetime and the two kinds of 401

- The bearer has no refresh (the password is never kept). `expires_at` is stored from `expires_in`
  (§3).
  - The account page shows the connection as needing a reconnect once `expires_at` has passed. The
    match admission refuses `not_connected` for a row past `expires_at`, so a doomed job is never
    queued.
  - The **gateway's 401 stays the authority**: a bearer that the gateway refuses early (its key
    rotated) is caught by §6's 401 path.
  - The owner may raise `SESSION_TTL_SECONDS` on the gateway (a 24 h default means a daily
    password). classbeat reads `expires_in` and needs no change.
- **`401` with any `detail` other than `unknown client`** means the bearer was refused
  (`missing, invalid or expired bearer`). That covers more than one cause:
  - the bearer expired;
  - SwedeBeat invalidated the token inside it, which is common: signing in on the phone app takes
    the session back;
  - the gateway's session key was rotated;
  - or, today, **SwedeBeat failing its token check** (Context, fact 2).

  To keep an outage from asking every user for a password (and signing their phone out), the
  match confirms first:
  - It pauses 5 s, then makes **one** `GET {gateway}/auth/session` with the same bearer and key.
    A failed check is not cached, so the gateway asks SwedeBeat again.
  - `200`: the job ends `upstream_unavailable` (retried) and the row is untouched. The gateway
    caches a positive token check for 300 s, so a bearer SwedeBeat refused mid-match can still
    pass; the retry therefore comes no sooner than 330 s
    (`swedebeat_match.confirm_policy.CACHED_CHECK_SECONDS`), past the cache.
  - `401` again (not `unknown client`): the row flips to `needs_reconnect` as SwedeBeat's 401/403
    did (`connections.use`, `mark_needs_reconnect`).
  - `502`, `5xx` or a timeout: `upstream_unavailable`, untouched.

  **Accepted until magpern/sb-gateway-service#5 is fixed:** an outage of SwedeBeat's token check
  that lasts past the pause and the confirmation still flips the row, and the user's reconnect then
  signs their phone out. When the gateway answers 502 for those failures, the confirmation becomes
  a cheap extra call and can stay.
- **`401 unknown client`** is classbeat's configuration error, not the user's. It **never flips the
  connection and never asks for a password**.
  - Connect answers `app_key_unavailable` (§3).
  - A match ends `app_misconfigured` (an existing category of jobs and connections, not retried).
    `record_result` is not called, so the row's `last_error` is unchanged.
  - The worker logs `swedebeat gateway refused the client key` at error level with no value.
  - The job page says SwedeBeat is not available on this server.
- The exact-string test (`detail == "unknown client"`) is the contract's own distinction (its status
  table). A schema-422 list or any non-string `detail` is never compared.

### 6. Match: one `POST /catalog/match` per job

The `swedebeat_match` job (ADR 0016 §5) keeps its input, admission, preparation, revision, storage,
retention and isolation. Only the catalogue step changes:

- **The request.** The class's songs in play order, at most `MAX_SONGS` (40, under the gateway's
  200), deduplicated by the current `dedupe_key` (cleaned title and artist, in job memory only).
  - The job sends **one** `POST {gateway}/catalog/match` with
    `{"tracks": [{"title", "artist"}], "include_unsure": false}` and the headers `X-API-Key`,
    `Authorization: Bearer <bearer>`, `Content-Type` and `Accept`.
  - **Only each song's title and artist leave**: no Spotify id, BPM, length, role, class, brief or
    user detail. ADR 0016 §5 and T110 still hold, and the test still reads every request.
  - The titles are sent as the song list holds them, at most 200 characters each (`song_list`'s
    own bound, under the gateway's 300). The gateway cleans them itself.
  - A song whose title or artist is empty after trimming is **not sent**; it is `missing`. One bad
    item would make the gateway refuse the whole request with a 422.
  - One checkpoint before the call. No pacing: one call is one request in flight.
- **The answer.** It is read through a field allowlist (the ADR 0016 §4 parser rules carry over):
  - `items` must be a list with **exactly one item per sent track, in order**. Any other count is
    `invalid_response`.
  - Of each item, only `status`, `score` and `track.title`, `track.artist` and `track.duration` are
    read and checked:
    - `title` and `artist` are text of at most 200 characters, with URL-shaped and
      credential-shaped values dropped (the current `_text`);
    - `duration` is finite and from 0 to 3600 s, rounded;
    - `score` is a finite number from 0 to 1.
  - `reference`, `bpm`, `selected`, the totals, `playlist_name`, every other field and every URL are
    dropped inside the parser, and nothing from the answer is ever fetched.
  - The catalogue's `reference` is not stored (ADR 0016 §5: the mix is built by hand).
- **classbeat's own verdict** stays the library's 0.85:
  - `found` only when the gateway says `found` **and** `score >= SAME_SONG` (0.85), with a usable
    track;
  - `unsure` when the gateway says `found` below 0.85, or `unsure`, with a usable track;
  - `missing` when the gateway says `missing`, or the item has no usable track;
  - an `unsure` is never counted as found.

  `SAME_SONG` becomes classbeat's own constant (0.85). The verdict no longer calls
  `mowl.swedebeat.title_score` or `match_track`. `clean_title` stays for the dedupe key (a pure
  library helper, no I/O).
- **"All missing" is suspect.** When every sent track is `missing` and at least 3 distinct tracks
  were sent:
  - the job pauses 5 s and repeats the call once (after a checkpoint);
  - if the answer is still all missing, it is stored as it is, with an operator log line
    `swedebeat match all missing` (counts only).

  The result is not destructive: the page already lists missing songs and offers the replace, and
  the user can match again. A short list (one or two songs) is not retried.
- **Status mapping** (from the status and, for a 401, the string `detail` only; no body is logged):

  | Gateway answer | Job |
  |---|---|
  | `200` with a valid body | the verdicts |
  | `401` whose `detail` is `unknown client` | `app_misconfigured` (§5); connection untouched |
  | any other `401` | the confirmation of §5, then `needs_reconnect` (`mark_needs_reconnect`) or `upstream_unavailable` |
  | `403` (a proxy or deny in front of the gateway) | `app_misconfigured`; connection untouched |
  | `429` | `rate_limited` with `Retry-After` |
  | `502`, other `5xx`, a timeout, the deadline, a refused connection | `upstream_unavailable` (retried, as today) |
  | `413`, `422`, another status, a bad body, a redirect | `invalid_response` (a redirect: `outbound_blocked`) |

- The catalogue replace's re-match (`replace.rematch`) goes through the same function.
- The job sentence codes and the page are unchanged, apart from `app_misconfigured`'s sentence for
  SwedeBeat.

### 7. Nothing is written to SwedeBeat

*(Superseded by [ADR 0036](0036-swedebeat-studio.md) §8 on 2026-11-15: classbeat may also call `GET /tracks/{reference}/download`, `POST /playlists`, `POST /playlists/{id}/publish`, `POST /playlists/merge-publish` and a user's approved output choice. `GET /playlists/shared` (reconciliation only). `PATCH`, `/catalog/search` and the match's `playlist` mode stay forbidden. ADR 0036 §8 also amends §1's 150 s clamp for the download (120 s read) and `merge-publish` (900 s).)*

classbeat never calls the gateway's playlist routes. ADR 0016 keeps the mix manual, and the gateway
ships with writes off.

- The gateway client module exposes only `login`, `session` (the 401 confirmation of §5) and `match`.
- A guard test fails on any other gateway path string in `classbeat`: `/playlists`, `/publish`,
  `merge-publish` and `/tracks/`.

Using the gateway's search, download or playlist routes would be a new ADR. The same goes for
`playlist` mode of the match, which reads a public Spotify playlist with the gateway's Spotify app:
classbeat sends `tracks` only.

### 8. What is removed

Nothing of classbeat holds, mounts, reads, validates or tests SwedeBeat's AES key any more:

- **Settings:** `swedebeat_app`, `swedebeat_app_file`, `swedebeat_keys()`,
  `classbeat.config_swedebeat`, `check_worker_has_no_swedebeat_app` and the worker's start check.
  The worker now holds the client key, and neither process holds SwedeBeat's key.
- **The image:** the Dockerfile's `swedebeat_app` secret mount and `REQUIRE_SWEDEBEAT_APP`, the
  release workflow's `SWEDEBEAT_APP` check and `secret-envs`, the PR-image "no key file" CI step,
  `.gitignore`/`.dockerignore` entries that exist only for the key, and the `swedebeat_app` lines of
  `scripts/worktree.sh`, `scripts/build-mowl-wheel.sh` and the install and compose checks.
  - The vendored wheel check (ADR 0009 §12: the wheel carries no `_swedebeat_app.json`) **stays**.
    It is now a plain "the wheel holds no SwedeBeat key" guard.
  - `scripts/check-prod-compose*` keep refusing a `swedebeat_app` secret in a production compose
    file. An old server copy must not mount it.
- **Compose:** the `swedebeat_app` secret in `compose.yaml`, `compose.prod.yaml`, `compose.e2e.yaml`
  and `.env.example`; the new `swedebeat_gateway_key` (§2) takes its place in web and worker.
- **Tests:** `test_swedebeat_key_guard.py` is replaced by `test_swedebeat_gateway_key_guard.py`. The
  new guard checks that the key comes from the secret file only, sits on web and worker only, is in
  no image or workflow, and is redacted. It also checks that no tracked file is shaped like the old
  `{"key", "iv"}` app key (the e2e test key is deleted too), that `swedebeat_app` appears in no
  compose or workflow file, and that no SwedeBeat host is in `classbeat`.
  - The fake SwedeBeat (`backend/tests/swedebeatfake.py`, `cataloguefake.py`, `swedebeatcore.py`)
    and `e2e/fake-swedebeat/` with its public test key go. The SwedeBeat-shape tests
    (`test_swedebeat_match_catalogue.py`, the cloud egress tests) are replaced by gateway-shape
    tests.
- **Code:** `SWEDEBEAT_WWW_HOST`, `SWEDEBEAT_CLOUD_HOST`, `SWEDEBEAT_CLOUD_ALLOWLIST`,
  `swedebeat_cloud_*`, `www.swedebeat.se` from `WEB_URLLIB_HOSTS`, the `mowl.swedebeat.login` call,
  the catalogue query builder and the extendedsearch parser.
  - Log redaction drops the dead names (`swedebeat_app`, `swedebeat_app_key`). It keeps
    `swedebeat_device_id`, which is still sent in every login.
  - It keeps `swedebeat_token` (a defence for any stray library field) and adds `bearer`,
    `x-api-key` and `swedebeat_gateway_key`.
- **Done when:** `git grep -E "swedebeat\.se|swedebeat_app|swedebeat\.login|match_track"` finds
  nothing under `backend/src`, `e2e`, `compose*.yaml`, `Dockerfile` or `.github`, except the guard
  tests that look for these strings.

**The owner's GitHub secret `SWEDEBEAT_APP`** is no longer read by any workflow. Deleting it, and the
older release images that hold the key, is an owner step (§10). Agents do not delete secrets or
packages.

### 9. Fakes

- **Backend:** `backend/tests/gatewayfake.py` is an in-process fake of `docs/api.md`'s `/auth/login`
  and `/catalog/match` over an httpx transport. It holds:
  - users with passwords;
  - issued bearers with an expiry the test can move;
  - a catalogue (title, artist → status, score, track);
  - failure injection: unknown client, a bearer 401, `502`, `429` with `Retry-After`, a slow answer,
    an oversized or gzip body, a redirect, a wrong item count, all missing;
  - a record of every request's path, headers (key and bearer) and body.

  It checks `X-API-Key` before anything else and the bearer next, in the contract's order. Two users
  get two bearers, and the fake records which bearer each match carried.
- **e2e:** `e2e/fake-gateway/` serves the same core over TLS under a compose network alias. The
  stack's `CLASSBEAT_SWEDEBEAT_GATEWAY_URL` names it, its CA joins the shared bundle chain, and it
  replaces `swedebeat-fake`.
  - The stack mounts a **public test client key**, `e2e/fake-gateway/client-key.txt`, as web's and
    the worker's `swedebeat_gateway_key`. It is a test value, not the owner's.
  - The connect, match, replace and hand-off specs run against it, and the netguard rule is
    unchanged.

### 10. Owner steps (never an agent's)

1. On the gateway:
   - list classbeat's key in `CLIENT_API_KEYS`;
   - keep its writes off (`allow_swedebeat_writes` false, the default). Its write guard applies to
     every client, not per key, so classbeat's key must never be the only thing between a caller
     and a permanent SwedeBeat record;
   - **reserve `192.168.1.222`** for the server (a DHCP reservation or a static address), so the
     address can never be handed to another device;
   - set `GATEWAY_BIND` so the port listens on that address;
   - keep it bound to the LAN or the docker bridge, never on a public interface while it is plain
     HTTP;
   - optionally raise `SESSION_TTL_SECONDS` (§5).

   If it is ever exposed outside the LAN: put TLS in front, allow only classbeat's address in nginx,
   and rate-limit `/auth/login`. Then the setting becomes an `https` name.
2. On the classbeat server:
   - set `SWEDEBEAT_GATEWAY_URL=http://192.168.1.222:7355` (the compose variable for web and
     worker);
   - create `secrets/swedebeat_gateway_key` **before** deploying any release that carries SG-1
     (empty is fine: SwedeBeat is then "not available"), and write the key into it (mode as the
     other secret files);
   - deploy a release carrying SG-4, and keep `SWEDEBEAT_ENABLED` as it is.

   The worker and web containers must reach the host's LAN address (docker's default bridge
   does).
3. **After a release with SG-4 exists:** delete the GitHub secret `SWEDEBEAT_APP` (until SG-4 the
   release workflow fails without it), and delete the older release image tags that carry the key
   from GHCR (the key is the gateway's now).
4. Authorize one live check with the owner's own SwedeBeat account:
   - connect (it signs the phone app out, as the form says);
   - match a short real list;
   - read `found`/`unsure`/`missing` against the app;
   - disconnect.

   Logged under "Live activation log".
5. Decide on magpern/sb-gateway-service#3 (optional `force` and a 409). Until then the up-front
   consent of §4 stays. Fix magpern/sb-gateway-service#5 (a 502 when SwedeBeat's token check
   fails).
6. **The privacy notice** (`frontend/src/i18n/legal.en.ts`) says SwedeBeat gives classbeat a
   device id that is stored encrypted. After SG-2 that is no longer true: classbeat stores a
   gateway bearer, and sends the password through the owner's gateway. The wording, and whether to
   name the gateway, is the owner's (and a lawyer's) call. Agents do not edit legal text.

### 11. The issues (milestone "SG SwedeBeat gateway")

| # | Title | Scope | Review | Depends on |
|---|---|---|---|---|
| SG-0 | `docs(adr): 0035 the SwedeBeat gateway` | this ADR, roadmap Amendment 23, notes on ADR 0015 and 0016 | Opus | none |
| SG-1 | `feat(swedebeat): gateway client, egress, client key and fakes` | §1 (the URL setting and its policy, the origin-checked client), §2 (setting and mount; available still false unless set), the client module `classbeat.swedebeat_gateway` (`login`, `match`, the parsers and the status mapping as pure `*_policy` modules), `gatewayfake.py`, `e2e/fake-gateway` in `compose.e2e.yaml` beside the old fake, guard and egress tests. No caller is switched | Opus Developer, Opus Reviewer | SG-0 |
| SG-2 | `feat(swedebeat): connect and match through the gateway` | §3 to §6 in **one PR**, so main never holds a bearer that the old match could send to `cloud.swedebeat.se`. It covers:<br>- connect: `swedebeat_account` with `open_bearer`, the payload, `build_credentials` refusing `swedebeat`, the route's `sign_out_required`, the dialog's notice and checkbox (English, keys on `untranslated.json`);<br>- match: the job, the replace's re-match, the expiry admission, the 401 confirmation, `app_misconfigured`.<br>Tests changed:<br>- `test_swedebeat_connect.py`, `test_swedebeat_secrecy.py`, `test_swedebeat_match.py`, `test_swedebeat_match_catalogue.py` (to gateway shape), `test_swedebeat_replace.py`, `test_classes_swedebeat_match.py`, `test_swedebeat_preference.py`, `m6world.py`;<br>- the frontend `SwedebeatConnection.test.tsx`;<br>- `e2e/tests/swedebeat.spec.ts`, `swedebeat-match.spec.ts` and the hand-off spec, on the fake gateway | Opus, Opus | SG-1 |
| SG-3 | folded into SG-2 (the issue is closed with a pointer) | none | none | none |
| SG-4 | `refactor(swedebeat)!: remove the SwedeBeat app key, hosts and fake` | It covers:<br>- §7's guard and §8 in full;<br>- the threat model: T19, T23, T104 to T107 and T110 rewritten "as enforced", plus the accepted-risks paragraph on the app key;<br>- ADR 0010's table row and accepted risk, and ADR 0009's wheel-key paragraph (the notes on ADR 0015 and 0016 are already in SG-0);<br>- the runbooks and `docs/developer-commands.md`. | Opus, Opus (egress and secret handling) | SG-2 |
| SG-5 | `feat(i18n): Swedish and Danish for the SwedeBeat gateway strings` | the new strings in sv and da (ADR 0023's translation PR rule) | Sonnet translator, Opus Reviewer | SG-2 |

SG-1 changes `classbeat.egress` (over 1000 lines). It has Opus review, so the split-on-touch rule
applies (CLAUDE.md, rule 2): the gateway's origin check and client move to their own module, and
`egress.py` ends with no net growth. The e2e fake's https name must pass the policy's DNS-name
rules (for example `gateway.classbeat-e2e.test`).

`docs/status.md` is updated by the Architect after each merge.

## Alternatives

- **One fixed https host in code** (`sb.classbeat.eu` through nginx, the first draft).
  - It is stronger in transit.
  - The owner chose the LAN address on the shared host (2026-11-15).
  - The policy still allows an https name later, by setting only.
- **Allow any http URL.** That would let a mistyped or repointed DNS name receive passwords in
  clear. Plain http is limited to IP literals in loopback and private ranges.
- **A non-standard port as protection.** It obscures nothing from a scanner. The protection is the
  key, the LAN binding and TLS if exposed.
- **Bake the client key into the release image** (as `SWEDEBEAT_APP` was).
  - Anyone who can pull the image could read it.
  - A runtime secret file changes without a rebuild and is the pattern of every other secret.
- **Keep the per-song requests** (`GET /catalog/search` per song). That is 40 calls instead of one,
  the gateway's matcher would be redone in classbeat, and the contract recommends `/catalog/match`.
- **Trust the gateway's `found` (0.78).** It is looser than classbeat's 0.85 on which recordings
  count as the same song. Re-thresholding keeps the verdicts users already saw on the fakes, and
  costs nothing.
- **Treat all-missing as a failure.** A real list of songs SwedeBeat lacks could then never be
  stored. One retry and a log line is the cheap middle.
- **Drop the sign-out consent** because the gateway always forces. That is the owner's product call
  (§4). Until then the consent moves up front.
- **Store `customer_id`.** Nothing uses it. The email in `public` already says whose account it is.

## Consequences

- classbeat holds no SwedeBeat secret and makes no SwedeBeat request. Its one SwedeBeat-related
  outbound path is the configured gateway origin (production: `http://192.168.1.222:7355`), from
  web (login) and worker (match), with a client key both processes now mount.
- **Accepted (owner, 2026-11-15):** on the LAN, the SwedeBeat password, the bearer and the client
  key travel in clear between classbeat and the gateway (§1).
- Every connect signs the user's SwedeBeat phone app out, and the form says so first. The bearer
  expires (24 h by default) and a match after that asks for the password again.
- A gateway outage or a wrong client key stops SwedeBeat connect and match for everyone. The page
  says "not available on this server" and nobody's connection is touched.
- The release image no longer holds SwedeBeat's key. The PR-image key check and the key guard become
  a gateway-key guard. Old images still hold it until the owner deletes them.
- Threat model:
  - T104 (the app key leaking) is closed for new images and becomes the client key's row: its leak
    lets a caller use the gateway with a user's bearer or password, so rotate it in
    `CLIENT_API_KEYS` and the secret file.
  - T105 changes to "every connect forces, after an explicit consent".
  - T19 and T110 change their hosts.
  - T23 gains the accepted LAN plaintext path for the password (§1).
  - The gateway, a service of the owner's on a home server, becomes a processor of SwedeBeat
    passwords and bearers in transit (it keeps neither the password nor a session store). That is
    the owner's own service and risk. classbeat's part is the URL policy, the key and the
    one-origin client.
