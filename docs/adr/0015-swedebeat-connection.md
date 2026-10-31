# ADR 0015: The Swedebeat connection (S1)

Status: Accepted (the owner's decisions of 2026-10-31, issue #362, are its premise; reviewed with
the code in that PR).

Related: [ADR 0004](0004-connections-and-encryption.md) (connections and encryption),
[ADR 0009](0009-spotify-connection.md) (the connect wizard's style), [ADR 0010](0010-live-activation-permissions.md)
(permissions), [ADR 0011](0011-tiers-quotas-and-admin.md) §7 (flags), [ADR 0013](0013-ui-design-night-studio.md)
(the account page), the threat model (T19, T23, T94, T102 to T105 and the accepted risks).

## Context

Swedebeat is the music service many gyms use; gym instructors (SATS employees among them) teach with
its licensed catalogue, not with Spotify audio. The `mowl-workouts` plugin signs a user in to
Swedebeat once (`mowl.swedebeat.login`, `docs/setup.md` §4: `mowl connect swedebeat`) and keeps a
token and a device id (`Credentials.swedebeat_token`, `swedebeat_device_id`); the password is never
stored. Until now classbeat had Swedebeat only as a column value (`connections.provider` allows
`swedebeat`), a read-only "off" line in the admin area (ADR 0011 §8) and "Coming later" on the account
page, because Swedebeat's sign-in needs Swedebeat's own AES app key and AGENTS.md forbids using
credentials embedded in third-party apps.

Owner decisions of 2026-10-31:

1. **Swedebeat matters** and must exist, though it stays optional per user and nothing else in
   classbeat may require it. It is built and verified against fakes; agents make no live call.
2. **The connection works like the plugin's.** The first issue (S1 T1, #362) is the connection only:
   connect, status, disconnect. What a connection is then used for (matching a Spotify selection to the
   Swedebeat catalogue, as the plugin does on the phone) is later work, and may run in the worker.
3. **The app key is used and baked into the production image at build time** from a GitHub secret
   named `SWEDEBEAT_APP` (`{"key": 32 characters, "iv": 16 characters}`), the name and shape the
   `mowl-workouts` release workflow uses. This supersedes the earlier "disabled flag until Swedebeat
   authorizes server-side use" (threat model, accepted risks) and the first wording of #362 ("an optional
   runtime secret file only"). The owner creates the secret; no agent creates, reads or asks for it.

## Decision

### 1. The flow

`POST /api/connections/swedebeat {email, password, force?}` → the library's `swedebeat.login` → token
and device id → envelope-encrypted per user with `classbeat.connections.save` → the password is
discarded. It mirrors MOWL connect (ADR 0004 §4, `mowl_account`): the sign-in runs first inside
`run_library_call` (outside any transaction; the library's exception never leaves, the
`ConnectionFailure` has no `__cause__` or `__context__`), then `save` takes the row lock and updates
or inserts, keeping the row id across reconnects and raising `generation` on each. The route needs
recent authentication and, with TOTP, a step-up; CSRF by the middleware; rate limits per user, IP and
normalized Swedebeat email, 5 per 15 minutes for the email (the `connect_buckets("swedebeat", ...)`
buckets, T43), a success releases the slots. Events `connection_connected`, `connection_failed`
(provider and category only) and `connection_disconnected` go to `auth_events`.

- **Status** is the `swedebeat` row of `GET /api/connections` (`connected` or `needs_reconnect`, last
  error category, dates) plus `account`, the Swedebeat email, and a `swedebeat` block `{enabled,
  available}` that tells the page whether to show the form (§5, §6). Nothing secret is returned.
- **`needs_reconnect`** works as for MOWL: `connections.use` flips the row on a 401 or 403 from
  Swedebeat (`last_error = needs_reconnect`), no provider call is made while it needs one, and a
  reconnect (the same route) keeps the row id and the device id.
- **Disconnect** is `DELETE /api/connections/swedebeat` (recent authentication, step-up): deletes the
  row, local only (Swedebeat has no known revoke endpoint), 204 whether or not a row existed. It is
  never gated by the flag: a user can always remove what is stored about them. Account deletion and
  the GDPR export treat the row as before (cascade; the export shows status only, ADR 0014).
- **Stored:** the secret payload is `{token, device_id}` (ADR 0004 §3, unchanged). `public` holds the
  Swedebeat **email only**, so the page can say whose account it is. It is personal data kept in the
  clear, like the Spotify user id; it is never logged (the log filter masks `public`), never in an
  event, and goes with the row. Never the password.
- Idempotent: repeating a connect updates the same row; two parallel connects end with one row (the
  `UNIQUE (owner_id, provider)` retry of `save`).

### 2. One stable device id per user

Swedebeat tells devices apart by the `deviceid` the app sends; the plugin makes a random one and
keeps it. classbeat derives it: `HMAC-SHA256(app_secret_key, "classbeat/swedebeat-device/v1\0" +
user_id)`, the first 16 hex characters (the library's own length). It is the same on every connect,
reconnect and after a disconnect, so a reconnect is the same "device" to Swedebeat; two users never
share one; it needs no extra column. It is stored in the secret payload like the plugin's. Rotating
`app_secret_key` would change it; that rotation signs everyone out anyway (ADR 0003 §8).

### 3. `force` is the user's choice

The library's `login(force=False)` refuses when another device holds the Swedebeat session, and
`force=True` takes it, which can sign the user's phone out. classbeat sends `force=False` unless the
request says `"force": true`, which the page sends only after it showed "your account is signed in on
another device ... that device is signed out" and the user chose "Sign in here anyway" (the password is
typed again; it is never kept on the page).

### 4. The app key

Swedebeat's AES key and iv encode the login fields (`SwdBeat<epoch>:%%:<value>`, AES-CBC, hex, Base64).
They are Swedebeat's, not classbeat's.

- **Where it comes from, in this order** (`Settings.swedebeat_keys()`): (a) the optional runtime secret
  file `swedebeat_app` (JSON `{"key", "iv"}`, a Docker secret like the others, **web only**, never the
  environment; an empty file is no key; the home server mounts it without a rebuild), (b) the file
  `/app/swedebeat_app.json` (`CLASSBEAT_SWEDEBEAT_APP_FILE`) that a release image holds. Both are
  validated (a JSON object with exactly `key` of 32 and `iv` of 16 single-byte characters; a malformed
  secret file refuses the start without echoing it, a malformed image file is no key). The key is
  passed explicitly to the library through `Credentials.swedebeat_app` for the one sign-in; the
  installed wheel is not modified, and its `_swedebeat_app.json` stays absent (ADR 0009 §12's
  wheel check, `scripts/build-mowl-wheel.sh` and `test_the_vendored_wheel_has_no_swedebeat_key` stay
  true: **the image adds the key, the wheel does not**).
- **The production image holds it.** Only `.github/workflows/release.yml` (tags `v*.*.*`) passes it:
  a step checks the repository secret `SWEDEBEAT_APP` (present, JSON, 32 and 16 characters; it fails
  the release otherwise and prints nothing), and `docker/build-push-action` hands it to BuildKit as a
  secret (`secret-envs: swedebeat_app=SWEDEBEAT_APP`, `build-args: REQUIRE_SWEDEBEAT_APP=1`). One
  `RUN --mount=type=secret,id=swedebeat_app` in the `Dockerfile` validates it again and installs it
  as `/app/swedebeat_app.json` (`0444`); with `REQUIRE_SWEDEBEAT_APP=1` a missing or malformed secret
  fails the build. It is no `ARG`, `ENV` or `COPY`, and the release build exports and imports no layer
  cache (a cache would copy the layer into the Actions cache).
- **No other image has it.** Pull-request, CI, merge-queue and e2e builds pass no secret: the file is
  absent and connect answers `app_key_unavailable`. The e2e stack uses the **public TEST key**
  `e2e/fake-swedebeat/app.json` as its `swedebeat_app` secret; the fake decodes with it to prove the
  app encodes correctly. `backend/tests/test_swedebeat_key_guard.py` pins all of this (ignored by git
  and Docker, no tracked file shaped like a key but the test key, the Dockerfile's one mount, the
  release workflow the only holder, no cache, web the only container with the secret, off by default),
  CI checks the PR image has no key file and only web mounts the secret, and the M6 secrecy tests
  search every log, response and database dump for the key and iv.
- **Absent** (no secret, no image file): connect answers 503 `app_key_unavailable` before the rate limit is
  spent and before anything is sent; the page says "Swedebeat sign-in is not available on this
  server". It never crashes the app.
- **Who can pull the image:** GHCR packages stay private (AGENTS.md); anyone who can pull a release image can
  read the key. That is the owner's risk acceptance (threat model, T102).
- **Rotation / leak:** replace the GitHub secret (§9, step 1), cut a new release, deploy it, and delete
  the old image tags from GHCR; a runtime `swedebeat_app` secret file overrides an image's key at once
  without a rebuild. Users need not reconnect unless Swedebeat invalidates tokens.
- The worker never gets the key: it does not sign anyone in, `swedebeat_app` is not in its secrets, and
  the worker refuses to start when it can read the file (`check_worker_has_no_swedebeat_app`). The file
  in the shared image exists on the worker's filesystem but nothing there reads it.

### 5. Error categories

Plain words in the UI; the body carries the category alone (`{"detail": {"reason": ...}}`).

| Category | Status | When | UI |
|---|---|---|---|
| `invalid_credentials` | 400 | Swedebeat refused the login (wrong email or password, any refusal that is not "device in use") or answered 401/403 | "Swedebeat did not accept that email and password." |
| `device_in_use` | 409 | Swedebeat says another device holds the session and `force` is off; nothing is stored | asks before signing the other device out |
| `upstream_unavailable` | 503 | Swedebeat answered 5xx, timed out or was unreachable | "could not be reached. Try again in a few minutes." |
| `app_key_unavailable` | 503 | this server has no app key (§4); nothing sent, no slot spent | "Swedebeat sign-in is not available on this server." |
| `rate_limited` | 429 | Swedebeat's 429 (with `Retry-After`) or classbeat's connect limit | "Try again in N minutes." |
| `feature_off` | 404 | the effective flag is off (§6) | the card says "coming soon" |
| `unknown` | 502 | anything else | "answered in a way classbeat did not expect" |

`device_in_use` and `app_key_unavailable` are **not** `ConnectionFailure` categories: that set is
shared with the checks of `connections.last_error` and the jobs (migrations 0003, 0004, 0006), and
these two are never stored on a row (a failed connect writes nothing). They are `SwedebeatRefusal`
reasons of `classbeat.connections.swedebeat_account`. Decided in the route from the category alone.

**The one decision from a message.** The library turns a refusal that arrives as HTTP 200 into a
`MowlError` with Swedebeat's message. "Another device holds the session" cannot be told from a wrong
password without it, so `_sign_in` reads the message once, inside the worker thread, against a short
fixed list of lower-case phrases (`another device`, `other device`, `already logged`, `already
signed`, `in use`) and only when there is no HTTP cause (an HTTP 409 counts as well). The message is
never stored, logged or returned, and the answer leaves the thread as a plain return value, so nothing
is chained. **Any other message is `invalid_credentials`**, the safe reading: no force, no kick. The
real wording is unverified (no live call was made): it is an owner step at live activation (§9), and a
mismatch only makes a taken session look like a refused login.

### 6. The flag `swedebeat_enabled`

A real admin flag with the song-picking shape (ADR 0011 §7), **stricter wins, default off**:

- Environment: `CLASSBEAT_SWEDEBEAT_ENABLED` (compose `SWEDEBEAT_ENABLED`, default `false`, web only), the
  kill switch. Off, the connect route answers 404 `feature_off` whatever the database says.
- Database: `app_settings` key `swedebeat_enabled`, stored `off` or `on` (migration `0016_swedebeat_flag`
  adds the key to `app_settings_key`). Absent means `on`, but only under the environment's switch; the
  database can switch Swedebeat off for everyone and never on past the environment. A stored value that
  does not validate reads as `off` (the stricter reading) and is logged by key.
- The admin Settings page shows it as a flag with its environment and effective value, an "Everyone / Nobody"
  switch and "Use the environment's value", all audited like the other flags; it is no longer in the
  read-only list. Admin writes stay behind TOTP and a step-up.
- The account card: flag off → "Swedebeat is coming soon." (and the disconnect button for a user who
  has a connection); on and the key present → the connect form; on and no key → "Swedebeat sign-in is not
  available on this server."

This supersedes ADR 0011 §8 ("no feature exists to switch") and §7's "shown, read-only and off" for
Swedebeat only; `billing` is unchanged.

### 7. Outbound

The web process signs a user in over `urllib` (the library), so `www.swedebeat.se` joins
`WEB_URLLIB_HOSTS` (https, port 443, same-host redirects only: the existing opener, ADR 0006 §9, T19).
`cloud.swedebeat.se` (the catalogue; the token is sent there) is on **no** list: nothing in classbeat
calls it yet, and a feature that does must add it to the list of the process that calls it with its
own redirect tests. The sign-in sends the user's AES-encoded email and password and no token. The
worker's list is unchanged.

### 8. The account page

`Account > Connections > Swedebeat` (ADR 0013): the "Coming later" text becomes a card: the connect
form (email, password with `autocomplete="current-password"`, "Connect Swedebeat"), the connected state
(status, the Swedebeat email, since when, the last problem in words, "Disconnect Swedebeat" with an
explicit confirmation), a reconnect form when the connection needs one, and the
`device_in_use` question. The password goes in the JSON body only: never a URL, `localStorage` or
`sessionStorage`; the field is cleared after every attempt. Errors are `role="alert"`; every control
has a label and a 44 px target.

### 9. Out of scope, and the owner's steps

Not in this ADR: any use of the token (catalogue matching, the Swedebeat part of a class, a job kind),
the Swedebeat token's lifetime (unknown, as MOWL's was: `expires_at` stays `NULL`), a Swedebeat revoke
call, and any live call. Agents run none (AGENTS.md safety envelope).

**Owner steps** (also in [the runbook](../runbooks/swedebeat-connect.md)):

1. Create the repository secret once; the shell reads the key file, nothing is printed or echoed:
   `gh secret set SWEDEBEAT_APP --repo magpern/classbeat < swedebeat_app.json`
   (the file holds `{"key": "<32 characters>", "iv": "<16 characters>"}`, the same content as the
   `mowl-workouts` repository's `SWEDEBEAT_APP`).
2. Cut a release (AGENTS.md, "Releasing"); a release without the secret fails at the key check.
3. Deploy it, set `SWEDEBEAT_ENABLED=true` for web, and (home server) optionally mount the key as the
   `swedebeat_app` secret instead of rebuilding.
4. Authorize one live check with the owner's own Swedebeat account: connect, see the status, read
   the real "another device" wording against §5, disconnect. Logged under "Live activation log".

## Alternatives

- **Runtime secret only, no key in the image** (the first wording of #362): the owner's decision is
  the image, so deployments need no extra file; the runtime file stays as an override.
- **Patching the installed wheel** with `_swedebeat_app.json` (what `mowl-workouts` does): changes a
  pinned, hash-locked artifact in the image and makes the ADR 0009 wheel check say less; passing the key
  through `Credentials.swedebeat_app` needs no change to it.
- **A new connection category for `device_in_use`:** would need a migration of three check constraints
  for a value never stored.
- **A random device id stored per user:** needs the previous id on every reconnect, which a
  `needs_reconnect` row cannot open; a keyed hash needs no state and survives a disconnect.
- **Both Swedebeat hosts on the web list** (as #362 first said): the connect route needs only `www`;
  the catalogue host is added by the feature that calls it.

## Consequences

- A release image contains Swedebeat's key and anyone who can pull it can read it; PR, CI and e2e images
  never do. Rotation needs a new secret and release.
- A new migration (`0016_swedebeat_flag`), one table row kind, three routes' worth of surface
  (POST, DELETE, and the extended GET), one setting, one secret.
- Swedebeat's real wording and token lifetime are unknown until the owner's live check.
- `GET /api/connections` has a new top-level `swedebeat` block and a per-row `account`.
- Threat model: T19 and T94 updated, T102 to T105 new, the Swedebeat accepted risk rewritten.
