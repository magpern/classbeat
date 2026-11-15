# Swedebeat endpoints classbeat calls (#1087)

Read-only reference, as the code stands on 2026-11-15. classbeat no longer calls SwedeBeat
directly: [ADR 0035](../adr/0035-swedebeat-gateway.md) moved that to the owner's own **SwedeBeat
gateway** (`magpern/sb-gateway-service`), already merged to `main` before this doc was first
written. For why each call exists and the design around it, see ADR 0035 (the gateway),
[ADR 0015](../adr/0015-swedebeat-connection.md) (the connection, as amended by ADR 0035),
[ADR 0016](../adr/0016-swedebeat-music.md) (mixes and matching, as amended by ADR 0035) and
[`swedebeat-mixing-findings.md`](swedebeat-mixing-findings.md) (how the app itself builds a mix).
This doc lists only the calls classbeat makes and their exact shape. No code or decision changes
here.

The gateway's origin, allowlist policy and dedicated HTTP client are in
`backend/src/classbeat/egress_gateway.py` and `swedebeat_gateway/url_policy.py`; the three gateway
calls (`login`, `session`, `match`) are in `swedebeat_gateway/client.py`, called from
`connections/swedebeat_account.py` (login) and `swedebeat_match/gateway_match.py` (session, match).
The MOWL mix calls are in `operations/swedebeat.py`, `design/swedebeat_music.py`, and the vendored
`mowl-workouts` library (`mowl/api.py`; only `mowl.swedebeat.clean_title` is still used from that
library's SwedeBeat module — its `login`, `match_track` and the AES handshake are dead code from
classbeat's side).

## A. The SwedeBeat gateway — classbeat's only path to SwedeBeat

classbeat holds no SwedeBeat secret and sends no request to a SwedeBeat host. These three calls go
to the gateway's one configured origin (`CLASSBEAT_SWEDEBEAT_GATEWAY_URL`; production is
`http://192.168.1.222:7355`, plain HTTP to a private LAN address — the owner's accepted risk, ADR
0035 §1) with a client key in `X-API-Key` (`swedebeat_gateway_key`, the Docker secret mounted on
web and worker only). The gateway itself holds the SwedeBeat AES key, talks to SwedeBeat, and
answers classbeat in a shape of its own choosing.

### 1. Sign in — `POST {gateway}/auth/login`

- **Caller:** web process. `connections/swedebeat_account.py` (`connect_swedebeat` → `_login`),
  through `swedebeat_gateway/client.py`'s `login`.
- **Headers:** `Accept: application/json`, `Content-Type: application/json`, `X-API-Key: <client
  key>`. No bearer (this call is how one is obtained).
- **Body (JSON):** `{"email", "password", "device_id", "force": true}`.
  - `device_id` is the user's stable 16-hex-character id (`device_id_for`: HMAC-SHA256 of the
    user id under an app secret, never stored, derived fresh on every connect).
  - `force` is always `true`: the gateway always signs the account out of the SwedeBeat phone
    app on login, and classbeat refuses to send the request at all unless the user ticked the
    sign-out consent checkbox (`SwedebeatRefusal("sign_out_required")`, nothing sent).
- **Expected response (JSON):** `{"bearer", "token_type", "expires_in", "customer_id"}`. classbeat
  keeps only `bearer` (16–4096 printable ASCII characters) and `expires_in`; `token_type` and
  `customer_id` are read but discarded. The password is discarded; the bearer is stored encrypted
  (ADR 0004) as `{"bearer": "..."}`, never as library credentials (`build_credentials` refuses
  `swedebeat`).
- **Failure mapping:** a `401` with `detail == "unknown client"` (bad/missing client key) or a
  `403` is classbeat's own configuration problem (`app_key_unavailable`), never the user's
  password, and nothing is stored. Any other `401` is `invalid_credentials`. `429` is
  `rate_limited` with `Retry-After`. `502`/`5xx`/timeout is `upstream_unavailable`.

### 2. Session check — `GET {gateway}/auth/session`

- **Caller:** worker only, from `swedebeat_match/gateway_match.py` (`_Match.confirm`), used only
  to disambiguate an otherwise-ambiguous `401` from call 3 before deciding whether to ask the user
  to reconnect (ADR 0035 §5).
- **Headers:** `Accept: application/json`, `X-API-Key: <client key>`,
  `Authorization: Bearer <the user's stored bearer>`.
- **Body:** none.
- **Expected response:** `200` (the bearer is still good; the match's own failure is treated as a
  transient `upstream_unavailable` and retried, the connection untouched) or `401` (the bearer is
  genuinely refused; the connection flips to `needs_reconnect`).

### 3. Catalogue match — `POST {gateway}/catalog/match`

- **Caller:** worker only, from `swedebeat_match/gateway_match.py` (`_Match.call`), one call per
  match job (not one per song).
- **Headers:** `Accept: application/json`, `Content-Type: application/json`,
  `X-API-Key: <client key>`, `Authorization: Bearer <the user's stored bearer>`.
- **Body (JSON):** `{"tracks": [{"title", "artist"}, ...], "include_unsure": false}` — the class's
  songs in play order, deduplicated in job memory (`dedupe_key`: cleaned title + artist,
  case-folded), at most `MAX_SONGS` (40, under the gateway's own 200). Only each song's title and
  artist leave classbeat: no Spotify id, BPM, length, role, class, brief or other user detail. A
  song with an empty title or artist after trimming is not sent (treated as `missing`).
- **Expected response (JSON):** `{"items": [{"title", "artist", "status": "found"|"unsure"|
  "missing", "score", "track": {"reference", "title", "artist", "bpm", "duration"} | null}, ...]}`,
  **exactly one item per sent track, in the same order**. classbeat reads only `status`, `score`
  and `track.title`/`track.artist`/`track.duration`; `reference`, `bpm` and everything else are
  dropped by the parser and never fetched. classbeat applies its own 0.85 cut on top of the
  gateway's `found` (0.78): `found` only when the gateway says `found` **and** `score ≥ 0.85`;
  `unsure` when the gateway says `found` below 0.85 or says `unsure` itself; `missing` otherwise.
  If every sent track comes back `missing` and at least 3 distinct tracks were sent, the call is
  retried once after a 5 s pause before the result is accepted (a SwedeBeat outage can look like
  "all missing").
- **Failure mapping:** `401`/`403` as call 1 (`unknown client` → `app_misconfigured`, connection
  untouched; any other `401` → the session check of call 2, then `needs_reconnect` or
  `upstream_unavailable`). `429` → `rate_limited`. `502`/`5xx`/timeout → `upstream_unavailable`
  (retried). A bad body, wrong item count, `413`/`422` or a redirect → `invalid_response`.

## B. Swedebeat mixes — through MOWL, not SwedeBeat or the gateway

These calls never reach SwedeBeat or the gateway. They go to `https://api.mowl.com/`,
authenticated with the user's **MOWL** token (header `authorization`, a different token from the
SwedeBeat bearer of section A). This part of the integration is unchanged by ADR 0035: MOWL is the
counterparty here, not SwedeBeat.

| # | Call | Caller | Parameters | Expected response |
|---|---|---|---|---|
| 4 | `POST v1/swedebeat/Playlists` | web (listing) and worker (re-listing before attach/analysis) | form `{"NameLike": ""}` | `{"Playlists": [{"ID", "Name", "URL", "DurationInMilliseconds"}, ...]}` — the user's own published mixes |
| 5 | `PUT v1/swedebeat/Playlist` | worker, in `attach_swedebeat_mix` (`operations/swedebeat.py`) | form `{"SwedebeatPlaylistID": "<id>"}` | the new MOWL playlist, including its `PlaylistID` |
| 6 | `GET v1/Playlists/{playlist_id}/tracks` | worker, polled after attach | — | `[{"IndexStatus", "IndexError", ...}]`; polled until `IndexStatus == "Track indexing done"` or `IndexError` is set |
| 7 | `GET v1/swedebeat/playlist/{playlist_id}/TemporaryLinks` | worker, in `design/swedebeat_music.py` | — | `{"TemporaryLinks": [{"TemporaryLink": "https://..."}]}` — a short-lived, pre-signed URL to the mix's MP3 |

Call 4 is rate limited per user and per IP (`auth.swedebeat_mixes_buckets`).

## C. Fetching the mix audio — S3, via the MOWL temporary link

### 8. `GET <TemporaryLink from call 7>`

- **Host:** `users-playlists.s3-accelerate.amazonaws.com` (the one host confirmed by the owner's
  live check, #524; `egress.py`'s `MOWL_MEDIA_HOSTS`). Not a Swedebeat host.
- **Caller:** worker, through a dedicated client (`egress.mowl_media_async_client()`) that follows
  no redirect and reaches only this allowlisted host.
- **Headers:** `User-Agent` only. **No token and no credentials are sent**: the temporary link
  itself is a bearer capability (MOWL's signed URL), and it is never logged.
- **Expected response:** `200`, the mix's MP3, capped at 250 MB and a 300 s budget. classbeat
  analyses it (songs, BPM, beats) and deletes the file afterward; nothing is kept beyond the
  derived analysis.

## Summary: who classbeat actually talks to

| Call | Host today | Counterparty |
|---|---|---|
| 1. Sign in | the configured gateway origin (prod: `192.168.1.222:7355`) | SwedeBeat gateway (owner's own server; it, not classbeat, talks to SwedeBeat) |
| 2. Session check | the gateway | SwedeBeat gateway |
| 3. Catalogue match | the gateway | SwedeBeat gateway |
| 4–7. Mixes (list, attach, index status, temporary link) | `api.mowl.com` | MOWL |
| 8. Mix audio fetch | `users-playlists.s3-accelerate.amazonaws.com` | S3, via MOWL's signed link |

classbeat makes **no** request to a SwedeBeat host and holds no SwedeBeat secret: calls 1–3 go
only to the gateway, which is itself a separate server app in front of SwedeBeat (ADR 0035) — the
owner already built the kind of intermediary this doc was originally meant to help design. Calls
4–8 are classbeat-to-MOWL (and MOWL's S3) and are unrelated to SwedeBeat connectivity.
