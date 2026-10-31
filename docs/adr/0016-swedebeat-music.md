# ADR 0016: Swedebeat music: "My Swedebeat mix" and Spotify-to-Swedebeat matching (S2)

Status: Proposed (issue #363; the owner's decisions of 2026-10-31 are its premise).

Related: [ADR 0005](0005-jobs-and-leases.md) (jobs, external operations), [ADR 0006](0006-agent-runs.md)
(the music step, model input), [ADR 0008](0008-song-picking.md) (song picking, `song_list`, retention),
[ADR 0010](0010-live-activation-permissions.md) (Spotify-derived data, live gate),
[ADR 0011](0011-tiers-quotas-and-admin.md) §7 (flags), [ADR 0013](0013-ui-design-night-studio.md) (UI),
[ADR 0015](0015-swedebeat-connection.md) (the Swedebeat connection, merged in #366), the threat model
(T19, T104 to T107; new T108 to T111).

## Context

Swedebeat is mission critical for instructors at a gym chain (its licensed catalogue is what may be
played in the room; Spotify audio may not) and irrelevant to everyone else. S1 (ADR 0015) gave classbeat
the Swedebeat connection and nothing that uses it. #363 is what the connection is for. Two things are
asked, both modelled on the `mowl-workouts` library (`docs/mcp-tools.md` "workflow: Swedebeat music from
a Spotify selection", `docs/music-sync.md`, `swedebeat.py`, `library.py`, `service.py`):

- **(a) "My Swedebeat mix"**: design a class on a mix the user already has in Swedebeat. No song picking
  (ADR 0008): the mix is the music.
- **(b) The gym workflow**: the user picks songs from Spotify (ADR 0008), they are matched against the
  Swedebeat catalogue, the user sees matched and unmatched songs, and, because Swedebeat stitches a mix
  only inside its own app, the user builds the mix there by hand; classbeat then finds it, checks it and
  designs on it.

Facts from the library that decide the shape:

1. **Mixes are listed, attached and analysed through MOWL, not through Swedebeat.** `api.swedebeat_mixes`
   (`POST v1/swedebeat/Playlists`) lists the user's mixes, `api.attach_swedebeat` (`PUT
   v1/swedebeat/Playlist`) wraps one in the user's MOWL account, and `download_swedebeat_audio` fetches
   the mix's MP3 through a **temporary link** MOWL returns (`v1/swedebeat/playlist/{id}/TemporaryLinks`);
   `music.analyze_file` turns it into a music map (songs, BPM, beat 1, drops, breakdowns) and the audio
   is deleted. A Swedebeat mix is one long MP3 with no song markers and MOWL's own loudness curve is too
   coarse (about 2.5 s), so the audio analysis cannot be skipped. **(a) therefore needs the user's MOWL
   connection and no Swedebeat token.** The temporary link points at a host that is not `api.mowl.com`
   and that nothing in classbeat or the library docs names (§4).
2. **Catalogue matching uses the Swedebeat token** and `cloud.swedebeat.se/track/extendedsearch`
   (`match_track`): one request per track, `found` (title similarity at least 0.85 once version notes are
   dropped), `unsure` (a different-looking title by that artist) or `missing`, plus the found tracks'
   total duration (`swedebeat_sec`), because Swedebeat's masters differ in length from Spotify's.
3. **There is no sanctioned API to create a mix.** The library's docs say a mix is built only inside the
   Swedebeat app. The owner confirms: that step is manual.
4. A built mix is checked by length and song count only (a user's mix track list cannot be read outside
   the app): its length should be near `swedebeat_sec` minus about 10 s per song change.

Owner facts honoured: Swedebeat is optional for everyone; everything is built against fakes and agents
make no live call; creating the mix is manual; ADR 0010's rules for Spotify-derived data apply to
matching; everything is behind `swedebeat_enabled`; Swedebeat's embedded AWS credentials are never used;
the web `urllib` allowlist gains only `www.swedebeat.se` (already done in #366).

## Decision

### 1. Scope, and what is split

- **(a) is in, as the first set of issues (A1 to A6, §9).**
- **(b) is split from (a)** into its own issues (B1 to B5), built after (a) but under the same tracking
  issue (#363 stays open as the tracker; every piece is its own PR). Reasons: (a) needs only MOWL and one
  extra worker host and delivers a usable feature to an instructor who already has mixes; (b) adds a
  second Swedebeat host, sends Spotify-derived titles to a third party, adds a revision kind and a
  stateful manual hand-off. Different risk, different review, and (b) must not hold (a) back.
- Out of scope for both: creating or editing a Swedebeat mix by any means (no API call, no scripted
  app); reading a user's mix track list; a Swedebeat revoke or token-refresh call; playing audio;
  sharing a mix or an analysis between users; correcting beat 1 (the library's `correct_beat_one`, a
  later issue); any live call.

### 2. The flag and who sees what

Everything below is behind the effective `swedebeat_enabled` flag (ADR 0015 §6, stricter wins, default
off). Off: the builder is exactly what it is today, every new route is 404 `feature_off` before any
database or provider work, the new job kinds refuse to be created (`admit` → `feature_off`), and a
queued job of these kinds ends `feature_off`, calling nothing. Nothing in M1 to M6 (classes, song
picking, Save to MOWL) reads or requires a Swedebeat row, table, host or flag: a user with no Swedebeat
sees no difference, and a test pins it (§8).

| Feature | MOWL connection | Swedebeat connection | Spotify connection |
|---|---|---|---|
| (a) design on a Swedebeat mix | yes | **no** (list, attach and audio all go through MOWL) | no |
| (b) match a selection | yes | yes (the catalogue token) | yes (the selection comes from song picking) |

If MOWL lists no mix, the page says so and points at the Swedebeat app; it never offers to create one.

### 3. (a) "My Swedebeat mix" in the guided builder

`MusicChoice` (ADR 0008 §9: "Use my playlist" / "Pick songs for me") gets a third choice, **"Use my
Swedebeat mix"**, shown only when the flag is on and MOWL is connected (with the flag off, nothing; with
the flag on and no MOWL, a hint linking to the MOWL connection). Choosing it shows a picker (name,
length, "analysed before" badge) fed by `GET /api/music/swedebeat-mixes`, shaped like `GET
/api/music/mowl-playlists`: one library read (`POST v1/swedebeat/Playlists`) in the web process through
the existing MOWL-only opener, read-only, rate limited per user. No new web host.

Design reuses `design_draft` with a new music source next to `mowl_spotify_playlist` (ADR 0006 §3):
`music: {source: "mowl_swedebeat_mix", mix_id}`. `mix_id` is the opaque `ID` from the listing; `admit`
re-lists the user's mixes and refuses (`invalid_input`) an id that is not in the user's own listing, so
another user's id, a made-up id or a URL is never attached or fetched (T19, T108). The music step's
branch is a job step, so ADR 0005 checkpoints, fencing, budget and the connection guard of
`design/music.py` apply unchanged:

1. **Attach** (external operation `mowl_attach_swedebeat_mix`, ADR 0005): `PUT v1/swedebeat/Playlist`,
   then wait for MOWL to index it. It writes a playlist into the user's MOWL account, as adding the mix
   in the MOWL app would. Reconciled by listing the user's MOWL playlists for the mix (a repeated attach
   for an existing link is a no-op). The link is stored like `mowl_playlist_links` and deleted when the
   MOWL connection changes account.
2. **Analyse** (limits, A3): the decoded length is capped at the mix's listed length plus 5 minutes and never over 120 minutes; decode and analysis run in a child process limited to 1.5 GiB of memory and 180 s, so a crash, an OOM or a hostile file ends the job cleanly as `analysis_failed` and the worker lives on; the fake-mix test asserts peak RSS under that limit. If the user's cache has a map for this mix id and MOWL playlist id, use it; otherwise
   fetch the audio (§4), run the library's analysis in a worker thread with checkpoints, delete the audio
   at once, keep the map. The first analysis takes about a minute.
3. **Design** with the map exactly as for a MOWL playlist: stages, snapping and quality report are the
   library's; its warnings carry into the revision (listed length differs from the audio by more than
   60 s, songs whose beat-1 margin is below 1.3, a workout longer than the music). Nothing from the mix
   goes to the model beyond what the `model_input` allowlist already passes for a MOWL playlist (song
   index, BPM, section times; no free text); the mix name is shown to the user only.

**Stored**: one owned table `swedebeat_mix_maps(owner_id, mix_id, mowl_connection_id, playlist_id, name,
duration, listed_duration, map jsonb, created_at)`, unique per `(owner_id, mix_id)`, RLS by owner (ADR
0002), at most 20 per user (oldest dropped), 30 days, deleted with the account, **Disconnect MOWL** and a
MOWL account change, and by a per-row "forget this analysis" action. It is the user's own analysis of
audio Swedebeat licensed to them: no audio, nothing Spotify-derived (so ADR 0010's retention rules do not
bind it), but it is registered in the retention registry and the GDPR export shows counts only. **It is
never shared between users** (the shared-analysis machinery of ADR 0007 §11 stays unbuilt). The class
revision's `music` records `{source: "mowl_swedebeat_mix", mix_id, name, duration, fingerprint}`; the
fingerprint (hash of the map) lets a revise notice a re-analysis, as for Spotify.

A class on a Swedebeat mix keeps using `revise_draft`; changing its music is a new class (as for an own
MOWL playlist, ADR 0008 §6). Credit: the design job consumes credit like any design (ADR 0007 §3); the
attach and analysis are inside it.

### 4. Hosts: what the worker needs, and why

| Host | Process | Needed by | Decision |
|---|---|---|---|
| `www.swedebeat.se` | web | S1 sign-in | already there; unchanged |
| `api.mowl.com` | web, worker | list, attach, playlist reads | already there; unchanged |
| the **MOWL temporary-link media host** | worker only (own client) | (a): fetching the mix's MP3 | added by A1/A3 as an exact-host constant, see below |
| `cloud.swedebeat.se` | worker only (own client) | (b): the catalogue search with the Swedebeat token | **added by B1, not before** |

Nothing else. The web list gains no host: (a)'s list route is MOWL-only; the audio fetch and the
catalogue search are worker work (long, budgeted, checkpointed), so the Swedebeat token and the media link
never exist in the web process beyond S1's sign-in.

**The media host is unknown today.** The library follows whatever `TemporaryLink` MOWL returns; no repo or
doc names its host. classbeat does not guess and does not wildcard. The rule:

- The media host gets its own **scoped `Allowlist`** (like `SPOTIFY_ALLOWLIST`), built from
  `MOWL_MEDIA_HOSTS`, a code constant of exact hostnames (https, port 443; no wildcard, never a whole cloud
  provider domain such as `*.amazonaws.com`). It is **not** added to `WORKER_HOSTS`, and neither is
  `cloud.swedebeat.se` (B1): each has a scoped list and a **dedicated HTTP client** in `classbeat.egress`
  (the only module allowed to build one) that follows **no redirect at all** (the process-wide opener
  follows same-host redirects), sends only the headers the call site sets, and reaches only its own host.
  So the library's MOWL client, with its `X-Auth-Token`, can never reach the media host, and the media fetch
  and the catalogue search each reach one host, by construction. The library's `download_swedebeat_audio`
  is therefore not called through the global opener: classbeat reads the link from
  `v1/swedebeat/playlist/{id}/TemporaryLinks` through the MOWL call, fetches the file itself with that
  client, and hands it to the library's `music.analyze_file`.
- **How the list is filled, and how that is tested (no test-only setting).** The real host is unknown, so
  there is no real name for a fake to take over (as `compose.e2e.yaml` does for the other fakes), and the
  list is code: nothing in the environment can widen it (`egress.py`). The production constant is
  **empty until the owner confirms the host(s)** in the live check. Consequences:
  - *Backend tests* (A1, A3) build the dedicated client with an in-process `Allowlist` of the fake's
    loopback name, passed to the code under test as an argument (the pattern of
    `install_choice_allowlist`), so the fetch, its limits and the analysis are proven against a fake media
    endpoint. Production code never reads a list from the environment or a file; a guard test pins that
    `MOWL_MEDIA_HOSTS` is a literal frozenset and that the client factory has no other source.
  - *e2e* (A6) runs the unchanged production constant (empty) and proves **fail-closed**: a mix that is not
    in the cache ends `media_host_not_allowed` and the fake records zero media requests (the fake MOWL
    returns a link to an unlisted name), while a mix whose map is cached designs normally (the cache is
    seeded by a backend fixture through the same code path). The positive fetch is not in e2e; it is in
    the backend tests above and in the owner's live check.
  - With an empty list a mix analysis ends `media_host_not_allowed`: no request is made, the refused
    **hostname only** (never path, query or signature) is in the operator log, no response names it, and
    the page says analysis is not available on this server yet. A map already in a user's cache still works.
  - After the owner's PR adds the confirmed host, a test pins the constant to exactly those names.
- The owner step (§10) is one authorized live analysis with the owner's own accounts; the log line names
  the host; the owner adds it to the constant in a reviewed PR.
- The link is a **bearer capability**: it is fetched once, in the worker, with none of our headers (no
  `X-Auth-Token`); it is never logged (query string and signature included), never stored (job input,
  ADR 0005 checkpoints, job results and outputs, events, errors, `request_context`) and never returned. Redirects are refused. The response must be
  `200` and at most **250 MB** within a **300 s** budget (Content-Length and the streamed count are both
  checked); the content type is not trusted either way (CDN links often send `application/octet-stream`):
  the file is accepted only if the decoder opens it as audio. It is written to a private temp directory (0700) that is removed in a
  `finally` and again at worker start.
- **Swedebeat's embedded AWS credentials are never used.** The Swedebeat Android app carries AWS
  credentials; AGENTS.md forbids using credentials embedded in third-party apps. classbeat does not read
  them, ask for them, sign a request with them, or fetch from any S3 or CDN URL other than a MOWL
  temporary link on the exact list above. A static guard test (A1), beside `test_swedebeat_key_guard.py`,
  forbids `boto`, `botocore`, AWS signature headers and access-key shapes in the repository. Catalogue
  responses (B1) are parsed through a field allowlist (`reference`, `title`, `artist.title`, `duration`);
  every other field, any URL and any credential-looking value is dropped inside the parser, and no URL
  from a response is ever fetched.
- The `cloud.swedebeat.se` client (B1) follows ADR 0015 §7: its own scoped list and redirect tests, https/443,
  no redirect at all, the token sent only there and never to the media host. T19 and the egress comments
  (`egress.py`, `compose.e2e.yaml`'s note that the worker never calls Swedebeat, the worker's CA bundle) are
  updated by A1 and B1 when they land.

### 5. (b) Matching a Spotify selection

**Input** is the `song_list` revision of a class that went through ADR 0008 (title, artist, BPM, length,
order: Spotify-derived, ADR 0010). The kind `swedebeat_match` (B1) is deterministic: no model call. For
each song, in order, it makes the library's one catalogue request (paced, at most 40 songs per job, one
request in flight, a per-user job limit) and writes an immutable revision `kind = 'swedebeat_match'`
(`content` at most 64 KB, versioned): per song `{position, status: found | unsure | missing, wanted,
swedebeat?: {title, artist, duration}}`, the totals and `swedebeat_sec`. `found` keeps the library's
0.85 threshold; `unsure` is never treated as found. Catalogue `reference` ids are not stored (nothing uses
them; the mix is built by hand).

**What is sent to Swedebeat**: the title and artist of each song, as the Swedebeat app itself does for a
Spotify list, and the user's token. No Spotify id, class, brief or BPM. Titles and artists are
Spotify-derived (ADR 0010); the beta circle's accepted risk covers keeping them with the user's class, and
looking a song up at Swedebeat is not ingestion into an AI model (Developer Policy §III 14). Rules carried
over from ADR 0010 and ADR 0008 §8:

- the match revision is Spotify-derived: registered in the retention registry, deleted with the class and
  the account, and part of guard 10's plant-and-delete test;
- **no cache of catalogue results across users or classes** keyed by Spotify titles (in-job memory dedupe
  only), so nothing Spotify-derived outlives its class;
- a model may be given only each song's match **status** (§6); never Swedebeat titles, durations, ids or a
  token;
- matching makes no Spotify call, and deleting a class or disconnecting makes no Swedebeat call (as ADR
  0008 §8).

The `connections.use` rules from S1 apply: a 401 or 403 sets `needs_reconnect` and the page shows the
reconnect form; no call is made while one is needed. A 5xx or a timeout is the retryable
`upstream_unavailable`; Swedebeat's 429 pauses the job and shows its `Retry-After`.

### 6. (b) The user's decisions

The match page shows matched, unsure and unmatched songs in the style of Songs for approval (ADR 0013),
with `swedebeat_sec` against the planned time. The user can:

- **Keep as is**: unsure songs the user judges to be the same stay; a missing song stays and the page says
  plainly that the mix will lack it.
- **Replace the missing and unsure songs**: starts the existing `revise_songs` flow (ADR 0008 §6) with
  `replace` set to those positions and a fixed fact ("these songs are not in the Swedebeat catalogue;
  choose songs with the same role and a similar BPM"), then matches the new songs. It is a new
  `song_list` revision approved once, as any (ADR 0008 §5). At most 3 rounds per class.
- **Approve the selection** the mix will be built from: records the match revision the hand-off uses.
  Nothing is written to Swedebeat by the approval.

### 7. (b) "Next, in the Swedebeat app": the manual step and its verification

classbeat never creates the mix. After approval the class shows **"Next, in the Swedebeat app"** with
facts from the class only: the final Spotify playlist's name (ADR 0008; the Swedebeat app matches it), the
song count, the expected length (`swedebeat_sec` minus about 10 s per song change), and short
instructions (open the Swedebeat app, match that playlist, create the mix, let it sync). Two buttons:
**"I made the mix"** (the label until the first check, then **"Check again"**; same action) and "Not now".

State lives in one owned row, not in a long-waiting job (ADR 0005 jobs wait only for an approval):
`swedebeat_handoffs(owner_id, class_id, match_revision_id, state, mixes_before jsonb, mix_id, checked_at,
expires_at)`, one live row per class, RLS by owner. Starting the step snapshots the user's MOWL mix ids
(`mixes_before`, ids only). Each press creates a short job `swedebeat_verify_mix` (one per class at a
time, 10 per user per hour, no credit; and at most 10 new analyses per user per day across §3 and §7, because one press can attach a mix and fetch up to 250 MB; a cache hit costs none):

1. List the user's mixes through MOWL, fresh, in the verify job. Candidates are mixes **not in
   `mixes_before`**; the user can also pick **any** mix from their own listing ("Which one did you make?").
   A `mix_id` sent by the client (picker or override) is accepted only if it is in that fresh listing; A4's
   `admit` repeats the check (defence in depth).
2. For the chosen mix run §3's attach and analysis (same hosts, same limits).
3. **Verdict**, by length and count since the track list is unreadable: `ok` (analysed length within
   `swedebeat_sec` minus up to 10 s per change, plus a small tolerance), `too_short` (names the song
   positions most likely missing, by cumulative length), `too_long`, or `not_found` ("Swedebeat has not
   shown a new mix yet; finish it in the app, then check again"). A verdict never blocks: the user can say
   "this is the right mix" and continue.
4. `ok` or the user's override sets `state = verified` and stores `mix_id`; the class then designs on it
   with §3's source, never on the Spotify playlist: **the class is timed on the Swedebeat mix, whose
   masters differ in length from Spotify's.**

**Tamper rules.** `match_revision_id` is set by the server to the approved `swedebeat_match` revision of the
same class and is never taken from a request; `state`, `mix_id` (once verified), `checked_at` and
`expires_at` change only on the server; a request carries a class id and at most a candidate mix id.
**Expiry** is enforced lazily: every endpoint and the verify job refuse a row whose `expires_at` has passed
(410 `handoff_expired`, nothing attached or fetched) and never revive it; the step offers a new row; the
maintenance pass deletes rows expired for 7 days. A `verified` row expires too (its work is done once the
class is designed); a class already designed on the mix keeps its revision and map. `swedebeat_handoffs`
is in the retention registry and the GDPR export (counts only) and is deleted with the class, the account,
**Disconnect MOWL** and an account change. Mixes attached into the user's MOWL account stay there as MOWL
playlists (as existing links do); classbeat deletes only its own link rows. Both buttons are idempotent; two tabs end with one job
(`class_busy`); a click after `verified` is a 200 no-op.

### 8. Isolation, ownership and the tests every issue carries

- New tables have `owner_id`, RLS and cascade and join the ADR 0002 tenant tests; new routes join the M6
  route-table classification (T101) as `owned` or `session`.
- Two users with the same mix id (a fake MOWL account each) never read or attach each other's mix, map,
  match, hand-off or link; an id not in the caller's listing is refused before any attach or fetch.
- The Swedebeat token is opened only through `UserContext` in the worker, for the match and nothing else;
  the fake records which token each catalogue call carried, with two users.
- Flag off: every new route is 404, no table is read, the builder renders as before (snapshot), a stale
  job ends `feature_off`; and a test runs the whole M4 fake flow with the flag off and asserts zero
  Swedebeat requests and rows.
- Secrecy (T101): the temporary link and signature, the token and the app key are searched in all logs,
  responses and the database dump of the fake flows.
- Egress: the worker lists hold exactly the hosts of §4 at each issue's merge; refusal tests cover a media
  link on an unlisted host, an `http` link, a redirect, an oversized body, and a `cloud.swedebeat.se`
  redirect.
- Fakes only: the MOWL fake gains the swedebeat endpoints and a media endpoint serving short generated
  audio; `e2e/fake-swedebeat` gains a catalogue endpoint and a token check.

### 9. The issues

Review class: **Opus** = jobs, external writes, egress, tokens, migrations with RLS (AGENTS.md); **Sonnet**
= UI and tests over already-reviewed seams (an Opus reviewer where noted). None can start before this ADR
is accepted. #363 stays the tracker. Migration numbers are the next free at merge (0017 to 0019 are
expected).

| # | Title | Scope | Done when | Review | Depends on |
|---|---|---|---|---|---|
| A1 | `feat(swedebeat): fakes and egress for mixes` | MOWL fake: `v1/swedebeat/Playlists`, `Playlist` (PUT), `playlist/{id}/TemporaryLinks`, indexing; an in-process fake media endpoint with failure injection (4xx, redirect, oversize, slow, octet-stream, undecodable); the empty literal `MOWL_MEDIA_HOSTS`, its scoped `Allowlist` and the dedicated no-redirect client (§4); guard tests (literal constant, no other source, no AWS); egress comments updated | fakes reproduce the library's calls; every refusal of §8 passes against an injected list; `WORKER_HOSTS` and the production lists unchanged | Opus | none (#366 merged) |
| A2 | `feat(swedebeat): mix listing, attach and the analysis table` | migration for `swedebeat_mix_maps` (RLS, retention registry, export counts, deletion paths); `GET /api/music/swedebeat-mixes` (flag, MOWL connection, rate limit); external operation `mowl_attach_swedebeat_mix` with reconcile and link deletion on MOWL account change | list and attach work on the fake; cross-user tests; reconcile after a crash between attach and link; flag-off 404 | Opus | A1 |
| A3 | `feat(swedebeat): fetch and analyse a mix` | the music-step branch: temp-link fetch (limits, temp directory, link never logged), the library analysis in a thread with checkpoints and budget, the per-user cache with limit and TTL, `media_host_not_allowed` and the error categories; the child-process analysis with its caps (§3 step 2); the worker image carries `librosa` | analysis of the fake mix (injected list) gives the map with peak RSS under 1.5 GiB and deletes the audio (also on cancel and crash); an oversized-duration file ends `analysis_failed`; a cache hit makes no request; every media refusal ends cleanly and secretly | Opus | A1, A2 |
| A4 | `feat(swedebeat): design on a Swedebeat mix` | `design_draft` source `mowl_swedebeat_mix`: input validation, `admit` (own-listing check, flag, MOWL), revision `music` provenance and fingerprint, warnings, `model_input` planted-value test, Save to MOWL unchanged | a class designed and saved on a fake mix end to end; model input holds no mix text; a user with no Swedebeat designs as before | Opus | A3 |
| A5 | `feat(web): "Use my Swedebeat mix" in the New class builder` | `MusicChoice` third option, picker with analysed badge and empty state, hint without MOWL, plain errors, a11y, flag-off snapshot | component and page tests, axe clean, 44 px targets; flag off changes nothing | Sonnet | A2 (API shape), A4 |
| A6 | `test(swedebeat): done-when suite and e2e for mixes` | the ownership, secrecy and flag-off suites of §8; Playwright for (a) on the fakes with the production (empty) media list; runbook section for the owner's media-host check; T108 and T109 reconciled with the code ("as enforced", T19 updated) | green and mutation-checked per repo practice; e2e shows list, picker, design on a cached map, and `media_host_not_allowed` with zero media requests for an uncached mix; the positive fetch is covered by A3's backend tests | Sonnet (Opus reviewer) | A4, A5 |
| B1 | `feat(swedebeat): catalogue matching job` | `cloud.swedebeat.se` on the worker list only (own redirect tests); the field-allowlist parser; token handling, pacing, 429/5xx; kind `swedebeat_match` and revision kind `swedebeat_match` (migration); retention registry and plant-and-delete test; fake catalogue | a `song_list` matched on the fake gives found/unsure/missing and `swedebeat_sec`; two-user token test; only title and artist leave | Opus | #362 and M4 (done); starts after A1 merges (both edit `egress.py`), otherwise independent of A2 to A6 |
| B2 | `feat(swedebeat): the Swedebeat app step and verification` | `swedebeat_handoffs` (migration), job `swedebeat_verify_mix`, the "I made the mix" / "Check again" endpoints, snapshot, candidates, length verdict, lazy expiry and tamper rules (§7), override, hand-off into A4's design; starts only from the approved match revision B3 records | on the fake: a new mix appears, is analysed and the verdict is right for ok / too_short / too_long / not_found; idempotent buttons; deletion paths | Opus | A3, A4, B3 |
| B3 | `feat(swedebeat): replace unmatched songs and approve the selection` | the `revise_songs` extension (status-only fact, three-round limit, re-match) and the **approval of the selection** (§6): the endpoint that records the approved `swedebeat_match` revision of the class, which B2 and B4 read | missing songs replaced and re-matched on the fakes; model input holds statuses only (planted-value test) | Opus | B1 |
| B4 | `feat(web): match view and "Next, in the Swedebeat app"` | the match page, the step with its two buttons, the mix picker, verdict messages, reconnect prompt, a11y | page tests for each state and verdict; no Swedebeat call from the browser; axe clean | Sonnet | B1, B2, B3, A5 |
| B5 | `test(swedebeat): done-when suite, e2e and runbook for matching` | isolation (two users, tokens, hand-offs), the retention test, a full Playwright run of (b) on the fakes with a mismatch and an override, the runbook and the owner's live checklist (§10), T110 and T111 reconciled | green and mutation-checked; the owner's live checklist written | Sonnet (Opus reviewer) | B2, B3, B4 |

Order: A1; then A2, A3, A4 in sequence with B1 and B3 in parallel (B1 only after A1); A5; A6; then B2 (needs A3, A4, B3); B4; B5. **(a) is technically
done when A6 is green; #363 when B5 is.** `docs/status.md` is updated by the coordinator after each merge,
not by these issues.

### 10. Owner steps (live; never an agent's)

1. One authorized live run with the owner's own MOWL account: list mixes, analyse one, read the
   `media_host_not_allowed` line for the real media host(s) and add them to `MOWL_MEDIA_HOSTS` in a
   reviewed PR (the only code change a live check should need). The same run confirms that MOWL lists
   Swedebeat mixes without the classbeat Swedebeat token.
2. For (b): one match of a short real list with the Swedebeat token, one mix built by hand in the app,
   "I made the mix" and the verdict. Logged under "Live activation log".
3. `SWEDEBEAT_ENABLED=true` and the admin flag, as for S1 (ADR 0015 §9). An agent switches nothing on.

## Alternatives

- **One issue for (a) and (b)**: rejected (§1); one PR would span egress, jobs, a revision kind and a UI,
  and block (a) on the catalogue.
- **Skip the audio analysis and use MOWL's loudness curve**: too coarse to snap changes to the beat, which
  is the feature's point.
- **Analyse in the web process**: long, needs budgets, leases and cancellation; it is a job in the worker
  (ADR 0005).
- **Allow `*.amazonaws.com` for the temporary link**: an open door to any bucket; the exact host is a
  one-line reviewed change after one live check.
- **Create the mix automatically** (script the app, or call Swedebeat's cloud with the app's embedded AWS
  credentials): no sanctioned API; the owner decided it is manual; embedded credentials are forbidden.
- **Share analyses of the same mix across users**: saves a minute per user at the cost of the
  shared-analysis purge machinery (ADR 0007 §11, ADR 0008 §8); mixes are personal.
- **A long-lived job waiting for "I made the mix"**: holds a lease for days; a row plus a short verify job
  costs nothing while the user is in the Swedebeat app.
- **A second flag for (b)**: the owner wants one flag; the Spotify and Swedebeat connections already gate
  (b) per user.

## Consequences

- Three expected migrations, the job kinds `swedebeat_match` and `swedebeat_verify_mix`, one external
  operation, one music source, one builder choice.
- The worker gains two scoped clients (not `WORKER_HOSTS` entries): `cloud.swedebeat.se` with B1 and the
  media host(s) after the owner's live check; the web gains none. Until the media host is confirmed, new mix analysis is unavailable in production (cached
  maps work), by design.
- The worker downloads up to 250 MB per analysis and runs `librosa` (a dependency of the pinned
  `mowl-workouts` wheel) in a memory-limited child process (§3).
- ADR 0010 is amended (below).
- The Swedebeat token gets its first real use (B1); T104 to T107 stay true; T108 to T111 are new.
- Swedebeat's real response shapes, the media host and the 0.85 threshold on real catalogue data are
  unverified until the owner's live checks; a mismatch costs a wrong verdict or a `missing`, not data.

## Amendment to ADR 0010 (the owner accepts it by accepting this ADR)

ADR 0010's table row "Other Swedebeat features: Not built" becomes: **(a) mixes and (b) matching: built
against fakes (ADR 0016); live use only with the owner's authorization.** Its accepted-risk list gains: *classbeat
sends the title and artist of Spotify songs a user picked, with that user's own Swedebeat token, to
Swedebeat to find them in its catalogue.* This is disclosure to a provider the user already uses, not
ingestion into a model; the beta stays free and non-commercial and the tracklist is kept only as long as
the class (ADR 0008 §8). ADR 0010's text is edited in the PR that accepts this ADR.

## Threat model additions (rows T108 to T111, also added to the threat model as proposed)

- **T108** A user's Swedebeat mix, analysis or MOWL link reaching another, or a mix id outside the caller's
  own listing being attached or fetched. (A2, A4, A6)
- **T109** The temporary media link as an SSRF or exfiltration path, a hostile or oversized audio file, the
  link leaking, or embedded AWS credentials being used. (A1, A3)
- **T110** Spotify-derived titles sent to Swedebeat or kept longer than their class; the Swedebeat token
  sent to a wrong host. (B1, B3, B5)
- **T111** The manual hand-off verifying the wrong mix, being tampered with (a forged match revision, mix id
  or state; an expired row revived) or attaching another user's. (B2, B5)
