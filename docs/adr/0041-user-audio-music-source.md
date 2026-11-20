# ADR 0041: `user_audio`, a class built on the user's own audio files (#1305)

Status: Proposed. This ADR itself changes no code, setting, compose file, proxy or runtime
behaviour. It is SD-P7b of the spotdown programme (roadmap
[Amendment 31](../roadmap.md); plan [spotdown-integration.md](../plans/spotdown-integration.md) §1
and §5). The implementation issues of §14 are opened only after the owner accepts it (§13, item 1).

Depends on: [ADR 0040](0040-spotdown-analysis-service.md) (the boundary, timing classes, scopes, the
v2 API) and the relay pattern proven for studio tracks (#1302, still open). The web-side relay of §2
is not the worker-side relay that #1302 proves, so the upload issue (U4, §14) waits for #1302 as
well as for the owner's acceptance.

Amends, effective on the owner's acceptance of this ADR (§13, item 1):
- [ADR 0021](0021-use-part-of-a-song.md) §2, third bullet ("User files: not a source. Needs its own
  ADR") and §3(c) ("the owner approves a user-file source as a separate product decision"). This ADR
  is that ADR and that decision. Part of a song stays out: a user's player cannot be told to skip
  (§6), so no trim is added.
- [ADR 0040](0040-spotdown-analysis-service.md) §7 (and plan §5), for `user_audio` only: the scope
  bullet (an HMAC of `owner_id` as `cache_scope`) becomes a scope **per track** (§9), and the bullet
  "spotdown's lookup by sha256 within the scope, then upload" becomes classbeat's own library check
  only (§2 item 1). Both are stricter, not looser: no cross-user and no cross-track reuse. SwedeBeat
  tracks and the mix keep the owner scope.
- [ADR 0007](0007-classes-revisions-and-saving.md) the amendment of 2026-11-03 (soundless classes, #526), item 1: a class's music
  kind is `none` or not `none` and never changes; `user_audio` is a third, non-`none` kind (§3).

Related: [ADR 0002](0002-tenant-isolation.md) (isolation, `owner_id`),
[ADR 0014](0014-account-export-and-deletion.md) (account export and deletion),
[ADR 0004](0004-connections-and-encryption.md) §7 (logs), ADR 0005 (jobs, leases, fencing),
ADR 0007 §11 (retention registry, backups), [ADR 0008](0008-song-picking.md) §4 (the approved list),
[ADR 0010](0010-live-activation-permissions.md), [ADR 0011](0011-tiers-quotas-and-admin.md)
(plans, gates, quotas), [ADR 0016](0016-swedebeat-music.md) §3 (a private cache is never shared),
[ADR 0026](0026-scaling-music-analysis.md) (analysis slots, (a) the licence question),
[ADR 0034](0034-classbeat-player-connection.md) (Send to player),
[ADR 0036](0036-swedebeat-studio.md) (the studio's track caps, ledger and approval).

## Context

**What exists.** A class's music is a Spotify playlist, a SwedeBeat mix or studio selection, or
nothing (ADR 0007's `none`). ADR 0021 found that a user's own file is "not a source" because MOWL
plays only a Spotify playlist or a SwedeBeat mix, classbeat plays nothing and stores no audio, and
rights and storage would make it "a new product", and it left that to the owner (§3(c)). ADR 0040
now gives classbeat a service that analyses audio it is handed and forgets the audio: spotdown, with
a private `cache_scope`, an `upload` source and `DELETE /v2/scopes/{scope}`. The plan's table of
timing classes already names this case: "the user's own uploaded file that is played" can be `exact`
(ADR 0040 §3 (a)). The plan's decision 2 (no cross-user reuse; each analysis scoped per owner) is the
default for user audio.

**What spotdown already does for an upload** (`magpern/spotdown`, read in the checkout, no call):
`PUT /v2/uploads` takes `application/octet-stream` with a required `cache_scope`, reads the body in
chunks, refuses past `MAX_UPLOAD_BYTES` (100 MiB), keeps only mp3, mp4 and wav recognised by their
bytes, keeps no file name, deletes the file after the job or after one hour, and answers
`{upload_id, sha256, bytes}`. The analysis runs in a subprocess with rlimits (ADR 0040 §10).

**What classbeat has today.** No upload route and no audio storage. Every route's body is capped at
64 KiB (`classbeat.bodylimit`; `ROUTE_LIMITS` is empty on purpose), and the reverse proxy sets
`client_max_body_size 1m` (`docs/runbooks/vps-deploy.md` §10). The spotdown service key is a Docker
secret mounted **on the worker only**, because "every spotdown call is a job step" (ADR 0040 §10,
`classbeat.spotdown.key_policy`). Studio tracks have a per-owner cache, a 60 MB download cap and a
daily ledger of downloads (`design/track_cache.py`, `design/tracklimit.py`: 120 a day, rows kept 48
hours, a per-user advisory lock). `cache_scope` is an HMAC of the owner id
(`classbeat.spotdown.scope_calc`), and the account deletion already records scopes in an outbox
that the worker sends (`spotdown_scope_deletions`, `classbeat.spotdown.scope_outbox`).

**The product.** An instructor has a class in mind and music that is not on Spotify or SwedeBeat
(licensed for their studio, or produced by them). They want classbeat to design the class around
that music with cues at the right beat. They play the music themselves: classbeat cannot, and MOWL
cannot play a file. So the value is the timeline, and the timeline is only right if the file that
plays is the file that was analysed.

## Decision

### 1. The source, and what it is not

A new `music_source` kind **`user_audio`**: the class's songs are audio files the user uploaded for
this purpose, analysed by spotdown, and **played by the user on their own equipment**. It is a
source of analysis, not of playback.

- classbeat plays no audio, serves no audio, keeps no audio and sends no audio to a model, MOWL,
  Spotify or SwedeBeat. The upload is relayed to spotdown and the bytes are gone from classbeat when
  the request ends (§2). Only derived data is kept (§8). **"Never stored" means:** classbeat and
  spotdown never persist the upload (no file, no database row, no volume, no cache, no log). It does
  not promise that no byte ever touches a disk: the reverse proxy may spool the body transiently
  (§2 item 4).
- No Spotify, SwedeBeat or MOWL call is made for a `user_audio` class (no match, no playlist, no
  download). Licensed sources do not apply; the user's own files are the source.
- **No trim.** The whole file is the track. ADR 0021's reasoning stands: no player is told to skip,
  so a trimmed timeline would be wrong.
- **spotdown is required.** There is no local analysis fallback for user audio (the local
  `track_analysis` path is for studio tracks fetched through the gateway and stays as it is). When
  spotdown is off or unreachable the feature says so and offers nothing else (§7). This is a
  departure from ADR 0040 §8 ("spotdown is never a hard dependency"), because there is no path to
  fall back to; the rest of the app is unaffected. The other departure is ADR 0040 §7's scope (§9
  below). Both are named in the "Amends" list.

### 2. The upload path: browser to classbeat to spotdown, never persisted

```
browser --(raw body, PUT /api/user-audio/uploads)--> classbeat web --(stream, PUT /v2/uploads)--> spotdown
```

1. **Check first (no bytes).** The browser computes the file's SHA-256 (Web Crypto, in memory, the
   file is at most 60 MiB) and sends `POST /api/user-audio/check {sha256, bytes}`. If the user's own
   library already holds that sha256 (§8), the answer is `known` with the track and **nothing is
   uploaded**; otherwise `upload`. The check reads only the caller's own rows (ADR 0002), so it
   cannot reveal another user's file. This is ADR 0040 §7's "own cache by sha256 before any upload".
   spotdown's sha256 lookup is not used for user audio (§9: each track has its own scope, so it
   would always miss).
2. **Relay.** `PUT /api/user-audio/uploads` takes the file as the **raw request body** (no
   multipart, so no framework spooling to disk or memory), `Content-Type: application/octet-stream`.
   The web process reads it chunk by chunk and writes each chunk to spotdown's `PUT /v2/uploads`
   with `httpx` streaming, with back-pressure, **never to a file or a buffer larger than a chunk**.
   On the way it counts bytes and runs SHA-256. It sniffs the first bytes and accepts only what
   spotdown accepts (mp3, mp4/m4a, wav, §4) before it opens the spotdown request; the count is
   enforced as the bytes arrive, not from `Content-Length` (as `bodylimit` does). Over the cap, an
   unknown format, or a client that goes away: the relay closes the spotdown request, the track row
   goes to `failed` and its scope is deleted (§9). A sha256 the browser declared must equal the
   relayed one, else `422` and the same cleanup.
3. **Then a job.** The route creates the track row (state `uploading` before the first byte, so a
   scope always has a row, §9) and, when spotdown answers `201`, a worker job `analyse_user_audio`
   with `{track_id, upload_id}` (an opaque handle that lives one hour). The job runs
   `POST /v2/analyses` (by `upload_id`, no `reference`: user audio has no Spotify clock), polls with
   the existing fenced steps and stores the derived result (§8). The user's page follows the job
   as it follows any job.
4. **The route is the one body-limit exception.** `bodylimit.ROUTE_LIMITS` gains exactly one entry,
   `/api/user-audio/uploads`, with the cap of §4 plus a small margin, and the reason next to it
   (the test that fails on an undocumented entry is updated to name it). The reverse proxy needs, on
   that one `location` only (the rest of the site keeps `1m`), a larger `client_max_body_size` (the
   cap of §4 plus the margin) and a `client_body_timeout`. **The proxy may spool the body
   transiently.** nginx (SWAG) buffers a request body over `client_body_buffer_size` to a temp file
   in `client_body_temp_path` and deletes it when the request ends. That is not storage by
   classbeat or spotdown, and this ADR does not require the proxy to avoid it: turning request
   buffering off would hold an app connection open for every slow client (owner decision,
   2026-11-20). The spool is bounded by `client_max_body_size` on that location (60 MiB per
   request, and the box-wide limits of §4 on parallel uploads), and an unauthenticated request
   over the cap is refused with 413 before it is spooled. Optional hardening, not a requirement: put
   `client_body_temp_path` on tmpfs, or leave it on the container's ephemeral layer (it is gone with
   the container). The implementation PR (U4) puts the location in `swag-classbeat.subdomain.conf`
   and the runbook (`vps-deploy.md` §10) with the `client_max_body_size` line, and a runbook check
   that a body over the cap is refused with 413 (no performance cost). The privacy text (§13, item
   2) says the proxy may hold the body briefly while it is in transit. The deploy change itself is
   the owner's (§13, item 5).
5. **The web tier calls spotdown for this one path.** The service key is a worker-only secret today.
   Relaying from the web process needs it there, a change to how a secret is mounted. The chosen
   default (reversible; the upload-only key of §11 is the safer follow-up): mount the same `spotdown_service_key` file read-only on `web`
   as well; the egress builder takes a **role** (`web` or `worker`) and a role may call only its own
   paths and methods: `web` only `PUT /v2/uploads`; `worker` the rest (`POST /v2/analyses`,
   `GET /v2/jobs/{id}`, `GET /v2/analyses/{id}`, `DELETE /v2/scopes/{scope}`, and the studio
   calls); a test pins both lists, and `tests/egress_scan.py` is updated. The web process is
   internet-facing, so this widens the key's exposure; the key reaches only a service on the internal
   compose network that holds no user token, no KEK and no database access (ADR 0040 §13), and an
   attacker with code execution in web already holds the database role and the session key; a full
   key on web still lets a compromised web call `DELETE /v2/scopes/*` and start analyses (T142). This
   mount is the one place the ADR needs the Opus review of a secret-handling change (AGENTS.md,
   Amendment 13), and the Architect confirms it before the issue opens. §11 lists the alternatives.

### 3. The music kind, the source row and the model's part

- `classes.music_source` is `{"source": "user_audio", "origin": "upload"}` (inside the 1024-byte
  check; it lists no tracks). The class's music kind `user_audio` never changes once stored, enforced
  in `design.music_kind` like `none` (ADR 0007, the soundless amendment, item 1): `user_audio` on a class of
  another kind, or another kind on a `user_audio` class, is `invalid_input` before any credit is
  reserved. Duplicating a class keeps the kind and the track references (§8).
- The tracks sit in the revision: `class_revisions.music` holds, per track, the `track_id`, the
  user's label, the sha256, the measured duration, `analyzer_version`, the timing class with its
  basis (§5) and the derived timing the design needs (the same adapter as the studio's
  `spotdown_map_policy`). A revision is therefore complete without the library row (§8) and goes
  with its class.
- **The model proposes, the backend decides.** The user chooses which uploaded tracks are in the
  class. The model may propose an **order** and a **subset** to fit the brief and the length, the
  way it orders any list; the backend validates that every track exists, belongs to the user and is
  `ready`, that the total length fits the class length within the usual 10 % check, and that no
  cut, start or end offset is present (§1). The user approves the list (ADR 0008 §4: the approved
  list and its hash include the track ids, their sha256, the order and the crossfade, so a changed
  list needs a new approval).
- **What the model sees:** per track the user's label (user text, treated as untrusted like the
  brief and bounded to 80 characters), the duration, BPM and the feature-trust output of
  `feature_trust_policy`, and its timing class. **Never** a file name (none is kept), a tag read from
  the file (classbeat reads none), a sha256 or audio. The default label is "Track 1", "Track 2" and
  so on; the user may type their own.
- The design validator is unchanged: beat- and bar-precise instructions need `exact` (§5).

### 4. Types, caps and quotas

All numbers are settings with these defaults; each is reversible by the owner and a change is a
settings change, not a design change. Every limit is checked by the backend, never trusted from the
browser.

| Limit | Default | Where enforced |
|---|---|---|
| Formats | mp3, m4a (mp4 audio), wav, recognised by their first bytes **and** by spotdown. The declared extension and content type are not trusted. FLAC and Ogg are out of v1 because spotdown's upload does not keep them (the same list is extended when spotdown does) | web sniff; spotdown |
| Size of one file | **60 MiB** (the studio download cap, ADR 0036; under spotdown's 100 MiB). About 6 minutes of 16-bit stereo wav, far more of mp3 | the relay, counted as streamed |
| Length of one file | **12 minutes** measured after decoding; longer is `too_long`, nothing kept | spotdown's decoded-length rlimit; checked again on the result |
| Tracks in a class | **20** | admit |
| Tracks a user holds (all states) | **100** (`library_full`; the user removes some) | the route, under the owner lock |
| New uploads per rolling 24 hours | **30** and **1.2 GiB**; a `known` check costs none. Ledger table in the style of `swedebeat_track_downloads`: one row per upload started (so a failed one counts), per-user advisory lock, rows trimmed at 48 hours | the route, before the first byte |
| Uploads in flight | **1 per user**, **3 for the box** (`busy`, HTTP 429, the existing shape). Both are **database counts** (there are several web processes) of `uploading` rows younger than the 10-minute relay wall budget of §10, so a crashed relay stops counting after 10 minutes, not at the 2-hour purge | the route |
| Analysis slots | the same ADR 0026 stage 1 slots as every analysis; no priority change | the job queue |
| Plan gate | a feature gate `user_audio_allowed` in ADR 0011's gate set, seeded **off for Free and on for the paid plans**; the admin area edits it; the admin switch `CLASSBEAT_USER_AUDIO` is **off by default** and a kill switch | admit (`409 quota` `feature`) |

The first call of the feature records the user's acceptance of the rights statement (§10) as a
timestamp on the user's row; without it the route answers `consent_required`.

### 5. Timing class: `exact` only for the file that is played

ADR 0040 §3 (a) makes the user's own uploaded file `exact` **only if it is the file that is played**.
classbeat cannot see what the user plays, so the rule is:

- A track is `exact` when (i) spotdown analysed these bytes (the sha256 of the relay equals the
  sha256 in spotdown's provenance), and (ii) the user **attested at approval** that they will play
  these files, unedited, in this order, **gapless or with the crossfade stated on the class** (so the
  stated crossfade is part of what is attested and of the approval hash). The attestation is a required, unticked-by-default
  statement on the approval screen, bound into the approval like the studio's consent (ADR 0036 §7),
  and stored with the revision. Without it the class cannot be approved. The stored basis is
  `user_attested_same_file`, mapped to `played_recording` by `spotdown_map_policy` (the only basis
  policy v1 maps).
- The user can check what they will play: the class page and the cue sheet list each track with its
  label, duration and the **first 8 hex characters of its sha256**; the user can compare it with the
  file on their own computer. The sha256 is shown to its owner only and never logged with the owner.
- **A different file is a different recording.** A re-encode, another rip or an edited file has
  another sha256 and is not covered. The UI says "upload the file you will play". A fingerprint
  match is never `exact` (ADR 0040 §7); path (b), verified equivalence, has no consumer in policy v1.
- If the file changes, the user uploads it again: new sha256, new track, new approval.
- **Within a track the beats are exact; between tracks the user's player decides.** The class
  timeline places track k at the sum of the earlier durations less the crossfade. The crossfade is
  a per-class setting the user states, **default 0 s** (gapless), range 0 to 12 s; classbeat cannot
  know a player's gap or fade. A player that adds a gap moves later tracks; that is the user's
  equipment, and the sheet says so. The user is asked to start the first track when the class starts.
- The model sees `exact`; the validator allows beat-, bar- and drop-precise cues on these tracks
  (ADR 0040 §3). A `heuristic` label stays a hint. A `processing_failed` job has no analysis and
  the track is not used (§7); there is no reference, so `alignment_failed` does not occur.

### 6. What plays it, and where the class goes

- **The user plays the files**, on their own player or mixer, in the approved order. classbeat gives
  them the **cue sheet**: the class page and a printable and downloadable list of tracks, start
  offsets in class time, durations, short hashes and the crossfade. It is generated from the stored
  revision; it contains no audio.
- **Outputs, none new in kind:**
  1. the class page and the cue sheet (no external write);
  2. **Send to player** (ADR 0034), unchanged: the class format carries the timeline and the beat
     cues, and the player has no audio from classbeat;
  3. **Save to MOWL** as a workout **without a playlist** (`PlaylistID: -1`), the soundless
     mechanism (ADR 0007's amendment of 2026-11-03, #526), with the beat cues. This is **off until
     the soundless gates hold** (the environment switch `CLASSBEAT_SOUNDLESS_ENABLED`, eval
     coverage of the design route, and the owner-authorized live save and update checks listed under
     "Waiting for the owner", Soundless classes), **and** one further owner-authorized read-back of
     a `user_audio` save (§13, item 4). Until then the Save control is not offered for a
     `user_audio` class, and nothing writes to MOWL.
- No Spotify playlist, SwedeBeat playlist or mix is made for a `user_audio` class (no source to
  put in them), so ADR 0036's outputs and consent screen do not apply. Agents never call MOWL, and
  every external write needs the user's approval of the draft as today (ADR 0005 §7).

### 7. Failures

| Case | What the user sees | Rule |
|---|---|---|
| spotdown off or unreachable | "Audio analysis is not available now", the upload control disabled; classes of other kinds are unaffected | no queue of uploads; nothing is stored |
| wrong format, over the size or length cap | the reason, named in plain words | refused before or during the relay; nothing kept; the row and scope removed |
| `processing_failed` (decode, timeout, out of memory) | "We could not read that file; try another export" | no retry on the same audio (one retry after a timeout, ADR 0040 §8); the row removed |
| `rate_limited`, analysis slots busy | "Waiting for analysis", then the result | fenced requeue with `retry_after_s`, within the 10-minute budget of ADR 0040 §8, then "Try again later" |
| the class design fails for a track | the track is replaced by another uploaded one, or removed from the list, by the user | the user has nothing to swap from but their own tracks: no automatic replacement, no search |
| low confidence (BPM, beats) | the track is marked, and the model sees a low trust | `feature_trust_policy`; the user may still use it with a warning |

### 8. What is kept, where, and for how long

classbeat keeps **no audio, no file name, no tag and no URL**. It keeps:

| Data | Where | Deleted with / after |
|---|---|---|
| The track: label (≤ 80 characters), sha256, bytes, duration, state, `analyzer_version`, derived timing (size-checked, like `track_cache`), a random `track_id` | `user_audio_tracks` (owned, `owner_id`, ADR 0002) | the user's removal; the unreferenced purge (below); the account; failed rows at once |
| Per class: the tracks used, their labels, sha256, durations, timing class and basis, the attestation, the crossfade | `class_revisions.music`, `classes.music_source` | the class (cascade) or the account, as ADR 0007 §11's table |
| The upload ledger: owner, time, bytes (no label, no hash) | `user_audio_uploads` | trimmed at 48 hours; the account |
| The user's rights acceptance: a timestamp | the user row | the account |
| Counts, enums, own ids (no label, no hash) | `job_events`, logs (ADR 0004 §7, ADR 0005 §6) | the job; log retention |
| The same derived analysis, per track | spotdown, under that track's own scope (§9) | `DELETE /v2/scopes/{scope}` |

- **A track is referenced** when **any stored revision** of a class of the same owner lists its
  `track_id` (ADR 0007 §11: every owned row that uses an analysis records its id); old revisions
  count, because they are kept and can be made current again. **Unreferenced tracks are
  purged 7 days after upload** (a track that was uploaded and never used, or whose classes were
  deleted; a revision that dropped it does not free it while an older revision still lists it), and a `failed` or `uploading` row after 2 hours. The purge is a fenced
  worker sweep in the style of the queue's, in small batches; it only enqueues the scope deletion
  (§9) in the same transaction that deletes the row.
- **Removing a track** ("My audio" list, one by one or all) deletes the row and enqueues its scope
  deletion. A class that used it **keeps its revision's snapshot** (label, durations, timing), so the
  class and its cue sheet still work; only "Check again" and refinement of that track need an upload.
- **Deleting a class** removes its revisions and, in the same transaction, the tracks that no other
  class of the user references (a user who wants to keep a track for reuse keeps a class that uses
  it). This default favours privacy over re-upload cost.
- The retention registry (guard 10, ADR 0007 §11) gains one entry per new table and column above
  with its deletion path, and the plant-and-delete database test covers them; the account export
  (ADR 0014) lists the tracks' labels and metadata, not audio, which does not exist.
- **Backups** keep deleted rows for up to the period of ADR 0007 §11 (35 days proposed) and the
  privacy text says so; a restore re-applies the deletions made since. If spotdown's volume is
  backed up, the same rule applies there; spotdown's runbook states it (§14, U2).

### 9. spotdown scope, deletion and GDPR

- **One scope per track, not per owner.** `cache_scope` for a `user_audio` track is
  `u1-` plus the unpadded base64url HMAC-SHA256 of `owner_id` and `track_id` with the same derived
  key as `scope_calc` (a new pure function beside `cache_scope`, tested for stability and
  non-collision with the owner scope `o1-…`). Reasons: (1) `DELETE /v2/scopes/{scope}` removes a whole
  scope and nothing finer, so a per-owner scope could not delete one track or one class's tracks;
  (2) spotdown can neither link a user's tracks to each other nor to the owner id; (3) no cross-user
  and no cross-track reuse is possible at spotdown (decision 2 of the plan, strictly). The cost is
  that spotdown never reuses a user's analysis across tracks, which is nothing: classbeat's own
  library by sha256 (§2, step 1) already avoids re-uploading and re-analysing the same file.
- **The scope is stored, not recomputed.** It is opaque and holds nothing personal, so it is a
  column of `user_audio_tracks`, written with the row, and every deletion enqueues the **stored**
  value. A rotation of the derived key (`scope_calc` warns it orphans old scopes) therefore does not
  orphan user-audio scopes; the key is used only to create new ones.
- **Deleting** a track, the unreferenced purge, a failed upload and a class deletion insert the
  stored scope into the existing outbox (`spotdown_scope_deletions`) **in the transaction that
  removes the row**; the worker sends `DELETE /v2/scopes/{scope}` and retries until `2xx` or `404`.
  The **account deletion** records the owner's stored scopes just before the erasure, as it already
  does for the owner scope (`scope_outbox.record_for`, in the erasure's own steps, not one
  transaction with the row deletes), and the outbox row outlives the account. Nothing depends on the
  web request surviving.
- **A relay that outlives its row.** If the row is deleted while its relay is still streaming (an
  account deletion, or the purge of a stale `uploading` row), the outbox may send `DELETE` before
  spotdown stores the upload, and the `201` then leaves an upload in a scope nobody deletes. The
  relay therefore checks, on completion, that its row still exists; if not, it enqueues the stored
  scope again (the outbox keeps it, the sender treats `404` as done). If the process dies first, the
  upload is deleted by spotdown's own expiry within one hour (the accepted bound).
- **GDPR.** Erasure: the account deletion removes every classbeat row above and enqueues every scope;
  spotdown's deletion removes the analyses, jobs and run records of those scopes (ADR 0040 §5).
  Access and portability: the account export lists the library and the class snapshots (labels, durations,
  timing), not audio, which classbeat never had. Retention: §8's numbers are the stated defaults.
  spotdown's sha256 and decoded-audio fingerprint of a user's file identify a recording and are
  held only under that track's scope until it is deleted; spotdown has no user accounts and cannot
  link the scope to a person. A scope delete is a hard delete, a requirement on spotdown (§14, U2).
- A scope that fails to delete stays in the outbox and is retried; the user is told their data is removed from
  classbeat at once and from the analysis service on its next successful call.

### 10. Rights, abuse and malware

- **Rights stance (the plan's decision 2).** The user warrants that they may use the files for this
  purpose, accepted once at first use (§4) and stated in the terms (the owner's or a lawyer's text,
  §13, item 2). classbeat does not host, serve, share or publish any user audio and does not
  derive anything it shares: analyses are private to the track's owner, never reused across users,
  and **this stays so whatever the owner decides about #1297**: that decision covers SwedeBeat
  and the mix, and widening it to user files needs an amendment of this ADR. The analysis is a
  derived work of the user's file made for the user.
- **Abuse brakes:** the plan gate and the kill switch; the per-user ledger, the in-flight limits and
  the library cap; an admin can switch the feature off by plan and for the whole site and sees counts
  only, because there is no audio to see (a per-user switch is a possible follow-up); a rights complaint is answered by
  deleting the account's tracks and, if the owner chooses, the account (support contact in the terms,
  §13, item 2). No public or shared URL for audio or analysis exists.
- **Malware and hostile files.** classbeat never decodes, parses beyond the format sniff, writes or
  executes the bytes; it relays them. The decoder is spotdown's, in a subprocess with rlimits on
  memory, CPU time and decoded length, inside the container ceilings, and spotdown deletes the file
  after the job (hard gate: no audio kept). **No antivirus scan** is added: nothing is stored, served
  or executed, and a scanner would be one more parser of hostile input next to the web process; this
  is reversible (§11). Requirements on spotdown for hostile input (§14, U2): force the demuxer from
  the sniffed kind (mp3, mp4, wav only) so a text container such as a playlist, `concat` or HLS file
  is never opened, allow no protocol but `file`, read only the first audio stream of an mp4 and
  ignore video and data streams, and refuse a file whose decoded length passes the cap. Polyglot
  files only reach the decoder, never a classbeat parser.
- **Cost and denial of service:** one relay at a time per user, three for the box, the byte ledger,
  and the analysis slots keep a user from starving design jobs. A relay that stalls is closed by
  a per-chunk timeout (default 30 s) and a total wall budget (default 10 minutes).

### 11. Egress, sandbox and secrets

- **Egress.** One new web-side outbound path, to spotdown only, through the existing
  `classbeat.egress_spotdown` builder with a role (§2, step 5). No new origin and no new host:
  `tests/egress_scan.py` gets the role rule; the origin rule for an internal compose origin is
  ADR 0040 §10's. The web container joins the compose network that reaches spotdown (a compose
  change, in the implementation PR, with `scripts/check-prod-compose.sh` updated; spotdown still
  publishes no port).
- **Sandbox.** classbeat adds none: the decoder is spotdown's (§10). The web process holds at most
  one chunk of the file at a time, so a hostile body costs a bounded buffer and a socket; the
  existing seccomp and Landlock machinery of the worker's child is not involved.
- **Secrets.** The one change is the key mount on web (§2, step 5). The key stays a Docker secret
  file, never an environment value, and `ServiceKey`'s constant `repr` still applies. No audio,
  label or hash is logged, and none reaches a model.

**Alternatives for the relay,** rejected for now:
- **Spool to a shared volume and relay from the worker** (keeps the key worker-only): rejected. It
  stores audio at rest on classbeat's disk (this ADR's first rule), needs a shared volume between
  web and worker, a sweeper, quota on disk and a second hostile-input copy; the key exposure it
  avoids is small against that. Reversible: it is a change to the relay only.
- **A narrow upload-only key** (spotdown accepts a second key for `PUT /v2/uploads` only): the
  better hardening, rejected for v1 because it needs a spotdown contract change and a second secret;
  recorded as the first follow-up if the review prefers it.
- **A signed upload ticket straight from the browser to spotdown:** rejected in the plan (§8); it
  would publish spotdown on the web and put its auth in the browser.
- **An antivirus scan (ClamAV):** rejected, above.

### 12. Threat model rows

New rows in `docs/security/threat-model.md`, planned until built (T138 and up, the file's rule):

| ID | Threat | Mitigation (planned) |
|---|---|---|
| T138 | A user's audio stored, served, logged or sent to a model by classbeat (a spool, a temp file, a log line, a prompt) | The relay streams chunk by chunk, no file or large buffer; no multipart framework; the proxy may spool the body transiently (nginx's `client_body_temp_path`, deleted when the request ends, bounded by `client_max_body_size`; not persistence, §1 and §2 item 4); no audio, label or hash in logs or prompts; a test uploads a canary through the fake spotdown and scans the process's files, logs and the model request fixtures (the proxy's transient spool is outside this test, §2 item 4) |
| T139 | The upload route used to exhaust the box (a huge or endless body, many parallel uploads, a slow-body attack) | One route-specific cap counted as streamed; Content-Length not trusted; per-chunk and wall timeouts; 1 relay per user and 3 per box; the daily byte and count ledger; the proxy's per-path `client_max_body_size` (413 before the body is spooled) and body timeout, which also bound the proxy's transient spool; the plan gate and kill switch |
| T140 | Hostile audio reaching a decoder in a way that reads files or the network (a playlist container, `concat`, HLS), or decodes without bound | classbeat sniffs and relays only mp3, mp4, wav; spotdown forces the demuxer, allows no protocol but `file`, reads one audio stream and caps decoded length in a subprocess (spotdown tests); classbeat never parses the body |
| T141 | One user's track, analysis or sha256 reaching another user, or surviving a track, class or account deletion | A scope per track derived from `owner_id` and `track_id`; no lookup across scopes; the check route reads only the caller's rows; the outbox in the deleting transaction; the retention registry and plant-and-delete test; a fake spotdown test that deletes and then looks the scope up |
| T142 | The spotdown service key exposed through the web tier | Role-limited egress (web: `PUT /v2/uploads` only), a pinned path list, the key file read-only on web, never in env, logs or errors; the Opus review of the mount. **Residual:** a full key on web lets a compromised web delete scopes and start analyses; an upload-only key (§11) would remove that |
| T143 | A track treated as `exact` when the played file differs (an edited or re-encoded file), or the model told a beat position that does not match what plays | `exact` only with equal sha256 and the stored attestation; the sheet shows the short hash; a different file is a new upload and a new approval; the crossfade is the user's stated value; the validator refuses precise cues without `exact` |
| T144 | Copyright or rights abuse (the feature used to process content the user may not use, or as a way to share it) | The rights statement and terms; no hosting, serving or sharing of audio or analyses; private per owner; the plan gate and kill switch; deletion on complaint |
| T145 | A label or the sha256 used for injection or tracking: prompt injection through the label, or the hash linking users | The label is untrusted user text, bounded to 80 characters and delimited like the brief; the hash is shown only to its owner, never logged with the owner, and spotdown holds it only under a per-track scope |

### 13. What remains the owner's decision

1. **Accept the product** (ADR 0021 §3(c)): user files become a music source, with the limits above.
   Acceptance also accepts the amendments of ADR 0021, ADR 0007 and ADR 0040 §7 named in the header
   (the Architect records a dated amendment in each, and fixes plan §5).
   Until the owner sets this ADR to Accepted, no implementation issue opens. If the owner declines,
   ADR 0021 §2 stands and this ADR is Rejected.
2. **The legal text:** the rights statement and the terms and privacy sentences for user audio
   (what the user warrants, that spotdown processes it on classbeat's own box and keeps only derived
   data under a per-track scope, the rights-complaint contact and what classbeat does on one). The
   legal pages are drafts that the owner or a lawyer writes; agents write placeholders only.
3. **Plans and numbers** (default: §4; paid plans only, off for Free). An answer changes seeds and
   settings, not the design. Not blocking.
4. **A live check of the output** (owner-authorized, never an agent): saving one `user_audio` class
   to MOWL with `PlaylistID: -1` and reading it back, after the soundless gates of §6. Until then
   no Save to MOWL for this kind.
5. **The proxy change at deploy** (owner only): for the one upload path, a larger
   `client_max_body_size` and a body timeout (§2 item 4; no change to proxy buffering), and the
   compose network and key mount of §11.
6. **Not asked:** cross-user reuse of user-audio analyses (no, §10); YouTube (not used);
   anything about #1296 to #1299.

Defaults taken by the Architect (the owner can override): the numbers of §4 and §8, the web-side key
mount (§2, step 5), the per-track scope (§9), the privacy-first class deletion (§8), no antivirus
scan (§10), the 0 s crossfade (§5), the formats of v1.

### 14. The work that follows (issues open only after item 1 of §13)

| Id | Scope | Repo | Owner line |
|---|---|---|---|
| U1 | Pure modules: `user_audio_policy` (caps, format sniff, label rules, class limits), `user_audio_scope` (the per-track scope), `user_audio_limit` (ledger and in-flight rules), the music kind `user_audio` and the validator's `exact` basis; table tests | classbeat | Sonnet |
| U2 | spotdown: confirm and test the hostile-input requirements of §10 (forced demuxer, protocols, one audio stream, decoded-length cap), hard delete of a scope, the backup note; a contract line for a user-audio length cap if the upload needs a hint | spotdown | Opus |
| U3 | Tables `user_audio_tracks`, `user_audio_uploads`, the consent column, retention registry entries, the unreferenced purge, the outbox for per-track scopes, account and class deletion, export; RLS and plant-and-delete tests | classbeat | **Opus** |
| U4 | The relay route and check route, the body-limit entry, the egress role, the key mount on web, compose and `check-prod-compose.sh`, fake spotdown for upload, the canary test (T138) | classbeat | **Opus** |
| U5 | The job `analyse_user_audio`, the failure table of §7, the adapter to the music map | classbeat | Sonnet |
| U6 | The class flow: the music kind in the composer, the model's order and subset, the validator, the approval with the attestation and its hash, the revision snapshot | classbeat | **Opus** (approval binding) |
| U7 | UI: the upload step with progress and cancel, My audio, the class page and the cue sheet, sv and da strings (ADR 0023 translation PR) | classbeat | Sonnet |
| U8 | Outputs: the cue sheet download, Send to player, Save to MOWL behind its gate | classbeat | Sonnet; Opus for the MOWL write |
| U9 | Evals and e2e on the fake: a `user_audio` brief, the `exact` cues, the threat rows T138 to T145 as tests | classbeat | Sonnet |

## Alternatives

- **Keep user files out (ADR 0021's decision):** possible, and the owner's to choose (§13, item 1).
  It leaves instructors with licensed or own music no way to a beat-accurate class.
- **Store the audio in classbeat and play it** (a hosted player): rejected. It is storage, rights,
  streaming and a hosting liability, the "new product" ADR 0021 described, and MOWL cannot play it.
- **Analyse in the browser** (WebAssembly, no upload): rejected for v1. A second analyser to build and
  keep equal to spotdown's, no shared quality gates (ADR 0040 §9), and a browser's decode limits;
  it would be the privacy-best option and stays possible later, as a separate ADR.
- **Analyse locally in classbeat** (extend `track_analysis`): rejected. It puts hostile-input
  decoding next to the worker's KEK and tokens, the reason ADR 0040 chose spotdown.
- **A per-owner scope with a sha256 lookup:** rejected (§9): a scope can only be deleted whole, so a
  track or a class could not be erased on its own.
- **Trust the user's claim without the attestation, or class every upload `approximate`:** the first
  risks a false `exact` (a hard gate, ADR 0040 §9); the second discards the one source where
  beat-exact timing is possible. The attested-same-file rule is the middle path.
- **Read the title and artist from the file's tags:** rejected. Tags are hostile input, often wrong,
  and personal data; the user's label is enough.
- **Share analyses across users by sha256** (the same popular track analysed once): not now; the
  licence question (#1297, ADR 0026 (a)) is the owner's, and §10 keeps user audio out of it.

## Consequences

- Nothing changes in code, settings, compose, the proxy or deployment with this ADR. It becomes the
  design for §14's issues once accepted.
- ADR 0021, ADR 0007's music-kind rule and ADR 0040 §7 (the per-track scope and the library-first
  check, for `user_audio` only) are amended on acceptance; the Architect then records a dated
  amendment in each, pointing here (the way ADR 0040 did for 0010 and 0021). The frozen plan §5 is
  fixed in the same PR as ADR 0040's amendment.
- classbeat gains its first upload route, its first non-empty `bodylimit.ROUTE_LIMITS` entry, a
  second holder of the spotdown service key (the web tier, for one path), and a new class of
  personal data (a user's label and file hash) with a stated retention.
- The feature is a **hard dependency on spotdown** for its own kind only (§1).
- The player and the cue sheet give the timeline; the audio never leaves the user's hands. Whether
  MOWL can carry the class (Save) depends on the soundless live checks the owner authorizes.
- Reviewing: this ADR gets a fresh Opus review (a new security design: an upload path, a secret
  mount, personal-data retention), as do U3, U4 and U6.

## Status

Proposed. §13 item 1 is the owner's acceptance; items 2, 4 and 5 are owner-only before use.
