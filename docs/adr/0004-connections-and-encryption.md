# ADR 0004: Provider connections and envelope encryption

Status: Accepted

## Context

M2 stores the first secrets that belong to a user rather than to classbeat: the MOWL token
(M2), then the Spotify Client ID with access and refresh tokens (M4) and the Swedebeat token
and device id (later, behind a disabled flag). Roadmap §1 requires them to reach the
`mowl-workouts` library only as a per-request or per-job `Credentials` object; §7 requires
envelope encryption (AES-256-GCM, AAD `user_id|provider`, wrapped data keys, `key_id`
rotation) and log-redaction tests. ADR 0003 §8 encrypts TOTP secrets with a key derived from
`app_secret_key` and leaves rotation to this scheme. M2's "done when" asks for ciphertext only,
no tokens in logs, and a known MOWL token lifetime. This ADR fixes the design so #65 to #67
need no decisions of their own. Spotify token refresh is specified in M4 (§3).

Facts from `mowl-workouts` v0.4.0 (#64) this relies on: `mowl.credentials.Credentials` holds
every provider's fields and hides the tokens from `repr`; the clients are synchronous; HTTP
errors become a single `MowlError` whose text can include provider messages and request paths,
raised `from` the `HTTPError` (status, headers, URL), while network errors (`URLError`,
timeouts) propagate unwrapped; the sign-in's login ticket travels in a URL path; the MOWL
token is a 36-character GUID, not a JWT; Spotify tokens are refreshed lazily inside any
library call.

## Decision

### 1. The `connections` table

An owned table (ADR 0002 §2: `owner_id`, `UNIQUE (owner_id, id)`, RLS forced, read only
through `UserContext`), one row per user and provider. Every column is `NOT NULL` unless
marked otherwise.

| Column | Notes |
|---|---|
| `id uuid` | primary key with the `gen_random_uuid()` default ADR 0002 requires, but the application **always supplies it explicitly** on insert, before encryption (§2, §4) |
| `owner_id uuid` | references `users(id) ON DELETE CASCADE` |
| `provider text` | check `IN ('mowl', 'spotify', 'swedebeat')`; `UNIQUE (owner_id, provider)` |
| `status text` | check `IN ('connected', 'needs_reconnect')` |
| `last_error text NULL` | a category from §5, check constraint on the list; never a provider message |
| `public jsonb` | default `'{}'`; non-secret fields the UI or jobs need without decrypting: the Spotify Client ID and Spotify user id (M4). Nothing personal beyond those |
| `secret bytea`, `secret_nonce bytea` | the encrypted payload (§2); check `octet_length(secret_nonce) = 12` |
| `wrapped_dek bytea`, `dek_nonce bytea` | the data key, wrapped (§2); check `octet_length(wrapped_dek) = 48` (32 + tag), `octet_length(dek_nonce) = 12` |
| `key_id text` | check `key_id ~ '^kek-[a-z0-9-]{1,28}$'` (the `kek-` prefix keeps KEK ids apart from ADR 0003's `app-v1`) |
| `expires_at timestamptz NULL` | the connection's long-lived expiry when known (§6). A Spotify access token's expiry lives in the payload, not here |
| `connected_at`, `updated_at` | timestamps; `last_used_at` (`NULL` until first use) |

The MOWL email, the user's provider display name and passwords are not stored. The account
page shows the provider, the status and "connected since".

`UserContext.connections` (ADR 0002 §1) holds the `(provider, id)` pairs of the user's rows,
never secrets or status. Reading them needs a context, so the issuer in `classbeat.auth`
works in two steps: it issues a context without connections, reads the pairs through
`classbeat.connections` in that user's session, and then issues the final context. The CLI's
per-owner work (rewrap, §2) gets its contexts from a `classbeat.auth` function used only by
the CLI.

### 2. Envelope encryption

- **Module:** one pure module, `classbeat.crypto.envelope` (no database access), does every
  encryption, decryption, wrap and unwrap. Only `classbeat.connections`, `classbeat.auth`
  (TOTP) and `classbeat.crypto.keks` (the key check) import it; a guard test enforces that list.
- **Encodings:** UUIDs are written in their canonical text form (`str(uuid)`, lowercase,
  hyphenated, 36 characters). Canonical JSON is
  `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`.
  AAD fields are joined with `|`; no field can contain `|` (UUIDs, the provider check, the
  `key_id` pattern), so the strings can't collide.
- **Payload:** the provider's secret fields as canonical JSON (MOWL `{"token"}`; Spotify
  `{"access_token", "refresh_token", "expires_at"}`; Swedebeat `{"token", "device_id"}`),
  encrypted with AES-256-GCM (`cryptography`) under a **data key (DEK)**: 32 random bytes,
  **new for every write of the row**, with a random 96-bit nonce. A DEK encrypts exactly one
  payload, so nonce reuse under one key can't happen.
- **AAD:** `classbeat-conn-v1|<owner_id>|<provider>|<id>`. Ciphertext copied into another
  user's row, another provider's row or another row of the same user fails to decrypt.
- **Wrapping:** the DEK is encrypted with AES-256-GCM under the **key-encryption key (KEK)**
  named by `key_id`, random 96-bit nonce, AAD
  `classbeat-dek-v1|<key_id>|<owner_id>|<provider>|<id>`.
- **KEK files:** a new secret file `connection_kek`, created by `scripts/worktree.sh init`,
  holding exactly 64 lowercase hexadecimal characters (32 bytes), optionally followed by one
  newline. Its id comes from `CLASSBEAT_CONNECTION_KEK_ID` (default `kek-1`); web and worker
  refuse to start if the id doesn't match the `key_id` pattern. Retired KEKs are files
  `connection_kek_<key_id>`, read for decryption only: a row's `key_id` that matches the
  pattern names the file to open, and no other input ever builds a file name. Every KEK file,
  current and retired, is a compose `secrets:` entry on both web and worker.
- **Key check:** a system table `connection_keks (key_id primary key, check_value bytea,
  created_at)`, listed in `SYSTEM_TABLES` and reached only through `classbeat.crypto.keks`,
  stores `HMAC-SHA256(KEK, "classbeat-kek-check-v1")`, first 16 bytes. On startup web and
  worker run `INSERT … ON CONFLICT (key_id) DO NOTHING` for the current KEK, then read the row
  back and compare, so whichever starts first records it and both check the same value. They
  refuse to start if the current KEK file is missing or malformed, or its check value differs
  (a swapped key under an unchanged id). Every KEK was current once, so a retired KEK always
  has a row; a retired file with no row, or with a different check value, is not loaded and
  counts as `key_unavailable`.
- **Decrypt failures:** a missing or rejected KEK gives `key_unavailable`. That is an operator
  error, so the request fails with the category and the row is **not** changed; restoring the
  file fixes it. A GCM authentication failure (wrap or payload) gives `decrypt_failed`: the
  row's status flips to `needs_reconnect` with a conditional update (§4). Either way the
  category and the row id are logged, never key material or ciphertext, and no other key is
  tried. A TOTP row fails MFA closed with the generic error either way, and the attempt does
  not count toward `mfa_failed`. With `key_unavailable` it writes the auth event
  `totp_key_unavailable` and the operator restores the file; with `decrypt_failed` it writes
  `totp_decrypt_failed` and is fixed by `admin mfa-reset` (ADR 0003 §7).
- **Rotation runbook** (routine, no compromise): add the new KEK as `connection_kek` with a
  new `CLASSBEAT_CONNECTION_KEK_ID`, keep the old one as `connection_kek_<old id>`, restart,
  run `python -m classbeat keys rewrap`, and once rewrap reports zero rows for that `key_id`
  in both connections and TOTP, remove the old file and its compose `secrets:` entries from
  the running stack. Keep one copy with the backups until every backup taken before the
  rotation has aged out, so those backups stay restorable (M6); then destroy it.
- **After a suspected KEK compromise, everything goes.** Rewrap changes only the wrap: a row
  moved to a new KEK keeps its data key and ciphertext, so an old KEK plus any old dump or
  backup can still read it, and the tokens inside stay valid at the provider. Working out
  which rows a leaked key exposes is therefore not attempted. Any suspected compromise of any
  KEK, current or retired, is treated as exposing every connection and every TOTP secret:
  1. stop web and worker (no traffic, no key held in memory);
  2. install a new KEK as `connection_kek` with a new id, and remove the old KEK files and
     their compose `secrets:` entries; if the leak came through the secrets directory, also
     rotate `app_secret_key` (ADR 0003 §8) and the database passwords;
  3. run `docker compose --profile maint run --rm maint python -m classbeat keys wipe
     --confirm`. `maint` is a compose service under the `maint` profile, never started by
     default, that has the owner role's URL and `database_owner_password` plus the SMTP
     settings; it has no KEK. **Web and worker never get the owner password** (an owner can
     turn off forced RLS, ADR 0002 §4), so the wipe can't run from them. It refuses to start if
     any session of the app role is connected (`pg_stat_activity`), or if the configured KEK
     id already has a check row (the leaked key wasn't replaced). It takes the user ids from
     the same `classbeat.auth` function as rewrap and, for each user, in one transaction with
     `app.user_id` set:
     - writes an `auth_events` row `connections_wiped` listing the affected providers (nothing
       else), deletes that user's `connections` rows, and, only if the user has a TOTP row,
       resets it the way `admin mfa-reset` does (ADR 0003 §7: removes the TOTP row and
       recovery codes, forgets known devices, revokes sessions, writes `auth_events`), through
       `classbeat.auth` (guard 8);
     - after the commit it mails the user, built from that event: which providers were
       affected, that classbeat removed them, and what to do (end sessions or change the
       password at MOWL, whose tokens can't be revoked and may stay valid until they expire;
       remove the app's access in the Spotify account), plus the TOTP reset link if reset. The
       mail holds no token, id or account detail. A sent mail is marked on the event; a failed
       one is counted and left unmarked.
     Then it deletes every `connection_keks` row, runs `VACUUM (FULL)` on the affected tables
     (outside a transaction) and a `CHECKPOINT`. It prints counts only, including unsent
     mails. Re-running is safe: wiped users are skipped, and `keys wipe --resend` retries every
     unmarked mail; an address the relay permanently refuses is reported by `--resend` and
     never blocks the wipe itself;
  4. treat backups taken before the wipe as compromised, including WAL or PITR archives:
     destroy them, or, after restoring one, run the wipe again before web and worker start
     (M6 restore drill);
  5. start web and worker; the new KEK gets its check row on startup.
  Users then reconnect and re-enroll TOTP. For a beta of a handful of users this costs a few
  reconnects and is the only response that can't miss a row. A #65 test: after
  rotate → rewrap → wipe, no connection row, no `connection_keks` row and no v2 TOTP secret is
  left, a user with a connection and no TOTP got the mail, and a fresh connect works under
  the new KEK.
- **Rewrap mechanics:** it runs per owner (ADR 0002 §4): it gets the user ids from a
  `classbeat.auth` function (guard 8), sets `app.user_id`, and processes that owner's rows as
  `classbeat_app`, so it needs no RLS bypass. Each row is re-wrapped in its own transaction:
  `SELECT … FOR UPDATE`, unwrap, wrap under the current KEK, then
  `UPDATE … WHERE id = :id AND key_id = :old_key_id`. It prints counts per `key_id` only.
  Rows can't lack an owner (cascade).
- **TOTP:** #65 moves `mfa_totp` onto the same primitive. New nullable columns `wrapped_dek`
  and `dek_nonce`; `key_id` stays `NOT NULL`, with a check that tells the forms apart:
  `(key_id = 'app-v1' AND wrapped_dek IS NULL AND dek_nonce IS NULL) OR
  (key_id ~ '^kek-[a-z0-9-]{1,28}$' AND octet_length(wrapped_dek) = 48 AND
  octet_length(dek_nonce) = 12)`. TOTP payload AAD
  `classbeat-totp-v2|<user_id>`, wrap AAD `classbeat-totp-dek-v1|<key_id>|<user_id>`. From #65
  on, new enrollments write the v2 form.
  Rows with `key_id = 'app-v1'` keep decrypting under ADR 0003 §8 until migrated by
  `keys rewrap --totp` (one-time), which runs inside `classbeat.auth`: per row
  `SELECT … FOR UPDATE`, re-encrypt, then `UPDATE … WHERE user_id = :u AND key_id = 'app-v1'`
  so a secret the user replaced meanwhile is never overwritten. Plain `keys rewrap` covers v2
  TOTP rows as well as connections.

### 3. From the row to the library

- `classbeat.connections` is the only module that turns rows into credentials. Its repository
  takes a `UserContext` and returns a `mowl.Credentials`, never a dict or the plaintext fields
  on their own. Our wrappers have a redacting `__repr__`/`__str__` and are never serialized.
- Jobs (M3a) store only the connection id. The worker rebuilds `Credentials` from the job's
  `UserContext` when it runs and drops it when the run ends. Credentials never go into job
  rows, job events, SSE, model input, tool results or error messages. A job whose connection
  is gone or `needs_reconnect` stops before any provider call with the category
  `not_connected`.
- Library calls run in a worker thread (`asyncio.to_thread`); nothing in the library touches
  the database.
- **Spotify refresh (M4, specified in M4's ADR).** Requirements it must meet:
  - classbeat refreshes **before** building `Credentials` when the access token expires within
    a margin, so the library's own lazy refresh (inside any call) is only a fallback;
  - one refresh per user at a time, with the lock covering the refresh itself, not only the
    write-back, and the refresh call's timeout below the database role's transaction and idle
    timeouts (ADR 0002 §5), or a lock that isn't a database transaction;
  - after taking the lock the stored tokens are re-read and used if another request already
    refreshed;
  - a rotated refresh token is persisted before the lock is released, and a failed persist
    fails the call;
  - the library's synchronous callback reaches the database only through that path.

### 4. Connect, reconnect and disconnect

- **Recent auth** (ADR 0003 §6, step-up for users with TOTP) for connect, reconnect and
  disconnect.
- **MOWL connect:** `POST /api/connections/mowl` with email and password (JSON over HTTPS,
  CSRF-checked). The backend calls the library's sign-in, stores the token, and discards the
  password: it is never stored, logged, put in a URL, kept in an exception or sent to a model.
  Request bodies of this route are excluded from any request logging.
- **Write path (connect and reconnect).** The MOWL sign-in runs first, outside any
  transaction, so no row lock is held across a network call. Then one transaction:
  1. `SELECT … FOR UPDATE` on `(owner_id, provider)`.
  2. If a row exists, keep its `id`; otherwise generate a new `id` in the application.
  3. Encrypt under a new DEK with that `id` in the AAD.
  4. `UPDATE` the existing row (also setting `status = 'connected'` and `last_error = NULL`),
     or `INSERT` the new one. An `INSERT` that hits `UNIQUE (owner_id, provider)` (a parallel
     connect) rolls the whole transaction back and retries once from step 1 in a new
     transaction (READ COMMITTED).
  No `INSERT … ON CONFLICT DO UPDATE`: it would keep the old `id` with a payload encrypted for
  a new one.
- **Status changes** (to `needs_reconnect`, `last_error`, `last_used_at`) are conditional on
  the version that was read: `UPDATE … WHERE id = :id AND wrapped_dek = :read_wrapped_dek`.
  `wrapped_dek` changes on every write (a new DEK, a new nonce), so a stale 401 from before a
  reconnect can't mark the fresh connection `needs_reconnect`. They never touch `secret`,
  `wrapped_dek` or `key_id`.
- **Rate limits** (ADR 0003 §9 mechanism: reserve first, 429 with `Retry-After`): connect
  attempts count requests, and a success releases its slots. 5 per user, 20 per IP and 5 per
  normalized MOWL email (HMAC-keyed like ADR 0003's buckets) per 15 minutes, so the route
  can't be used to test passwords against MOWL.
- **Disconnect** deletes the row. Older backups still hold it wrapped, so it stays recoverable
  from them with that backup's KEK until the backup ages out or every copy of that KEK is
  destroyed. MOWL has no known revoke endpoint, so disconnect is local only; the page says
  classbeat stops using the token.
- **Account deletion** deletes the rows (cascade). Exports (M6) list connections without
  secrets.

### 5. Errors and status

`classbeat.connections` wraps every library call. It maps the exception to a category from
its type and its `__cause__` only, never from the message text:

| Category | From |
|---|---|
| `invalid_credentials` | during connect: a `MowlError` caused by an HTTP 401 or 403, or a `MowlError` from the sign-in with no HTTP cause (MOWL's refusal) |
| `needs_reconnect` | on use: a `MowlError` caused by an HTTP 401 or 403; the status flips to `needs_reconnect` |
| `rate_limited` | a `MowlError` caused by HTTP 429; `Retry-After` read from the cause's headers |
| `upstream_unavailable` | a `MowlError` caused by HTTP 5xx; an unwrapped `URLError`, `TimeoutError`, `socket.timeout` or `ConnectionError` |
| `key_unavailable`, `decrypt_failed` | §2 |
| `not_connected` | §3 |
| `unknown` | anything else: a `MowlError` outside the sign-in with no HTTP cause, any other HTTP status, `ValueError`, JSON errors and other exceptions |

The library's exception never leaves the wrapper. The `except` block only records the
category, the exception class name and the HTTP status into locals; the wrapper raises its own
`ConnectionFailure(category)` **after** the block, so the new error has neither
`__cause__` nor `__context__` (`raise … from None` would still leave `__context__` set). The
new error's `args` hold only the category. Nothing logs, stores or returns `str(exc)`,
`exc.args`, the response body, the `HTTPError`'s URL or a traceback of a library exception;
logs get the category, the class name and the status only. This matters because a failed
sign-in can put the MOWL login ticket, which can be exchanged for a token, into the error text
and the URL, and the sign-in's frames hold the password.

- **The `except` block can't raise.** Working out the category reads only attributes that
  always exist (`isinstance` checks, `getattr(cause, "code", None)`), parses `Retry-After` only
  as an integer (anything else, such as an HTTP date, means no hint), and is itself wrapped so
  any error inside it falls back to `unknown`. Otherwise the library's exception would become
  the `__context__` of that error.
- **Type checks go most specific first:** `HTTPError` is a subclass of `URLError`, so an
  unwrapped `HTTPError` is classified by its status, not as `upstream_unavailable`.
- **No frame locals anywhere.** `ConnectionFailure.__traceback__` still holds the wrapper's
  frame, which held the password during connect. No error reporter, debugger page or logging
  option that captures frame locals (`capture_locals`, Sentry's `include_local_variables` and
  the like) is ever enabled; the log formatter prints class, message and line numbers only.
- **The log formatter** (`classbeat/log.py`) is changed in #65 to stop following `__context__`
  when `__suppress_context__` is set, while still following `__cause__`, so a `DBAPIError`
  raised `from` keeps being found and reduced to its SQLSTATE (ADR 0003 §10).

### 6. Token lifetime

The MOWL token is a 36-character GUID with no expiry claim, so classbeat can't read its
lifetime. It is learned from use: a 401 or 403 flips the connection to `needs_reconnect`, and
`docs/status.md` lists an owner-authorized check against the owner's account to measure it
(M2's "done when"). `expires_at` stays `NULL` for MOWL until that is known. An expired token
degrades to a reconnect prompt, never to a failure that loses work.

### 7. Logging and redaction

- The JSON log formatter (ADR 0003 §10) drops or masks keys named `token`, `access_token`,
  `refresh_token`, `password`, `secret`, `authorization`, `cookie`, `ticket`, and anything
  under `public`/`secret` of a connection row.
- `mowl-workouts` and HTTP client loggers run at `WARNING` in classbeat; the library never logs
  headers or bodies (#64 checked it).
- Tests: caplog over connect, use and every error path, including a failed ticket exchange
  whose fake error text contains the ticket, shows no token, ticket or password; a `pg_dump` of
  a test database with a known fake token doesn't contain it (ciphertext only); `repr()` of
  `Credentials` and of the repository's results contains no secret; a `ConnectionFailure`
  raised from a failing library call has `__cause__` and `__context__` both `None` and `args`
  equal to `(category,)`; the `e2e` job's
  `docker inspect` check covers `connection_kek`.

## Alternatives

- **`pgcrypto` in the database:** the key would travel to the database in every query and
  sit in its logs and `pg_stat_statements`. Rejected.
- **One key for all rows, no envelope:** rotation would re-encrypt every payload, and one
  compromised value would expose the whole column. The roadmap asks for envelope encryption.
- **AES-KW for wrapping:** fine too; GCM with random nonces keeps one primitive and is well
  within its limits at this scale.
- **KEK derived from `app_secret_key` (HKDF):** couples rotating CSRF and device keys to
  re-wrapping every connection. Rejected for a separate file.
- **A KMS or Vault:** not available on Docker Desktop or the beta VPS. The KEK loader is the
  seam where one plugs in later (M6 or M7).
- **Storing the MOWL password to re-sign-in on expiry:** forbidden by AGENTS.md. Rejected.

## Consequences

- #65 adds the table, `classbeat.crypto.envelope`, `classbeat.connections`, the KEK file and
  check, the `keys rewrap` and `keys wipe` CLI with the `maint` compose service, the
  log-formatter fix and the TOTP move;
  #66 adds MOWL connect and pins the `mowl-workouts` v0.4.0 wheel; #67 proves isolation with
  two concurrent users.
- Backups hold ciphertext only; restoring one needs the KEK files of its time, which are backed
  up separately (M6 restore drill).
- Losing the current KEK means every user reconnects and re-enrolls TOTP; there is no recovery
  path by design.
- `mowl-workouts` follow-ups (before Swedebeat or M4): hide `swedebeat_device_id` from `repr`,
  and structured errors (a status attribute) so §5 doesn't depend on `__cause__`.
- Threat model T40 to T44 and three accepted risks follow from this ADR.
