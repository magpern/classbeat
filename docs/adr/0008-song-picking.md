# ADR 0008: Song picking (M4)

Status: Proposed

## Context

M4 is roadmap §2's song-picking flow, behind the product criteria A1 to A4: Claude drafts a candidate
pool, the backend puts it into a managed private playlist in the user's own Spotify account and has
MOWL analyse it, Claude chooses the final songs from real BPM, length and sections, the user approves
**once**, the backend creates the final private playlist, verifies it and attaches it in MOWL, and the
class design continues by itself (roadmap §2 steps 1 to 6, §4 approval, §5 stages, §6, §10 and the M4
row). The Spotify connection (BYOK wizard, OAuth, tokens, errors, the API migration, the library
release) is [ADR 0009](0009-spotify-connection.md); this ADR is everything built on it. The owner
accepted the Spotify-terms risks for the invite-only beta ([ADR 0010](0010-live-activation-permissions.md),
roadmap Amendment 1).

Relied on, unchanged: the job system (ADR 0005: phases, one wait for approval, `AwaitApproval` and
`Complete`, fencing, `perform` for external operations, `uncertain` and the user's two actions, the
7-day expiry, credit settle); the agent loop and its rules (ADR 0006: stages with allowlists, strict
schemas, limits, `model_input` as the one allowlisted builder, index-only references, provenance);
classes, revisions and the K1 seams (`consumes_credit`, `admit`, current-revision pointer; ADR 0007
§2, §3, §5); `mowl_playlist_links`, `mowl_attach_spotify_playlist` and the design music step
(ADR 0006 §3); `class_revisions.kind = 'song_list'`, `jobs.approval_*` and `POST /api/jobs/{id}/approve`
(built and tested with a test-only kind in M3a).

## Decision

Sections 1 and 2 are data and the Spotify writes, §3 the model side, §4 to §7 the jobs and the
approval, §8 retention, §9 the surface, §10 settings, §11 rollout, §12 tests, §13 the issues.

### 1. Tables, kinds, categories

One migration `0006_spotify` (issue SP1; no other M4 issue adds one) with ADR 0009's `spotify_oauth_flows`
and `connections.generation`, and these owned tables (ADR 0002 §2: `owner_id` → `users` `ON DELETE
CASCADE`, `UNIQUE (owner_id, id)`, owner-inclusive FKs, RLS, application-supplied ids):

- **`spotify_pool`**: one row per user (`UNIQUE (owner_id)`) = the standing consent of roadmap §2:
  `id`, `owner_id`, `spotify_user_id text`, `playlist_id text NULL` (check `^[0-9A-Za-z]{22}$`; NULL
  until the pool playlist exists), `consented_at`, `consent_version int`, `last_hash bytea NULL` (SHA-256
  of the ordered URIs last verified), `last_written_at NULL`, `updated_at`.
- **`class_playlists`**: one row per **final** playlist classbeat created for a class: `id`, `owner_id`,
  `class_id` → `classes` (`CASCADE`), `revision_id uuid NULL` (the approved `song_list` revision, `SET
  NULL (revision_id)`), `playlist_id text` (22 characters), `spotify_user_id text`, `name text` (≤ 100),
  `state` check `IN ('created', 'verified')`, `content_hash bytea NULL` (the verified ordered ids),
  `track_count int`, `mowl_playlist_id bigint NULL`, `create_operation_id uuid NULL` (`SET NULL`),
  `detached_at NULL`, `created_at`, `updated_at`. Partial unique index `(owner_id, playlist_id) WHERE
  detached_at IS NULL`. Holds ids and a name, no track data.
- `external_operations.kind` check gains **`spotify_create_playlist`** and **`spotify_replace_items`**.
  Pool rows carry `class_id NULL`; final rows carry the class (ADR 0007 §1's rule becomes: a row carries
  `class_id` exactly when the kind is a *class* write: the MOWL saves and the final-playlist writes).
- `jobs.input` of the new kinds, `classes.music_source` (gains an optional `origin: "song_pick"` and
  `class_playlist_id`), `class_revisions.music` (gains `song_list_id` on drafts made from picked songs)
  are existing jsonb columns with new validated fields.
- **Error categories** (the three shared checks): ADR 0009 §8's list, plus `consent_missing`,
  `pool_unusable`, `pool_missing`, `pool_not_synced`, `playlist_mismatch`, `pool_too_small`. Retryable:
  `pool_missing`, `pool_not_synced` (once more) and ADR 0009's retryable ones; the rest are not.
- **Job kinds** `design_with_songs`, `revise_songs`; **event kinds** do not change (`stage_started
  {stage}`, `revision_created`, `waiting_for_approval`, `approved`, `external_operation`, `completed`).
- Guard 10: `RETENTION` gets entries for the three tables and a deletion path `spotify_connection` (§8).

### 2. Playlists and the Spotify writes (roadmap §2)

**By ID, never by name.** The pool's id is stored when first created and only that exact id is ever
written (`spotify_pool.playlist_id`). Final playlists are always new resources created by classbeat and
addressed by the id the create returned. No code path searches playlists by name to decide where to
write; the **one** name-based use is the reconciler's search for a playlist classbeat itself just
created (below), and it uses a unique token and the owner check, not the display name.

**Every write is preceded by an owner check** (inside the operation's call, before the first write
request): `GET /playlists/{id}` must answer `owner.id == connection.public.spotify_user_id`, `public ==
false` and `collaborative == false`. A 404 on the pool is `pool_missing` (the user deleted it). In one
transaction the row's `playlist_id` and `last_hash` are set NULL conditionally (`WHERE owner_id = :u
AND id = :pool_id AND playlist_id = :old`) and **`forget_pool`** (below) deletes the user's pool
operation rows, so the retry creates a new pool under a new key and `ref` instead of reusing the
deleted playlist's id; anything else wrong is
`pool_unusable` ("make it private again or stop and set up the working playlist again"; classbeat
never flips a user's privacy itself); 403 is `forbidden`. All three are `failed` with nothing written.
`public: false` and `collaborative: false` are sent explicitly on every create (the library fixes
them, ADR 0009 M-2).

> **Amendment, 2026-10-30 (Architect; issue #198 items 4 and 5):** the text above stays; this aligns it with
> `backend/src/classbeat/playlists/replace.py`.
> - *Final playlists.* The owner check of a final playlist maps a 404 to `not_found`, and a 403, or a
>   playlist that is readable but not owned by the connected user, public or collaborative, to `forbidden`
>   (`replace.py:91-92`, `:155-156`, `:194`). The pool keeps `pool_missing`, `pool_unusable` and `forbidden`.
>   `not_found` is as in ADR 0009 (404 on a playlist addressed by id).
> - *Owner-check failures.* Only two outcomes of the owner check prove that nothing was written and are
>   `failed`: the request was never sent (a `NEVER_SENT` category), or Spotify answered it with a 4xx. A 404 and
>   a 403 have the categories above; any other 4xx is `failed` with the connection's own category
>   (`failure_of(...).category`, for example for a 401 or a 429) (`replace.py:143-157`, `:189-191`). Every
>   other outcome is `uncertain` (`replace.py:192`; `operations/protocol.py:352`): a drop, a 5xx, an unreadable
>   answer, and an allowlist refusal (`OutboundBlocked`) whether before or after sending, because the replace
>   is a multi-request write (`single_request = False`), so `outbound_blocked` is `uncertain` too and
>   `after_request` is not relied on (`egress.py:94-100`).

**Two operation kinds**, both through `perform` (ADR 0005 §7, ADR 0007 §8). Both share-lock the
Spotify connection in the decide transaction and compare its `generation` (ADR 0009 §5) and its
Spotify user id with the ones the call will use, and the pool kind also re-reads `spotify_pool`
(consent present, the same `spotify_pool.id` and `playlist_id`): a disconnect, reconnect or revoked consent in between ends the
job `not_connected` / `consent_missing` before `pending` is written.

**`spotify_create_playlist`** (create only; one request, `single_request = True`, so a refusal before
sending is `failed` and a drop after sending is `uncertain`). Pool key `<job_id>:pool-create` and
final key `<job_id>:final-create`: **one row per job, never one per user**, so a later job (after a
`pool_missing`, a revoked and re-given consent, or a retired uncertain create) always makes a new row
with a new operation id and therefore a new `ref`; only the attempts of one job share a row, and a
`failed` row is re-taken by them only when the create provably did not happen (refused before
sending). Request hash over `{kind, purpose, name, pool_id?, content_hash?}` (the pool's covers the
`spotify_pool.id`, the final's the approved revision's hash). Context `{purpose: "pool"|"final", ref,
pool_id?}` with `ref` = 12 hex characters of the operation id; the playlist's description is
`classbeat ref <ref>` (plus a fixed sentence for the pool). `external_id` = the new playlist id.
`on_succeeded`, in the same transaction: pool → `UPDATE spotify_pool SET playlist_id = :id WHERE
owner_id = :u AND id = :pool_id AND playlist_id IS NULL`; final → insert the `class_playlists` row
(`created`).

**`forget_pool(db, owner)`** (issue OP1, in `classbeat.spotify`) deletes the user's **pool operation
rows**: every `spotify_create_playlist` and `spotify_replace_items` row with `class_id IS NULL`, in any
state (an undecided one is gone, as in `forget_account`, ADR 0009 §7). It runs inside the caller's
transaction and only there: the `pool_missing` NULLing above, the consent revoke (ADR 0009 §9), the
Client ID change (ADR 0009 §7), and `forget_account` (which deletes these rows and the final ones).
A job whose `pending` row is deleted under it cannot record its outcome (the fence finds no row),
writes nothing more and is not retried; its next decide step re-reads `spotify_pool` and ends
`consent_missing` or creates anew.

**Reconciler for create-then-fill (the roadmap's open point).** The create is split from the fill so
the id exists between the two requests, and an uncertain **create** is reconciled by the one thing it
cannot hide: listing the user's playlists (`GET /me/playlists`, 50 per page, at most 20 pages) for
**exactly one** playlist with `owner.id` = the connected user, `public == false` and a description
containing this operation's `ref`. One found → `succeeded` with its id (it is empty or not, the fill is
absolute and repeats safely). None, with all pages read (fewer than the cap), `call_ended_at` set and
`now() ≥ call_ended_at + CLASSBEAT_SPOTIFY_LISTING_LAG_SECONDS` → `failed`; the setting is **unset by
default** (the delay of Spotify's listing is unknown; the owner measures it at live activation, as for
MOWL's) so by default "none found" is inconclusive and the user decides. Two found, a failed listing or
a full 20 pages → inconclusive. A stray empty private playlist of the user's own is the accepted
residue of **Do it anyway** (accepted risk, threat model).

> **Amendment, 2026-10-31 (Architect; issue #198 item 1):** "none found after the lag → `failed`" applies
> only to the user's (or an admin's) **Check again**. The job's own check after an uncertain create leaves
> the row `uncertain` and stops with `uncertain_write`. A job takes over a `failed` create row for a retry
> only when the failure was recorded by the call itself (`resolved_by = 'worker'`); a row resolved by a
> user or an admin ends the job `uncertain_write` and nothing is sent (`backend/src/classbeat/playlists/create.py`).

**`spotify_replace_items`** (absolute: sets the playlist to exactly this ordered list; idempotent in its
outcome, so repeating it is safe, unlike a create). Key: pool `<job_id>:pool:<h16>` (`h16` = first 16 hex
of the URI hash, because a retried stage may propose other candidates, and a hash mismatch under one
key is a bug by ADR 0005 §7), final `<job_id>:final-fill`. Hash `{kind, playlist_id, uris_hash}`.
Multi-request, so `single_request = False`, `bound_seconds` in the context (30 s × (3 + 2 × writes +
reads), cap 30 min), and ADR 0007 §8's `call_may_be_running` and `actionable_at` rules apply to it
(OP1 generalises them from the MOWL kinds to a set of multi-request kinds). The call, in one worker
thread: owner check → `PUT /items` with the first 100 URIs (an empty list clears) → `POST /items` for
the rest → **read back all items and compare the exact ordered ids** (ADR 0009 §11). Equal → success
(`external_id` = playlist id, `result` `{snapshot_id, count}`). Read back and different → `failed`
`playlist_mismatch`: the observed state is definite and the write absolute, so the next attempt just
replaces again (this is A3). Any other failure after the first request → `uncertain`. Its reconciler
reads the playlist the same way (owner check, then items): equal → `succeeded`; readable and different
→ `failed` **if the call is over** (`call_returned` or past `actionable_at`), inconclusive otherwise;
unreadable → inconclusive. `on_succeeded`: pool → `last_hash`, `last_written_at`; final → the
`class_playlists` row becomes `verified` with `content_hash` and `track_count`.

**The user is never asked to resolve a pool write.** Pool rows (`class_id IS NULL`) get no **Check
again** or **Do it anyway**: the operations routes answer 404 for them, so no user action ever re-sends
a pool create under the same operation id and `ref`. A job whose pool write stays uncertain after its
one automatic reconciliation fails `uncertain_write`, and the page words it for a pick ("the working
playlist could not be confirmed; nothing was chosen. Start the pick again"). The **next** pick then
supersedes the row in its own decide step:

- *Pool replace.* Because a replace is absolute, the decide step of the next pool replace by the same
  user marks an older `uncertain` (or orphaned `pending`) pool replace row of that playlist `failed`
  (`resolved_by = 'worker'`) once its `actionable_at` has passed; before that the job requeues without
  an attempt (`operation_busy`). A half-written pool simply gets overwritten.
- *Pool create.* It is a single request, so its call is over when its job is no longer `running` under
  the row's `lease_version`. The decide step of the next pool create (only reached while
  `spotify_pool.playlist_id` is NULL) marks every older `uncertain` or orphaned `pending` pool create
  row of the user `failed` (`resolved_by = 'worker'`) and creates under its own job's key, with a new
  `ref`. If the superseded create did happen, an empty private playlist with the pool's name stays in
  the user's Spotify and is never written to (its id was never stored); that is the accepted risk of
  the stray empty playlist (threat model), extended to the pool. `pool_busy` (below) means no other pick
  of the user is running, so the superseded row has no live holder.

A final playlist's uncertain create or fill
**is** shown (Check again / Do it anyway on `/api/operations/...`), and the page then offers "Create
the playlist again" as a **resume** job (§6), which makes a new create under a new key.

**One pool user at a time.** The pool playlist is shared by all of a user's classes, so two picks could
overwrite each other's candidates between the write and the analysis. `admit` therefore refuses a new
pick (`design_with_songs` or `revise_songs`, mode `pick`) with 409 `pool_busy` while the user has
another such job that is `queued` or `running` and not yet approved. A job in `waiting_for_approval`
no longer needs the pool (the analysis is done and the chosen songs are in its revision), and phase 2
never touches it. Defence in depth: the analysed track list must equal the pool just written (§4).

**Order of one pick**, all in the worker: `pool_search` (model, read-only) → `pool_write` (ensure the
pool playlist: create op if `playlist_id` is NULL; owner check; replace op) → MOWL attach of the pool
(`mowl_attach_spotify_playlist`, key `spotify:<pool id>`, once; the standing consent covers "attach or
refresh it in MOWL", roadmap §2) → `pool_analyse` (§4) → `choose` (model). **MOWL's refresh is an
unverified fact:** whether MOWL's wrapper of a Spotify playlist follows later edits of that playlist
is unknown. The analysis therefore waits, up to `CLASSBEAT_SPOTIFY_POOL_SYNC_WAIT_SECONDS` (default
120), until the tracks MOWL lists have exactly the pool's Spotify ids; if they do not, the stage ends
`pool_not_synced` (retryable once). If the live run shows MOWL never follows edits, the fallback is a
decision for an amendment, not a switch: a **fresh pool playlist per pick** (the same create and replace
code, the old one left in the user's Spotify), recorded in Alternatives.

**Privacy wording** (roadmap §2): the page and the consent say a non-public playlist does not appear on
the profile or in search but anyone with its link can open it. Playlist URLs and ids are never in
events, SSE, logs or model input; the owner sees a link to their own final playlist on their class page
only (`https://open.spotify.com/playlist/<id>` built by the page from the id, never from Spotify data).

### 3. Stages and the agent contract (roadmap §5, ADR 0006 §3)

Two new stages in the table of ADR 0006 §3, both with **read and compute tools only**:

| Stage | Tools | Ends with | Limits (turns / tool calls / corrections) |
|---|---|---|---|
| `pick_pool` | `search_tracks` (read), `check_pool` (compute) | `submit_pool` | 12 / 30 / 3 |
| `choose_songs` | `get_pool` (read), `check_song_list` (compute) | `submit_song_list` | 8 / 12 / 3 |

Stage wall time 300 s, job cost ceiling `CLASSBEAT_SONG_JOB_COST_MICROS` (default 3 000 000, $3, across
all stages and attempts; the live run sets it, ADR 0006 §5's reasoning). The dispatcher refuses any
tool outside the stage's list. No stage gets a write tool: the pool, the playlists and every Spotify
or MOWL call are the backend's, around the stages.

**Schemas** (`classbeat.agent.schemas`, strict, `extra="forbid"`, version `1` in the provenance):

- `Role` = `warmup`, `build`, `climb`, `sprint`, `interval`, `race`, `recovery`, `cooldown`.
- `search_tracks`: `{query: str (1..100, control characters stripped), year_from?: int, year_to?: int}`.
  The backend adds `year:` and builds the request; the model supplies no field filter, URL, market or
  limit. Result (≤ 10 items, ≤ 32 KB): `{ref: int, title, artist, album, year, duration_sec}`. **`ref`
  is an index into a per-job registry** the backend keeps (≤ 300 entries) that maps it to the Spotify
  id; the model never sees an id, URI or URL (T54). Text fields are cleaned (`clean_text`) and cut at
  120 characters.
- `SongPool` = `entries` (`n` to `4n`, at most 120) of `{ref, role}`; `check_pool` and `submit_pool`
  verify refs exist, no duplicate ref or (normalised title, artist), the size, and that every role the
  brief asks for has at least two candidates per slot (the brief's climbs, sprints, races, FTP test).
- `get_pool()` (after the backend wrote and analysed the pool): per usable track `{index, title, artist,
  role, bpm, length_sec, drops, breakdowns}` (MOWL's analysis; a track still without a BPM after the
  wait is left out and counted in `omitted_no_bpm`); fewer than `n` usable tracks is `pool_too_small`
  (not retried).
- `SongList` = `songs` (1..40) of `{source: "pool"|"kept", index: int, role: Role}` plus optional `note`
  (≤ 300). `check_song_list` and `submit_song_list` verify every index, no track twice, the
  **backend-computed total** (sum of lengths less the crossfade overlap of 9 s per transition, ADR 0006
  §3) within ±10 % of `target_sec`, that `kept` is used only when replacing (§6) and keeps its role and
  slot, and that the order starts and ends sensibly (first song `warmup` or `build`, last `cooldown`
  unless the brief has none). A failure returns the errors for correction (limit 3). BPM, lengths and
  titles in the stored list come from the analysed pool, **never from the model's output**.

`n`, `target_sec` and the roles come from the brief (`target_sec` = the asked length, `n` =
`round(target_sec / 240)`, 1..40), computed by the backend and told to the model as facts.

**What may reach the model** (Amendment 1, ADR 0006 §7, one allowlisted builder, `model_input`):
the brief; Spotify-derived **track metadata** (title, artist, album, year, duration); MOWL's
**analysis** (BPM, length, drops, breakdowns); in replace mode the kept songs the same way. **Never**:
tokens, emails, the Spotify user id or display name, Client IDs, playlist names/ids/URLs, track ids or
URIs, MOWL ids, `Retry-After` and provider error texts. The planted-value test of ADR 0006 §7 is
extended: the fake Spotify returns a display name, playlist names and ids and an email-shaped string in
every place; none may appear in what the provider adapter would send (titles do, being allowed).

**Prompt injection.** Titles, artists, albums and the user's text are untrusted (T8, T56, new T69).
Tool results are fixed-shape JSON and the prompt says text inside them is data. What matters more:
the model cannot write, cannot name an id (only registry indexes), cannot fetch (the search query is
text, the request is built by the backend from a fixed path), its output is validated and then
**shown to the user as plain text for approval**, and the stored list is built from the analysis, not
from its words. Adversarial fake scripts: a title that says to approve the list, to add a track by an
id, to ignore the roles, an artist field of 120 characters of instructions, a title with control
characters and bidi marks; each must end with a valid list or a correctable error and no side effect.

**Versions.** Each stage's `prompt_version` (SHA-256 of its system prompt and tool schemas, ADR 0006
§6) and the schema version go into the `song_list` revision's provenance (`stage_versions`).

### 4. The job kinds and the stage graph

**`design_with_songs`** (a new class with songs picked by Claude). `JobInput`: `{class_id | title,
brief, mode: "pick" | "resume", resume?: {song_list_revision_id}, seed?: {song_list_revision_id,
replace: [int]}, mowl_connection_id, spotify_connection_id}` (`CONNECTIONS = ("mowl", "spotify")`).
`consumes_credit = True` (it is the class's credit, as `design_draft`). `admit` (inside the create
transaction, under the owner lock): `song_picking_off` unless the setting is on; both connections
valid (the generic check) and `consent_missing` unless the user has a `spotify_pool` row; `pool_busy`
(§2) for `pick`; the brief limits of `design_draft`; for `seed` the list must be a `song_list`
revision of the class (the newest, not approved, of a cancelled or expired job) and `replace` indexes
valid; for `resume` the receipt rule of §6. Nothing is stored on a refusal.

```
phase 1 (until AwaitApproval)                   phase 2 (after Approve, or mode "resume")
pool_search ─ pick_pool (model)                 prepare ─ revalidate (below)
pool_write  ─ ensure pool, owner check,         create  ─ spotify_create_playlist
              spotify_replace_items                       (skipped if a verified class_playlists row exists)
              + MOWL attach of the pool         fill    ─ spotify_replace_items (owner check, write, read back)
pool_analyse─ MOWL analysis, waits for sync     attach  ─ mowl_attach_spotify_playlist (final)
choose      ─ choose_songs (model)              music   ─ the design music step on the final playlist
store       ─ song_list revision + hash         design  ─ the design stage (ADR 0006 §3)
            ─ AwaitApproval                     store   ─ draft revision, pointers, Complete
```

**Phase 1 stores** (the `AwaitApproval` store, one fenced transaction): the `song_list` revision (§5)
with its hash, `jobs.approval_*`, `expires_at`; the pool analysis itself is **not** stored (ADR 0006 §2:
it lives in memory for the run; a retried phase 1 starts the stage again, and the cost ceiling counts
every attempt). Each model stage is a checkpoint-bounded run of ADR 0006 §1; the MOWL reads and the BPM
wait use the design music step's budget and thread rules (`MUSIC_BUDGET_SECONDS`, no nested fenced
sessions).

**Phase 2 `prepare`** (fenced, the same revalidation discipline as ADR 0007 §5.2): the approved list is
still the newest `song_list` revision of the class, its stored `content_hash` equals the SHA-256 of its
canonical content recomputed now, the Spotify user id recorded in the revision equals the connection's
(`account_changed` otherwise), both connections are `connected`, the class exists and is the user's,
`consent_missing` is not needed in phase 2 (the pool is not touched). The playlist's tracks and order
come **only from the stored revision**, never from input.

**`music` and verification of the final playlist**: after the attach, the existing music step analyses
the final playlist; its track list must equal the approved list (fingerprint of the ids and order);
otherwise `playlist_mismatch`. Then the design stage runs unchanged on that music, and the final
transaction (K1's compare-and-set of ADR 0007 §2) stores the draft revision with `music.song_list_id`,
sets `classes.music_source` to the final playlist (`origin: "song_pick"`), records
`class_playlists.mowl_playlist_id`, and completes. Failures after approval keep what exists (the
verified playlist, the link) and a retry reuses succeeded operations by key.

**`revise_songs`**: §6. Both kinds are registered in the worker's `KINDS` and refuse to start when the
Spotify allowlist or the pool settings are missing (the worker startup check).

### 5. The `song_list` revision and the approval (roadmap §4)

A `class_revisions` row with `kind = 'song_list'` (immutable, ADR 0007 §2 rules; never current, never
revised or saved as a draft). `content` (≤ 64 KB, versioned `v: 1`):

```
{"v": 1, "mode": "pick" | "replace", "target_sec": 2700, "total_sec": 2688, "crossfade_sec": 9,
 "songs": [{"spotify_id": "...", "title": "...", "artist": "...", "role": "climb", "bpm": 92.0,
            "length_sec": 241, "new": true}],
 "omitted_no_bpm": 2, "note": "..."}
```

`content_hash` = SHA-256 of its canonical JSON (the same function as drafts). *Amended by ADR 0013 §9
(2026-10-30):*
- content `v: 2` adds `cover` to each song: the album image URL, host-pinned (checked before
  storing and before returning), or `null`;
- `v: 1` stays readable;
- `content_hash` covers the field;
- the URL is never taken from model output and never goes to the model.

`music` =
`{source: "claude_pick", fingerprint, spotify_user_id}`; `provenance` as ADR 0006 §6 plus
`origin: "design_with_songs" | "revise_songs"` and `stage_versions`. The page renders title, artist,
role, BPM, length and the total time **from this content** (`GET` below), never the ids.

**Approve** is the existing endpoint, `POST /api/jobs/{id}/approve {revision_id, content_hash}` (CSRF,
no recent authentication: the user's own work, ADR 0005 §2). ADR 0005 §2's checks already give every
roadmap §4 refusal, each starting nothing: not `waiting_for_approval` or past `expires_at` →
`expired`; not the job's `approval_revision_id` or not the newest `song_list` revision of the class →
`superseded`; hash differs → `hash_mismatch`; the connections of the kind's `CONNECTIONS` not valid
(now **MOWL and Spotify**) → `not_connected`; the per-user limit → `busy`; the credit seam → `quota`.
M4 adds one kind-specific refusal through a new **`JobKind.approve_check`** seam (issue SJ1, next to
K1's `admit`): `account_changed` when the connected Spotify user id is not the one the list was made
for. Approving again after the fact (same revision and hash, `approved_at` set) is a 200 and a no-op.
Applied once, the job goes `queued`, `attempt = 0`, and any worker's next claim runs phase 2.

**Ask for changes** is not a state of the job. The page offers, per song, a "replace" mark and an
optional note; pressing it **cancels the waiting job** (immediate, credit released, ADR 0005 §2) and
creates a new `design_with_songs` job with `seed = {song_list_revision_id, replace: […]}` and the
note appended to the brief's free text (the same 2000-character limit). The seed's songs the user
kept are fixed in the new list (`source: "kept"`), the replaced slots get a new pool of `3 × slots`
(at least 6) candidates. The cancelled job's list is superseded by the new one. The two calls are
sequential; `class_busy` makes the second wait for the first.

### 6. Song changes in revisions, own music, resume

**`revise_songs`** (issue SJ3): input `{class_id, base_revision_id, replace: [int] | "all", request,
mode, resume?, mowl_connection_id, spotify_connection_id}`, `consumes_credit = False`. `admit` as
`revise_draft` (ADR 0007 §3: `stale_base`, `revision_limit` — it makes one chat revision —, `chat_full`,
`invalid_input` for a base that is not a draft of the class, the user's message row) plus the
song-picking checks of §4 and `not_song_picked` when the base draft's `music.song_list_id` is absent
(a class designed on the user's own playlist keeps using `revise_draft`; changing its music is a new
class). Phase 1 is §4's with the current picked songs as `kept` (those not in `replace`; `"all"` keeps
none) and the same limits; the approval page marks the changed songs `new`. Phase 2 creates a **new**
final playlist with the full list (kept and new, in the approved order; the earlier playlist stays in
the user's Spotify and its `class_playlists` row stays as history, listed on the page), attaches it,
runs the music step, then ADR 0007 §3's `revise` stage with the base draft, the new music, the
user's requests along the parent chain and **a fact that the songs changed** (the model re-times the
intervals to the new music). A `revise_draft` request that asks for other songs gets the stage prompt's
instruction to say that "Change songs" is the way and to change nothing else.
*Amended (2026-10-31, issue #278):* a request that asks **only** for other songs has nothing to
revise, and the stage would refuse the unchanged draft as `invalid_output`, so the pointer would
never reach the user. The `revise` stage therefore has a second final tool, `songs_requested` (no
input; not offered to `revise_songs`, whose request is the songs). The stage ends `SongsRequested`
and the job ends **`use_change_songs`** (a new `jobs.error_category`, migration 0009): no
revision, no assistant message, no revision counted against `CLASSBEAT_REVISIONS_PER_CLASS`, the
user's request stays in the chat, and the run's usage is recorded as for any run. The class page
shows "This request is only about the songs. Use Change songs to pick other ones." with a Change
songs button. A request that mixes songs and other changes still ends `submit_draft`: the design
part is applied and the note says "Change songs" is the way for the rest. The smoke set has the
case `revise_other_songs` (suite version 5). The stage's `prompt_version` changes with this.

*Contract, issue SJ3 (2026-10-31).* `POST /api/jobs` with `kind: "revise_songs"` takes one of
two inputs (both with `mowl_connection_id` and `spotify_connection_id`):

- **pick** (Change songs): `{class_id, base_revision_id, mode: "pick", replace: [int] | "all",
  request}`. `replace` is positions in the base draft's picked songs (0-based, each once, at least
  one) or `"all"`; `request` is 1 to 2000 visible characters. The user's message row is inserted
  with the job. Phase 1 searches with the class's stored brief whose free text leads with the
  request (cut to the brief's 2000 characters).
- **resume** (Create the playlist again): `{class_id, base_revision_id, mode: "resume", resume:
  {song_list_revision_id}}`, with **no** `replace` and **no** `request` (a 422 if either is sent,
  empty or not): the resume runs phase 2 for the list a `revise_songs` job approved, with that
  job's request (its message row; no new one). `base_revision_id` is the class's current revision
  and must be the approved job's base (`stale_base` otherwise).

Refusals (409): `song_picking_off`, `invalid_input`, `stale_base`, `revision_limit`,
`not_song_picked`, then for a pick `chat_full`, `not_connected`, `consent_missing`, `pool_busy`,
`write_unresolved` (an undecided final-playlist write of the class), `invalid_input` (a `replace`
slot outside the list), and for a resume `not_connected`,
`receipt_invalid`, `stale_base`, `account_changed`, `write_unresolved`. A receipt is resumed by the
kind that approved it: a `design_with_songs` resume names a list a `design_with_songs` job
approved, a `revise_songs` resume one a `revise_songs` job approved; and "Ask for changes" (`seed`)
seeds `design_with_songs` only from a list `design_with_songs` made. The final transaction moves
`current_revision_id` and `music_source` (the new final playlist) together by compare-and-set from
the base; the draft counts as a chat revision (`revision_limit`).

*The stored brief, issue SJ3 (2026-10-31).* `GET /api/classes/{id}` returns `brief`: the class's
stored brief (`classes.brief`, set with its first design), or before that the brief of the class's
newest `design_with_songs` job; `null` when neither exists. The owner's own rows only (the route's
row security and `owner_id`). The page seeds Ask for changes and a `design_with_songs` resume from
it instead of from browser storage; the job inputs keep `brief` as in §4.

**Music the user already has** (a MOWL playlist, a Swedebeat mix) skips all of this: `design_draft`
is unchanged, needs no Spotify connection, and `song_picking` being off affects nothing there.

**Resume.** `mode = "resume"` (both kinds) starts at phase 2 for a list the user **already approved**:
for failures after approval (a Do-it-anyway on an uncertain create or fill, `playlist_mismatch`, a MOWL
or model failure that ended the job, expiry of a *started* phase 2). `admit` accepts it only against a
**receipt**: a job of the same class and owner, in a terminal state other than `completed`, whose
`approval_revision_id` is the named `song_list` revision, `approved_at` is set and the revision is
still the newest; the new job reserves credit again (the failed one released it), skips the creation
of any verified `class_playlists` row for that revision, and otherwise creates anew. No new approval is
needed because the user's click is the receipt; no other route to phase 2 exists, and an expired,
superseded or never-approved list cannot be resumed. **A receipt is spent once its list is done:**
`admit` (under the owner lock) also refuses `receipt_invalid` when any job of the class whose
approved or resumed revision is that `song_list` revision is `completed`, or is still `queued` or
`running` (another resume in flight). So after a resume completes, the original failed job is no
longer a receipt, and a second resume cannot reserve credit and attach, analyse and design again; after
a resume that **failed**, the user may resume once more against the same receipt.

### 7. Waiting, expiry, cancel, credit

All of ADR 0005, specified once more where M4 touches it. While `waiting_for_approval` the worker
holds no slot and the per-user limit does not count the job; the state is the revision and the job
row, so a restart changes nothing. After 7 days (`CLASSBEAT_APPROVAL_TTL_HOURS`) `queue_expire` sets
`expired` and **no model, MOWL or Spotify call is made again**, including no cleanup of the pool
playlist; the reserved credit is released by settle; approving is refused `expired`. A cancel while
waiting is immediate. The pool playlist keeps the last candidates until the next pick overwrites it
or the user revokes consent (then it is left alone; §8). The credit commits only when a draft revision
exists (`completed`); a failure after approval releases it and a resume reserves again.

### 8. Retention and deletion (Amendment 1, ADR 0010, ADR 0007 §11)

What M4 stores that is Spotify-derived, and how each goes (guard 10's `RETENTION` registry gets these
entries in SP1; its plant-and-delete test covers them):

| Data | Where | Deleted with |
|---|---|---|
| Track titles, artists, ids, BPM, lengths, order of the chosen songs; album cover URLs (`cover`, ADR 0013 §9) | `class_revisions.content` (`song_list`), the draft's `music` | the class (cascade), the account |
| Final playlist id and name | `class_playlists` | the class, the account, **Disconnect Spotify** (detached first, then with the class) |
| Pool playlist id, Spotify user id, consent | `spotify_pool` | the account, **Disconnect Spotify**, a Client ID change, **revoking the consent**; the playlist id alone also on `pool_missing` |
| Client ID, Spotify user id | `connections.public` | Disconnect, the account |
| Tokens | `connections.secret` (ciphertext) | Disconnect, the account, `keys wipe` |
| Playlist ids, `ref`, hashes of the pool kinds (`class_id NULL`) | `external_operations.request_context`, `external_id` | `forget_pool` (§2) in the same transaction as revoking the consent, `pool_missing` and a Client ID change; the account; Disconnect and an account change (`forget_account`) |
| Playlist ids, `ref`, hashes of the final kinds | `external_operations.request_context`, `external_id` | the class (`class_id` cascade), the account, Disconnect and an account change (`forget_account`) |
| Seed ids, brief, the user's change request | `jobs.input`, `class_messages` | the class |
| Transient: OAuth flows, search registry, pool analysis | `spotify_oauth_flows` (≤ 10 min); memory | use or expiry; the run's end |

**The shared analysis question: M4 stores none.** The pool's analysis is for one user's brief and lives in
memory for the run; the chosen songs' BPM and lengths are in the `song_list` revision (owned, deleted
with the class); phase 2 analyses the final playlist again through the existing music step. Sharing
across users would save one MOWL analysis of a playlist that differs per user and brief and would
bring the whole purge machinery of ADR 0007 §11 (system table, `SECURITY DEFINER` purge, open-job
protection) for no M4 benefit. So **Amendment 3's trigger does not fire in M4**: no purge is built, and
the roadmap records that the purge moves again to the first milestone that stores a shared analysis
(roadmap Amendment 5). The requirements list of ADR 0007 §11 stays the contract for that day.

**Spotify-side content is the user's own.** classbeat never deletes a playlist in the user's Spotify
(the pool or a final one): deleting a class, revoking consent or disconnecting leaves them, and each
of those dialogs says so and where to delete them. What classbeat controls, its own copy, is gone;
what stays is in the user's account under their terms. The deletion paths are made executable: class
delete (ADR 0007 §12, now also listing the playlists left in Spotify), `DELETE …/pool-consent`,
`DELETE /api/connections/spotify` (ADR 0009 §7) and account deletion are each tested to leave no
Spotify-derived row (the registry test), and a test asserts that none of them makes a request to
Spotify.

### 9. API and UI

New or changed routes (signed in, CSRF on writes, 404 for foreign or unknown ids, no Spotify id or
token in any body except where noted; ADR 0009 §2 has the connection routes):

| Route | Does |
|---|---|
| `POST /api/jobs` with `design_with_songs`, `revise_songs` | §4, §6; 409 reasons `song_picking_off`, `consent_missing`, `pool_busy`, `class_busy`, `stale_base`, `revision_limit`, `chat_full`, `not_song_picked`, `invalid_input`, `not_connected` (and `hash_mismatch`-style `receipt_invalid` for resume) |
| `POST /api/jobs/{id}/approve`, `/cancel` | unchanged shape; new refusal `account_changed` |
| `GET /api/classes/{id}` | gains `songs`: `{state: none|waiting|approved, job_id, revision_id, content_hash, expires_at}` and, for the current draft, `playlist: {name, link?}` (the owner's own link); and `brief` (§6, SJ3) |
| `GET /api/classes/{id}/revisions/{rid}` | for a `song_list`: the songs as in §5 without ids, `total_sec`, `target_sec`, `new` marks, the note as plain text |
| `GET /api/jobs/{id}` | the existing state; failures carry the category and the plain sentence (ADR 0009 §8 and below) |
| `/api/operations/...` | unchanged; the final-playlist kinds appear with `actionable_at` |

Plain sentences added for the page: `consent_missing` ("allow the working playlist in your Spotify
settings first"), `pool_unusable` ("your working playlist was changed: make it private again or set it
up again"), `pool_missing` (retried by itself), `pool_not_synced` ("MOWL has not caught up with the
working playlist; try again in a minute"), `playlist_mismatch` ("Spotify's playlist did not match the
approved songs; nothing was designed. Create it again"), `pool_too_small` ("not enough usable songs
were found; try a wider style or era"), `account_changed`, `pool_busy` ("another song pick is still
running").

**Frontend** (issues SC2, FE3): the **Spotify wizard** and connection card (ADR 0009 §2) on the account
and onboarding pages; the new-class form gains the music choice **"Let Claude pick the songs"** (needs
Spotify connected and the consent, otherwise it links to the wizard); the **Designing…** page shows the
phase-1 stages in words (finding candidates, writing the working playlist, MOWL analysing, choosing)
over the existing SSE; the **Songs for approval** page (role, BPM, length, total time against the
asked time, `new` marks, per-song replace marks, **Approve songs**, **Ask for changes**, the expiry
date, the privacy sentence about the final playlist, and its states `superseded`, `expired`,
`not_connected`, `account_changed` in words); **Change songs** on the class page; the uncertain panel
for a final-playlist write with "Create the playlist again"; the Spotify reconnect prompt on any
`invalid_grant`. Everything rendered as text; no ids shown.

### 10. Settings

| Setting | Default | Meaning |
|---|---|---|
| `CLASSBEAT_SONG_PICKING` | off | the feature switch (ADR 0009 §2); the owner turns it on |
| `CLASSBEAT_SONG_JOB_COST_MICROS` | 3 000 000 | job ceiling of the two kinds (§3) |
| `CLASSBEAT_SPOTIFY_FLOW_TTL_SECONDS` | 600 | OAuth flow life |
| `CLASSBEAT_SPOTIFY_REFRESH_MARGIN_SECONDS` / `_REFRESH_TIMEOUT_SECONDS` | 120 / 10 | refresh before expiry / its total deadline |
| `CLASSBEAT_SPOTIFY_CALLS_PER_JOB` | 300 | request budget per job |
| `CLASSBEAT_SPOTIFY_LISTING_LAG_SECONDS` | unset | lets a create reconcile `failed` (§2); at most 86 400 |
| `CLASSBEAT_SPOTIFY_POOL_SYNC_WAIT_SECONDS` | 120 | wait for MOWL to show the pool (§2) |

Existing settings used as they are: the agent defaults, `CLASSBEAT_APPROVAL_TTL_HOURS`,
`CLASSBEAT_JOBS_PER_USER`, `CLASSBEAT_REVISIONS_PER_CLASS`, `CLASSBEAT_CHAT_*`.

### 11. Rollout: technical completion, then live activation

**Technical completion** (the roadmap's M4 "done when") is tested against the fakes only; agents run
no live Spotify, MOWL, Swedebeat or model call, and the `CLASSBEAT_SONG_PICKING` switch stays off in
every default. **Live activation** is the owner's, one authorised step at a time, logged under "Live
activation log" in `docs/status.md`; agents may run a step only with the owner's go-ahead for that
step. The checklist (issue T3 writes it into `docs/runbooks/m4-live-activation.md`):

1. *Owner, in the Spotify dashboard:* create a Development Mode app, register the callback URL, add
   the owner's Spotify account to User Management (the app owner needs Premium).
2. Turn `CLASSBEAT_SONG_PICKING` on in the owner's local stack. First real connect (reads `/me`),
   then the pool consent, which **creates one private playlist** in the owner's account.
3. One pick: a few real searches, one pool write, MOWL attach and analysis. **Measures:** does MOWL
   follow later edits of the playlist (§2 `pool_not_synced`); the real shape of `/items` (`item` vs
   `track`), its page size and the owner/privacy fields; the first `/me` 403 and the token endpoint's
   `invalid_grant` body for a user off the allow-list and a revoked token (ADR 0009 assumptions); how
   soon a new playlist appears in `/me/playlists` (sets the listing lag). Also the facts ADR 0009's
   Context takes from secondary sources: the `/items` path and field rename, that contents are
   returned only for owned or collaborative playlists, that `POST /me/playlists` creates, that search
   `limit` is at most 10, and the Development Mode allow-list size and Premium rule (step 1 and 2).
4. One Approve: the final playlist, its read-back verification, the attach, the design.
5. One Change songs.
6. Real cost per song-picked class (the ceiling of §3 and `PRICES_VERSION`, as ADR 0007 §10).
7. Disconnect, and the check that the playlists are left in Spotify as the dialog says.

Each owner-authorised live step is a write to a real Spotify account (a private playlist) or a read of
it; none is done by an agent unprompted. The first testers are invited only after steps 1 to 7.

### 12. Threat model and tests

New rows T66 to T78 ([threat model](../security/threat-model.md); ADR 0009 §14 names its own): T66
callback mix-up, T67 token theft and refresh, T68 pool or playlist overwrite (the wrong playlist),
T69 injection through track metadata, T70 approval replay or a superseded list, T71 BYOK
misconfiguration, T72 quota and 429 abuse, T73 SSRF through Spotify URLs, T74 half-written playlists,
T75 two jobs on one pool, T76 Spotify-derived data surviving deletion, T77 stale connection used after a
reconnect, T78 playlist privacy drift. This ADR's own rows are T68 to T70 and T74 to T76 (T78 is
shared); the others are ADR 0009's. Four accepted risks are added: a stray empty playlist after a
**Do it anyway** or a superseded uncertain pool create, the pool's last candidates staying in the
user's Spotify, the plaintext PKCE verifier for at most 10 minutes, and the Spotify Developer Terms
risks of ADR 0010 for the beta circle.

**Tests** (fakes only; ADR 0009 §13 lists the connection ones), each named in an issue's criteria:

- **A1 to A3**: from the brief to a draft with one Approve; the created playlist is `public: false`,
  `collaborative: false`; the fake holds **exactly the approved ids in the approved order** (pages of
  50 over 120 items, both item shapes); a fake that drops, duplicates or reorders one track makes the
  read-back `playlist_mismatch`.
- **Approval**: superseded (a newer list), expired (`expires_at` in the past), hash mismatch, another
  user's job or revision (404), a Spotify disconnect or account change while waiting, a MOWL
  disconnect, quota refused: each starts nothing (counted at the fakes); a double click and a second
  tab are one approval; a replay after completion is a 200 no-op; an expired job makes **no** call.
- **Ownership**: a pool or final playlist that is public, collaborative, owned by another user, gone,
  or whose id was changed in the database to another user's id: nothing is written; a model-supplied
  or browser-supplied id is never used (the model sees only registry indexes); no name-based write.
- **Writes and partial failure**: failure injection at every request of a create, a fill and a pool
  replace (refuse before send, drop after send, answer without id, partial PUT): the states of §2; the
  create reconciler's table (one match, none with and without the lag setting, two, a failed or full
  listing); crash mid-fill then the next attempt never creates a second playlist; a stale call that is
  still running blocks the next pool replace until `actionable_at`; the supersede rule for an
  uncertain pool replace and an uncertain pool create (the next pick creates under its own key with a
  new `ref`, and no Check again / Do it anyway route accepts a pool row); **resume** after Do it anyway
  creates once more and designs; resume refuses an expired, superseded or never-approved list, and a
  receipt whose list already has a `completed` job or a resume in flight.
- **Pool lifetime**: after `pool_missing` the retry makes a new create row and a new playlist (the
  deleted id is never returned from an old row); after a consent revoke and a new consent the next pick
  creates a new pool and sends **no** request to the old playlist id; the revoke, the `pool_missing`
  NULLing and a Client ID change leave no pool operation row (in the same transaction, a crash test
  between the statements finds both or neither).
- **Pool**: two picks of one user: the second gets `pool_busy`; two users concurrently each get their
  own pool; a MOWL that shows the old tracks gives `pool_not_synced`, one that shows the new gives a
  list; unknown-BPM tracks are left out and `pool_too_small` fires.
- **Model data**: the planted-value test; injection scripts; refs outside the registry, unknown tools,
  oversized inputs; limits and cost ceiling; a song list that is too long or too short is refused with
  the errors.
- **Isolation**: two users on **different Client IDs** with different tokens, music and playlists run
  the whole flow concurrently; every fake request carries its own user's token and Client ID; 404 on
  the other's every id; one user's playlist is never overwritten by the other's job; the wrong-account
  callback (ADR 0009 §13).
- **Retention**: the registry test and plant-and-delete for the new tables; class delete, consent
  revoke, Disconnect and account deletion leave nothing and make no Spotify request.
- **E2E** (Playwright against the compose stack with `e2e/fake-spotify`, fake MOWL and the fake
  model): the wizard (callback copy, wrong Client ID, cancel, allow-list refusal), consent, a pick
  and approval, superseded and expired lists, a partial failure and resume, Change songs, Disconnect,
  and two users on different Client IDs.

## Alternatives

- **Pool created and written as one operation.** Rejected: a failure between the create and the fill
  loses the id and the user ends with a stray playlist and a retry that makes another. Two operations
  put the id on a row between the requests, and the fill is absolute, so it repeats safely.
- **Reconciling an uncertain create by name only.** Rejected: a same-named playlist would be taken for
  ours; the unique `ref` token plus the owner and privacy checks identifies exactly this operation.
- **A fresh pool playlist per pick.** Not taken: roadmap §2 wants one managed pool, a fresh one per pick
  leaves one stray playlist per class in the user's Spotify; kept as the documented fallback if MOWL
  does not follow edits (§2).
- **Sharing the pool analysis across users.** Rejected for M4 (§8).
- **Letting the user approve inside the job's `waiting_for_approval` and also later "edit" the list.**
  Rejected: the list is immutable and hash-bound; changes are a new list and a new approval.
- **Resuming after an uncertain create by adopting the found playlist without a receipt.** Rejected:
  an approval is the user's click on one hash; a resume needs a prior approved job as proof.
- **Model-chosen search filters, markets or limits.** Rejected: the backend builds the request; the model
  supplies text and years.
- **One `song_picking` job kind with a mode for revisions.** Rejected: a revision starts from a draft
  and ends in the `revise` stage with its message row and revision limit; two kinds keep input, admit
  and tests small, sharing the phase code.
- **Cleaning the pool playlist after the choice.** Rejected: one more write that can fail, and none is
  allowed after an expiry anyway; the pool is a working copy that the next pick overwrites.

## Consequences

- M4 is fourteen issues (§13); eight need an Opus Developer (Spotify OAuth and egress, the connection,
  operations, the two jobs and the library release), and every PR gets a fresh Reviewer (Opus except
  frontend, docs and E2E; AG1, whose data-boundary review is Opus by the "when in doubt" rule; and
  CL1 and SEC1, whose cross-user 404 and deletion tests are tenant isolation, Opus by the review gate).
- The shared operation protocol grows additively: a set of multi-request kinds (the `bound_seconds`,
  `call_may_be_running` and `actionable_at` rules apply to Spotify's two kinds), the pool supersede
  rule and `generation` in place of `wrapped_dek` for connection guards; M3a/M3b tests pass unchanged.
- `JobKind` gets one more optional seam, `approve_check`; ADR 0005 §2's approval is otherwise as built.
- The user sees uncertain writes rarely and is never asked to resolve a pool write. A **Do it anyway**
  on a final playlist, or a superseded uncertain pool create, can leave an empty private playlist in
  the user's Spotify.
- A pick takes minutes (searches, MOWL analysis, a model turn or more) before the approval page; the
  per-user `pool_busy` rule means one pick at a time.
- Real behaviour of MOWL on a changed Spotify playlist, the `/items` shapes and the 403 and token
  errors are assumptions to check at live activation; the fakes and readers isolate each.
- A mid-flow reconnect of MOWL or Spotify fails the job `not_connected` and the picked list survives
  only as an unapproved (or approved, resumable) `song_list`.
- Roadmap Amendment 5 (the purge moves again, the one-pick-at-a-time rule); threat model T66 to T78.

## M4 issue breakdown

One issue per PR, milestone "M4 Song picking and Spotify". "Opus" = security-critical with an Opus
Developer (AGENTS.md: Spotify OAuth, approvals and external writes, encryption and keys, jobs, egress);
the rest a Sonnet Developer. No issue is startable before ADRs 0008 and 0009 are accepted. Fakes only.
`L1` is done by the **local Architect session** (the library repo is private); the issues in
`magpern/mowl-workouts` are the items M-1 to M-6 of ADR 0009 §12.

| # | Issue | Owner | Depends on |
|---|---|---|---|
| L1 (#173) | `mowl-workouts` v0.6.0 (M-1 to M-5, issues in the library repo, local Architect), then the wheel pinned in classbeat (`build-mowl-wheel.sh`, `uv lock`, the 0.5.0 wheel removed) | **Opus** (local Architect) | none |
| SP1 (#174) | Migration `0006_spotify`: `spotify_oauth_flows`, `spotify_pool`, `class_playlists`, `connections.generation` (and `hold`/flips compare it), the new kinds and categories, retention entries and path, RLS and cascade tests | **Opus** | none |
| SP2 (#175) | Spotify egress and fakes: worker and web allowlists, `classbeat.spotify.http`, guard-test updates, `tests/spotifyfake.py` and `e2e/fake-spotify` with failure injection, compose e2e wiring | **Opus** | none |
| SC1 (#176) | Spotify connection backend (ADR 0009): wizard routes, flow table use, callback rules, verification, storage, refresh with lock and rotation, `spotify_call`, error categories, Disconnect/Client ID/account change with `forget_account`, pool consent, rate limits, feature flag | **Opus** | SP1, SP2, L1 |
| SC2 (#177) | Frontend: Spotify wizard, connection card and plain error sentences, pool consent step, reconnect prompt | Sonnet | SC1 |
| OP1 (#178) | Operation kinds `spotify_create_playlist` and `spotify_replace_items`: calls, owner check, read-back verification, reconcilers (create by `ref`, replace by comparison), multi-request set, pool supersede (replace and create), per-job pool create key, `forget_pool`, listing-lag setting, generation guard | **Opus** | SP1, SP2, SC1, L1 |
| AG1 (#179) | Stages `pick_pool` and `choose_songs`: schemas, tools, search registry, limits, prompts and versions, `model_input` additions with the planted-value test, fake scripts (incl. injection), smoke cases | Sonnet (Opus Reviewer) | SC1, L1 |
| SJ1 (#180) | `design_with_songs` phase 1: input, `admit` (`pool_busy`, consent), pool write and MOWL analysis with sync wait, `song_list` revision and hash, `AwaitApproval`, the `approve_check` seam (`account_changed`), expiry/cancel tests | **Opus** | AG1, OP1 |
| SJ2 (#181) | `design_with_songs` phase 2 and resume: `prepare`, create, fill with read-back, attach, music and fingerprint check, design, store, `class_playlists`, receipt rule | **Opus** | SJ1 |
| SJ3 (#182) | `revise_songs`: admit, kept/replace choose, new final playlist, `revise` stage with changed music, limits | **Opus** | SJ2 |
| CL1 (#183) | Class and revision API: song state, `song_list` view, playlist link, class delete and Disconnect dialogs' data (playlists left in Spotify), retention tests, 404 tests | Sonnet (Opus Reviewer) | SJ1 |
| FE3 (#184) | Frontend: "Let Claude pick the songs", Designing stages, Songs for approval page and its states, Ask for changes, Change songs, uncertain panel with Create again | Sonnet | CL1, SC2, SJ2 |
| SEC1 (#185) | Backend M4 done-when suite: two users on different Client IDs end to end, cross-user playlist overwrite, every error category, wrong-account callback, whole-flow planted-value and injection tests, partial-failure matrix, no-request-on-deletion test | Sonnet (Opus Reviewer) | SJ3, SC1 |
| T3 (#186) | E2E with fakes (§12) and `docs/runbooks/m4-live-activation.md` (§11), status and AGENTS.md developer notes for Spotify | Sonnet | FE3, SEC1 |

Order: L1, SP1, SP2 in parallel; SC1; then SC2, OP1, AG1 in parallel; SJ1; SJ2 and CL1; SJ3 and FE3;
SEC1; T3 last. M4 is done technically when SEC1 and T3 are green (roadmap M4 "done when": E2E with
fakes meets A1 to A3 including superseded and expired approvals, the ownership check, partial-failure
reconcile and every error category; concurrent users with different Client IDs never mix; a callback
cannot connect the wrong account; one user's playlist is never overwritten by another; exact ids and
order verified against the current API shape). Live activation (§11) is separate and the owner's.
