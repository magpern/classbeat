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
need no decisions of their own.

## Decision

### 1. The `connections` table

An owned table (ADR 0002 §2: `owner_id`, `UNIQUE (owner_id, id)`, RLS forced, read only
through `UserContext`), one row per user and provider:

| Column | Notes |
|---|---|
| `id uuid`, `owner_id uuid` | `owner_id` references `users(id) ON DELETE CASCADE` |
| `provider text` | `mowl`, `spotify` or `swedebeat` (check constraint); `UNIQUE (owner_id, provider)` |
| `status text` | `connected` or `needs_reconnect` |
| `last_error text NULL` | a category only (§5), never a provider message |
| `public jsonb` | non-secret fields the UI or jobs need without decrypting: the Spotify Client ID and Spotify user id (M4). Nothing personal beyond those |
| `secret bytea`, `secret_nonce bytea` | the encrypted payload (§2) |
| `wrapped_dek bytea`, `dek_nonce bytea`, `key_id text` | the data key, wrapped (§2) |
| `expires_at timestamptz NULL` | the token's own expiry when it states one (§6) |
| `connected_at`, `updated_at`, `last_used_at` | timestamps |

The MOWL email, the user's provider display name and passwords are not stored. The account
page shows the provider, the status and "connected since".

### 2. Envelope encryption

- **Payload:** the provider's secret fields as canonical JSON (MOWL `{"token"}`; Spotify
  `{"access_token", "refresh_token", "expires_at"}`; Swedebeat `{"token", "device_id"}`),
  encrypted with AES-256-GCM (`cryptography`) under a **data key (DEK)**: 32 random bytes,
  new for every write of the row, with a random 96-bit nonce.
- **AAD:** `classbeat-conn-v1|<owner_id>|<provider>|<connection id>`. Ciphertext copied into
  another user's row, another provider's row or another row of the same user fails to decrypt.
- **Wrapping:** the DEK is encrypted with AES-256-GCM under the **key-encryption key (KEK)**
  named by `key_id`, random nonce, AAD `classbeat-dek-v1|<key_id>|<owner_id>|<provider>|<id>`.
- **KEKs:** a new secret file `connection_kek` (32 random bytes, created by
  `scripts/worktree.sh init`), separate from `app_secret_key` so the two rotate independently.
  Its id comes from `CLASSBEAT_CONNECTION_KEK_ID` (default `kek-1`). Retired KEKs stay
  readable as files `connection_kek_<id>` for decryption only. Web and worker refuse to start
  when the current KEK is missing or shorter than 32 bytes. A row whose `key_id` has no file
  fails to decrypt with the category `key_unavailable` (logged with the `key_id`, never a key).
- **Rotation:** `python -m classbeat keys rewrap` re-wraps every DEK under the current KEK; the
  payload ciphertext is untouched. It runs per owner (ADR 0002 §4: list `users.id`, set
  `app.user_id`, process that owner's rows as `classbeat_app`), so it needs no RLS bypass. It
  prints counts per `key_id` only. After it reports zero rows on an old `key_id`, the old file
  can be deleted.
- **TOTP:** #65 moves `mfa_totp` secrets onto the same primitive (DEK per row wrapped by the
  KEK, AAD `classbeat-totp-v2|<user_id>`, `key_id` set), migrated by the same CLI with
  `--totp`, which runs inside `classbeat.auth` (guard 8). Rows with `key_id = "app-v1"` keep
  decrypting under ADR 0003 §8 until migrated.

### 3. From the row to the library

- `classbeat.connections` is the only module that decrypts. Its repository takes a
  `UserContext` and returns a `mowl.Credentials` (from `mowl-workouts` v0.4.0, #64), never a
  dict or the plaintext fields on their own.
- `Credentials` and every wrapper have a redacting `__repr__`/`__str__`; they are not
  serializable to JSON by our code.
- Jobs (M3a) store only the connection id. The worker rebuilds `Credentials` from the job's
  `UserContext` when it runs, and drops it when the run ends. Credentials never go into job
  rows, job events, SSE, model input, tool results or error messages.
- **Token refresh** (Spotify, M4): the library's refresh callback writes the new tokens back
  under `SELECT … FOR UPDATE` on the row, with a new DEK; a refresh token that rotated
  replaces the old one in the same transaction.

### 4. Connect and disconnect

- **Recent auth** (ADR 0003 §6, step-up for users with TOTP) for connect, reconnect and
  disconnect.
- **MOWL connect:** `POST /api/connections/mowl` with email and password (JSON over HTTPS,
  CSRF-checked). The backend calls the library's sign-in, stores the token, and discards the
  password: it is never stored, logged, put in a URL, kept in an exception or sent to a model.
  Request bodies of this route are excluded from any request logging.
- **Rate limits** (ADR 0003 §9 mechanism): 5 connect attempts per user and 20 per IP per 15
  minutes, so the route can't be used to test passwords against MOWL.
- **Disconnect** deletes the row. Older backups still hold it wrapped, so it stays recoverable
  from them only with that backup's KEK, until the backup ages out or the KEK is retired.
  MOWL has no known revoke endpoint, so disconnect is local only; the page says classbeat
  stops using the token.
- **Account deletion** deletes the rows (cascade). Exports (M6) list connections without
  secrets.

### 5. Errors and status

The library's errors map to categories, stored in `last_error` and shown in plain words:
`invalid_credentials` (connect refused), `needs_reconnect` (401 on use; status flips to
`needs_reconnect`), `rate_limited` (429; honours `Retry-After`), `upstream_unavailable`
(5xx, timeouts) and `unknown`. Provider response bodies are not stored or logged.

### 6. Token lifetime

The MOWL token is opaque to classbeat. If it is a JWT with an `exp` claim, #66 records
`expires_at` from it (the claim only, never the token) and the documented lifetime is that
value's distance from issue. If it carries no expiry, the lifetime is learned from the first
401s: the connection flips to `needs_reconnect`, and `docs/status.md` lists an owner-authorized
check against the owner's account to measure it. Either way an expired token degrades to a
reconnect prompt, never to a failure that loses work.

### 7. Logging and redaction

- The JSON log formatter (ADR 0003 §10) drops or masks keys named `token`, `access_token`,
  `refresh_token`, `password`, `secret`, `authorization`, `cookie`, and anything under
  `public`/`secret` of a connection row.
- `mowl-workouts` and HTTP client loggers run at `WARNING` in classbeat; the library never logs
  headers or bodies (#64 checks it).
- Tests: caplog over connect, use, refresh and every error path shows no token or password;
  a `pg_dump` of a test database with a known fake token doesn't contain it (ciphertext only);
  `repr()` of `Credentials` and of the repository's results contains no secret.

## Alternatives

- **`pgcrypto` in the database:** the key would travel to the database in every query and
  sit in its logs and `pg_stat_statements`. Rejected.
- **One key for all rows, no envelope:** rotation would re-encrypt every payload, and one
  compromised value would expose the whole column. The roadmap asks for envelope encryption.
- **KEK derived from `app_secret_key` (HKDF):** couples rotating CSRF and device keys to
  re-wrapping every connection. Rejected for a separate file.
- **A KMS or Vault:** not available on Docker Desktop or the beta VPS. The KEK loader is the
  seam where one plugs in later (M6 or M7).
- **Storing the MOWL password to re-sign-in on expiry:** forbidden by AGENTS.md. Rejected.

## Consequences

- #65 adds the table, the module, the KEK file and the CLI; #66 adds MOWL connect and pins the
  `mowl-workouts` v0.4.0 wheel; #67 proves isolation with two concurrent users.
- Backups hold ciphertext only; restoring one needs the KEK files of its time, which are backed
  up separately (M6 restore drill).
- Losing the current KEK means every user reconnects; there is no recovery path by design.
- Threat model T40 to T44 and two accepted risks follow from this ADR.
