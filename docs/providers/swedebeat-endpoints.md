# Swedebeat endpoints classbeat calls (#1087)

Read-only reference, as the code stands on 2026-11-15. For why each call exists and the design
around it, see [ADR 0015](../adr/0015-swedebeat-connection.md) (the connection), [ADR 0016](../adr/0016-swedebeat-music.md)
(mixes and matching) and [`swedebeat-mixing-findings.md`](swedebeat-mixing-findings.md) (how the
app itself builds a mix). This doc lists only the calls classbeat makes and their exact shape, so a
later change (for example, routing the direct Swedebeat calls through a separate server app) has a
precise list to work from. No code or decision changes here.

Host and allowlist constants are in `backend/src/classbeat/egress.py`; the calls themselves are in
`connections/swedebeat_account.py`, `swedebeat_match/catalogue.py`, `operations/swedebeat.py`,
`design/swedebeat_music.py`, and the vendored `mowl-workouts` library (`swedebeat.py`, `api.py`).

## A. Direct Swedebeat calls

These two are the only calls classbeat sends to a Swedebeat host directly. Routing Swedebeat
through another server would mean replacing these two call sites and their host constants
(`SWEDEBEAT_WWW_HOST`, `SWEDEBEAT_CLOUD_HOST`, `SWEDEBEAT_CLOUD_ALLOWLIST`, and `SWEDEBEAT_WWW_HOST`'s
entry in `WEB_URLLIB_HOSTS`); nothing else below needs to change for that.

### 1. Sign in — `POST https://www.swedebeat.se/downloader/login.php`

- **Caller:** web process. `connections/swedebeat_account.py` (`connect_swedebeat`) calling the
  library's `swedebeat.login`.
- **Headers:** `Accept: application/json`, `Content-Type: application/json`,
  `User-Agent: SwedeBeat/2.1 okhttp/4.12.0`. No token (this call is how one is obtained).
- **Body (JSON):**

  ```json
  { "email": "<enc>", "password": "<enc>", "deviceid": "<enc>", "force": false }
  ```

  `<enc>` is the app's own `Utils.encodeString`: AES-CBC over `"SwdBeat<epoch>:%%:<value>"`
  (zero-padded to a 16-byte block), hex-encoded (lowercase), then Base64. It needs the app's AES
  key and iv (32-byte key, 16-byte iv), which are not in this repository (`swedebeat.py`'s
  `_app_keys`; the library reads them from an optional bundled file or `Credentials.swedebeat_app`).
  `device_id` is a stable 16-hex-character string generated once per user and reused on every
  later sign-in. `force: true` would take the session from another device, signing the phone app
  out; classbeat always sends `false` unless the user explicitly confirms otherwise.
- **Expected response (JSON):** `{"token": "...", "customerId": ..., "customerName": ...,
  "brand": ...}`. classbeat keeps `token` and `device_id` (stored encrypted, ADR 0004); the
  password is discarded. A missing `token` is read as a failure via `message` or `errorMessage`.

### 2. Catalogue search — `GET https://cloud.swedebeat.se/track/extendedsearch`

- **Caller:** worker only (never the web process). `swedebeat_match/catalogue.py` rebuilds the
  library's request and sends it with a dedicated, scoped client
  (`egress.swedebeat_cloud_async_client()`), one per job.
- **Headers:** `Accept: application/json`, `X-Auth-Token: <the user's Swedebeat token>`,
  `User-Agent: SwedeBeat/2.1 okhttp/4.12.0` (the app's own user agent; some endpoints reject a
  default one).
- **Query parameters:**
  - `q` = `(and+(term+field=songtitle+'<title>')(term+field=artist+'<artist>'))`, where `<title>`
    has version notes stripped first (`clean_title`: "- Remastered 2003", "(feat. X)", "- Radio
    Edit" and similar are dropped) and both values are encoded the way the app does (`'` becomes
    `’`, then URL-encoded).
  - `q.parser=structured`
  - `start=0`
  - `size=5`
- **Expected response:** a JSON body whose hits classbeat finds under any of `data`,
  `searchListResult`, `tracks`, `results` or `hits` (whichever wrapper is present). Per hit,
  classbeat's parser (`catalogue.py` `parse_hits`/`_hit`) reads only `title`, `artist.title` and
  `duration`; the library additionally keeps `reference`. A hit with no usable title or artist is
  dropped. classbeat scores the best hit's title similarity against the one it searched for and
  records `found` (similarity ≥ 0.85), `unsure` (a different-looking title by that artist), or
  `missing` (no hit).

## B. Swedebeat mixes — through MOWL, not Swedebeat

These calls never reach a Swedebeat host. They go to `https://api.mowl.com/`, authenticated with
the user's **MOWL** token (`X-Auth-Token`, a different token from Swedebeat's). Moving the direct
Swedebeat calls (A) behind another server has no bearing on these: MOWL is the counterparty here,
not Swedebeat.

| # | Call | Caller | Parameters | Expected response |
|---|---|---|---|---|
| 3 | `POST v1/swedebeat/Playlists` | web (listing) and worker (re-listing before attach/analysis) | form `{"NameLike": ""}` | `{"Playlists": [{"ID", "Name", "URL", "DurationInMilliseconds"}, ...]}` — the user's own published mixes |
| 4 | `PUT v1/swedebeat/Playlist` | worker, in `attach_swedebeat_mix` (`operations/swedebeat.py`) | form `{"SwedebeatPlaylistID": "<id>"}` | the new MOWL playlist, including its `PlaylistID` |
| 5 | `GET v1/Playlists/{playlist_id}/tracks` | worker, polled after attach | — | `[{"IndexStatus", "IndexError", ...}]`; polled until `IndexStatus == "Track indexing done"` or `IndexError` is set |
| 6 | `GET v1/swedebeat/playlist/{playlist_id}/TemporaryLinks` | worker, in `design/swedebeat_music.py` | — | `{"TemporaryLinks": [{"TemporaryLink": "https://..."}]}` — a short-lived, pre-signed URL to the mix's MP3 |

Call 3 is rate limited per user and per IP (`auth.swedebeat_mixes_buckets`).

## C. Fetching the mix audio — S3, via the MOWL temporary link

### 7. `GET <TemporaryLink from call 6>`

- **Host:** `users-playlists.s3-accelerate.amazonaws.com` (the one host confirmed by the owner's
  live check, #524; `egress.py`'s `MOWL_MEDIA_HOSTS`). Not a Swedebeat host.
- **Caller:** worker, through a dedicated client (`egress.mowl_media_async_client()`) that follows
  no redirect and reaches only this allowlisted host.
- **Headers:** `User-Agent` only. **No token and no credentials are sent**: the temporary link
  itself is a bearer capability (MOWL's signed URL), and it is never logged.
- **Expected response:** `200`, the mix's MP3, capped at 250 MB and a 300 s budget. classbeat
  analyses it (songs, BPM, beats) and deletes the file afterward; nothing is kept beyond the
  derived analysis.

## Summary: what a Swedebeat proxy server would front

| Call | Host today | Direct to Swedebeat? |
|---|---|---|
| 1. Sign in | `www.swedebeat.se` | **Yes** |
| 2. Catalogue search | `cloud.swedebeat.se` | **Yes** |
| 3–6. Mixes (list, attach, index status, temporary link) | `api.mowl.com` | No — MOWL |
| 7. Mix audio fetch | `users-playlists.s3-accelerate.amazonaws.com` | No — S3, via MOWL's signed link |

Only calls 1 and 2 are classbeat-to-Swedebeat traffic. Calls 3–7 are classbeat-to-MOWL (and
MOWL's S3), and would be unaffected by replacing classbeat's own Swedebeat connection with a
server app in front of it.
