# ADR 0036: The SwedeBeat studio: classbeat designs the music and the workout, and produces the outputs

Status: Accepted (owner direction and plan acceptance, 2026-11-15; roadmap Amendment 24; milestone "SR").
Implementation is on fakes. Every live output stays off until its gates in §10 hold.

Supersedes:
- [ADR 0021](0021-use-part-of-a-song.md) decisions 1 to 3 for SwedeBeat. Part of a song is built here.
  Spotify stays not allowed.
- [ADR 0016](0016-swedebeat-music.md) §6 ("every replace round is a pick the user approves") for studio
  classes.
- [ADR 0016](0016-swedebeat-music.md) §7, the manual hand-off, for studio classes only. It stays as the
  fallback.
- [ADR 0035](0035-swedebeat-gateway.md) §7 ("nothing is written to SwedeBeat") and §1's 150 s clamp for two calls, within the gateway paths
  of §8 here.
- The draft-playlist route of the closed PR #1089, which does not work: API-created playlists are invisible
  in the app unless public.

Amends:
- [ADR 0008](0008-song-picking.md): for a studio class, the final Spotify playlist is an output of the
  approved draft (§6).
- [ADR 0007](0007-classes-revisions-and-saving.md) §2: a studio draft's `content_hash` covers the mix spec (§1).
- AGENTS.md "Everything we create in a user's accounts is private": the user may choose published SwedeBeat
  outputs (§7, roadmap Amendment 24).

Related:
- [ADR 0002](0002-tenant-isolation.md) (isolation) and [ADR 0005](0005-jobs-and-leases.md) (jobs,
  approvals, external operations).
- [ADR 0007](0007-classes-revisions-and-saving.md) (revisions, saving) and
  [ADR 0010](0010-live-activation-permissions.md).
- [ADR 0034](0034-classbeat-player-connection.md) (classbeat-player) and
  [`docs/providers/swedebeat-mixing-findings.md`](../providers/swedebeat-mixing-findings.md).
- The gateway contract `docs/api.md` of `magpern/sb-gateway-service`.

## Context

classbeat reaches SwedeBeat only through the owner's gateway (ADR 0035). Until now a SwedeBeat class ended in
a manual step: classbeat matched a Spotify selection to the catalogue, and the instructor built the mix in the
SwedeBeat app and pressed Merge & Publish there (ADR 0016 §7). Part of a song (#580) was blocked because no
source could play it.

The gateway's contract gives what is needed without new gateway logic:
- **`GET /tracks/{reference}/download`** streams a catalogue track's MP3.
- **`POST /playlists`** registers a playlist of catalogue tracks with a start, an end and a crossfade per
  track. The gateway fetches the files itself and stores the cuts as metadata, which the app applies.
- **`POST /playlists/{id}/publish`** sets `public = 1` for good. Until then the playlist is invisible in the
  app (owner, 2026-11-15).
- **`POST /playlists/merge-publish`** registers one already rendered MP3 as a published mix. That is what MOWL
  lists *(inferred; gate L1)*.
- SwedeBeat has no delete.

The owner's direction (2026-11-15):
- classbeat becomes the complete design tool for music and workout, with Spotify, SwedeBeat, MOWL and
  classbeat-player as integrations of **one** model.
- The gateway stays a **thin proxy**: all analysis, composition, rendering, validation and idempotency live
  in classbeat.
- classbeat analyses, cuts, crossfades and renders the mix itself, on the same host as the gateway.
- Missing songs are swapped automatically, and each swap can be undone.
- The preview is a timeline first, and audio later.
- The user chooses the outputs, with the SwedeBeat playlist on by default.

## Decision

### 1. Three related objects, never one

1. **Revisions** (the existing ADR 0007 kinds; no new mechanism):
   - **`song_list`** is approved as today (ADR 0008 §5). A studio list also holds, per song:
     - the gateway `reference`;
     - the SwedeBeat `duration_sec` and `bpm` from the match;
     - the swap record (§3).

     The match result lives in the `song_list`; the `swedebeat_match` revision stays for the fallback flow
     only. The song approval stays a gate, because downloading and analysing tracks costs time.
   - **`draft`** stays the only kind that is current, revised or saved.
     - Its `content` is the workout.
     - Its `music` (`StudioRevisionMusic`) holds `source: "swedebeat_studio"`, the `song_list_id`, and the
       **mix spec** (§4) with its `map_version`.
     - **Its `content_hash` covers the mix spec** (amending ADR 0007 §2, which hashes `content` only). For a
       studio draft it is the SHA-256 of the canonical JSON `{"content": …, "mix_spec": …}`. Every other draft
       keeps ADR 0007's hash of `content`. So an edited cut makes a new draft, and the revise no-op rule and the
       consent binding see the cut.

     The outputs (§6) bind **one** draft's id and `content_hash`. A song change makes a new `song_list` and
     then a new draft, as "Change songs" does.
2. **The music timeline** is derived from the draft by one pure function (`compose_map` and `mix_offsets`,
   §4). It is authoritative for timing, the timeline view, `fit_to_music` and the render.
3. **Outputs** are produced only from the **final approved draft**, each with its own state (§6).

### 2. Music sources

| `music_source.source` | Needs | Meaning |
|---|---|---|
| `none` | – | soundless (unchanged) |
| `spotify` | Spotify, MOWL | today's Spotify flow (unchanged) |
| `mowl_swedebeat_mix` | MOWL | "Use my SwedeBeat mix": an existing mix (ADR 0016 (a), unchanged) |
| `swedebeat_studio` | Spotify, SwedeBeat | **new**: classbeat designs and renders the playlist and the mix |

- A class's music kind never changes (as `music_kind.py` enforces today).
- **A studio class needs no MOWL connection** from the description to the published SwedeBeat outputs. MOWL is
  one destination (§6).
- The fallback: with the setting `CLASSBEAT_SWEDEBEAT_STUDIO` empty (the default), or with the gateway's
  writes off, SwedeBeat classes use today's match and the manual hand-off.

### 3. Songs: match, evidence, swaps (studio)

1. **Candidates and the match.** Candidates come from a Spotify search or the user's playlist, as today. The
   user's own Spotify playlist is never modified. The facts about each candidate (duration, BPM) come from one
   gateway `/catalog/match`, **not** from MOWL's pool analysis. A song counts as found only when the gateway
   says found **and** the score is at least 0.85 (`SAME_SONG`, ADR 0035 §6; the gateway's 0.78 lets other
   recordings through). An unsure result counts as not found.
2. **Failures are treated as evidence.**
   - The songs that came back missing are matched once more after a pause. A song found on the second try was
     a transient failure, and it is kept.
   - When every song is missing after that (at least 3 sent), that is **suspected unavailability, not proof**.
     There are no automatic swaps. The user sees "SwedeBeat found none of these; this may be an outage" with
     **Try again later** or **Swap anyway**, which is the user's explicit choice.
   - A high miss rate on its own never blocks.
3. **Swaps.**
   - The song agent replaces the songs that are really missing, at most 3 rounds. Each new candidate is
     matched again.
   - Each swap is stored in the `song_list` as `{original:{title,artist}, replacement, reason, score}`.
   - The user sees every swap. The user can **undo** it: the original stays as a marked gap, which can never go
     into a mix, and the user can ask for another song.
   - Nothing is final until the user approves the list.
   - The length check runs on SwedeBeat durations. The upper bound is relaxed, because cuts can only shorten.
4. **The reference** matches `^[A-Za-z0-9_-]{1,64}$`, is owned, and is deleted with the class. It never
   reaches a model. The model sees indexes, as for titles and lengths today.

### 4. Analysis, design and the mix spec

1. **Per-track analysis.**
   - Each approved track is downloaded through the gateway with a cap of **60 MB** set by classbeat. It is
     decoded and analysed in the existing sandboxed child (`design/mixchild.py`, seccomp required, Landlock
     when present) with `mowl.music.analyze_track` (one song; events at the edges kept; RMS section energy;
     `beat1_margin`).
   - The map is cached **per owner** (ADR 0002) in a new table keyed by `(owner_id, reference,
     analyser_version)`, with the sha256 of the analysed bytes. It follows the retention and GDPR rules of
     `swedebeat_mix_maps`.
   - A cache hit needs no download. **The audio is deleted at once.**
2. **The mix spec** is `{tracks: [{reference, start_sec, end_sec}], crossfades: [int, …], map_version}`. The
   pure `mix_plan_policy` validates it against the stored analysis, never against model text:
   - The order is a permutation of the approved references, with no gaps (§3.3). *(Amended 2026-11-21,
     #1331, at the end: each approved reference at least once, and a song may repeat.)*
   - `0 <= start_sec < end_sec <= length`. Cuts snap to the nearest downbeat of the analysis. At least 60 s of
     each track plays. *(Amended 2026-11-21, #1331, A4.4 at the end: a loop piece may be shorter; its loop
     run plays at least 60 s.)*
   - Each crossfade is a whole number of seconds from 0 to 30, shorter than the played window of **both**
     neighbouring tracks. For every middle track, the incoming plus the outgoing crossfade is shorter than its
     window, so part of each track plays alone.
   - At most 40 tracks (the gateway allows 60), at most 150 minutes in total, and no tempo change (the
     gateway has no tempo field).
3. **One duration rule:** the sum of the played windows minus the sum of the crossfades. `compose_map` cuts each
   track's map to its window (beat 1 moves to the first downbeat at or after `start_sec`) and joins the windows
   with each crossfade. It produces the map `fit_to_music` uses. `mix_offsets` produces the sample positions
   the render uses. Both come from **one** function in `mowl-workouts`, so they cannot drift apart.
4. **Design together.** The model proposes the workout **and** the order, the cuts and the crossfades; the
   backend validates and composes. The prompts say the backend computes the total.
5. **The edit loop.** Every edit is a new revision through the existing kinds, and only what it touched is
   recomputed:
   - `revise_draft` changes the workout, the cuts or the order. The map is recomposed, with no download and no
     match.
   - `revise_songs` changes some songs. Only those slots are matched and swapped, and only new references are
     analysed.

   The loop ends when the user approves a draft for outputs.

### 5. The timeline

- The timeline shows the songs, the cut points, the crossfades and the intervals, all from the composed map.
- A playable audio preview is **deferred**. Nothing suggests the user can audition the mix.
- The approval is shown together with the output choice (§6).
- **Amendment of 2026-11-18 (#1173): the `studio` flag of the class summary.** The class list and the class
  detail carry `studio: bool` (`ClassOut.studio`), true exactly when the class's `music_source ->> 'source'`
  is `swedebeat_studio` and false for every other class (Spotify, MOWL, soundless, no source yet, every older
  class). It is read-only, computed by the same owner-scoped statement as the other flags (`owner_id` and
  `UserContext`, no new query), and says nothing the class's owner could not already infer from the class. The
  page's `useStudioTimeline` reads `GET /api/classes/{id}/timeline` only for a class whose summary says
  `studio`; for any other class it answers `no_draft` without a request (`none` only when the class has no
  version yet), which is what the designed `409 no_studio_draft` meant, so a composer class with approved songs
  and no studio design yet still reaches "Start the design". That 409 of a non-studio class no longer reaches
  the browser (and the e2e `watchConsole` no longer exempts that URL).

### 6. Outputs

| Output | Default | Requires | Does not require |
|---|---|---|---|
| Final Spotify playlist: fresh, private, in the approved order | on | the approved draft | – |
| SwedeBeat playlist, created and published | **on** | the approved draft | a render, MOWL |
| SwedeBeat mix: rendered MP3, merge-published | on when MOWL is chosen, otherwise optional | the approved draft and one render | the playlist |
| MOWL workout | optional | the mix, then the attach | – |
| classbeat-player | later (§9) | the approved draft | MOWL |

- The user may turn off the SwedeBeat playlist; that is an explicit choice.
- There is **no private playlist output**: it is invisible in the app, and classbeat already holds the spec.
- **The consent screen** names each chosen output and states the facts:
  - SwedeBeat has no delete;
  - a published playlist shows the user's name and email;
  - what a merged mix shows others (gate L2).

  It checks `GET /auth/session` before the button. The approval binds the draft's id, its `content_hash`, the
  chosen outputs and the **SwedeBeat connection** (its id and generation; the publish operation also records the connection's account digest in its context when it is sent, ADR 0035 §3 as amended by #1117, which the approval does not bind). The
  server re-checks all of them when the job runs (ADR 0007 §5). A different or reconnected connection refuses
  the job with `connection_changed`, and nothing is written.

**One security-critical job, `produce_outputs`.** It keeps state per output in an owned
`class_outputs(owner_id, class_id, revision_id, output, attempt, state, external_id, detail, updated_at)` table,
with RLS and `UNIQUE (owner_id, class_id, revision_id, output, attempt)`. `attempt` starts at 1.

The `output` values are `spotify_playlist`, `swedebeat_playlist`, `swedebeat_mix` and `mowl`. When an attempt row is created, it stores everything that would otherwise vary between runs, **before the first write intent**: the mix name with its random `cb-` suffix, the playlist name, and the render plan.

**Keys and the link to ADR 0005 §7.** Each external call is an ADR 0005 external operation, and a write-intent
row is committed before the call.
- The idempotency key is **bound to the approval, not to the job**:
  `out:<class_id>:<revision_id>:<output>:<attempt>:<step>`. The steps are `create`, `publish`, `merge`,
  `attach`, `spotify_create` and `spotify_fill`. A later job for the same approval finds the earlier row, so
  it cannot make a second record.
- The request hash is the canonical JSON of `{kind, draft content_hash, output, attempt, the stored names, the
  exact JSON body fields}`. For `merge`, it covers the stored **render plan** and the tracks' analysed
  sha256s, not the MP3 bytes. The render must still be deterministic: C1 tests it. The first render's
  sha256 is stored with the attempt, and a later render with a different one stops the output with
  `render_not_reproducible` (its own message), nothing sent. "Music changed" (`music_changed`) is only a
  fresh download whose sha256 is not the analysed bytes' (step 1 of the mix, below).
- The same key with a different hash is refused, as ADR 0005 §7 refuses it (`internal`).
- `class_outputs.state` is derived from the operations:

  | ADR 0005 operation | Output state |
  |---|---|
  | `pending` | `running` |
  | `succeeded` (all steps) | `done` |
  | `failed`, a definite refusal or never sent | `failed`, safe to retry |
  | `uncertain` | `in_doubt` |

  The wording of this ADR maps onto ADR 0005's: **Check again** is ADR 0005's reconciler, and **Create another
  anyway** is ADR 0005's **Do it anyway**. "Create another anyway" opens `attempt + 1` with new keys after a
  warning that states the duplicate risk. The `in_doubt` row stays on record.

**Rules for every output.**
- **Every returned id is stored before the next step:** the `playlist_id`, the mix URL and MOWL's
  `PlaylistID`. A resumed output **always continues from its stored id and never creates a second record.**
- **`in_doubt`** (a timeout, a lost lease, or a 5xx after the request was sent) **must be reconciled before any
  new write** to that output, and is never retried automatically.

**Settling an output in doubt.**
- **Playlist create in doubt.** It cannot be listed through the gateway: `/playlists/shared` lists only
  published playlists, and an unpublished one cannot be found. The step stays `in_doubt`, and the user is told
  it may exist. The only way on is **Create another anyway** (`attempt + 1`). The recommended gateway pass-through
  (listing one's own playlists) would let Check again settle it.
- **Playlist publish in doubt.** The `playlist_id` is stored, so Check again reads **every page** of `GET /playlists/shared` (pages 1 to 50, until a page comes back empty), **with the approved SwedeBeat connection and generation** only (stored with the publish). When that connection is gone or reconnected, the listing proves nothing about it: the step stays `in_doubt` with `connection_changed` and nothing is read, never "not listed" and then a publish under another bearer. Amended (#1117): a reconnect of the **same** connection row to the **same** SwedeBeat account (the publish's stored account digest equals the row's, both present) is read with the new bearer; another account, a row or a publish without the digest, or another row stays `connection_changed`. That reason is recorded apart from the failure's category the user was shown (`check_reason`). If
  the id is listed, the step is `succeeded`. If it is not, publishing **the same `playlist_id`** again is safe,
  because publish is one-way on an existing record and creates none. It is never a new create.
- **Mix (merge-publish) in doubt.**
  - With a MOWL connection, Check again looks in the user's MOWL listing for the unique name. A hit is **shown**
    to the user, never attached automatically. When the user confirms it, the step becomes `succeeded`, with
    that entry's URL.
  - Without MOWL (or before gate L1 holds), the mix cannot be checked. It stays `in_doubt` for good, and the way
    on is Create another anyway, with a new unique name and the duplicate risk stated.

- **Spotify final playlist:** the ADR 0008 create and fill, from the approved draft's order. It is stored with
  the revision id.
- **SwedeBeat playlist:** `POST /playlists`, then `/publish`. The body is built from the spec, never from model
  text:
  - `name` is the class title: printable, 1–120 characters, otherwise "classbeat class";
  - `category_id` is 27;
  - `crossfade_sec` is 0 at the playlist level;
  - every track gets an **explicit** `crossfade_sec` (its outgoing crossfade; the last track gets 0, because
    the gateway would otherwise default to 5);
  - `start_sec` and `end_sec` are rounded to 0.01 s, the app's precision. The spec stores the cuts already
    rounded, so `compose_map`, `mix_offsets` and the gateway body all use the same numbers;
  - `trim` is true whenever there is a cut;
  - title, artist and bpm come from the `song_list`'s SwedeBeat facts.
- **Mix:**
  1. Render once **per merge attempt**, from freshly downloaded tracks, and never before the approval. After a
     definite failure (nothing sent), a retry downloads and renders again; the result has the same sha256
     because the render is deterministic. A sha256 different from the analysed bytes stops the
     output with "the music changed, review again"; nothing is published.
  2. The render is sample-accurate from `mix_offsets`: cuts, equal-power crossfades, and 320 kb/s CBR MP3
     through the `soundfile` libsndfile encoder (checked: libsndfile 1.2.2 writes `MPEG_LAYER_III`; ffmpeg is
     not added).
  3. It runs in the sandboxed child and streams transition by transition, under 1 GiB.
  4. Its decoded length must equal the composed duration within 100 ms of encoder padding. A beat-alignment
     spot check runs at each transition.
  5. The file must be at most 400 MB, the gateway's cap; 150 minutes at 320 kb/s is about 360 MB. Otherwise
     nothing is sent.
  6. Then `merge-publish` with the exact bytes and its own streamed deadline of 900 s (an amendment to ADR 0035
     §1, below). The name is unique: `<class title, at most 100 chars> · cb-<6 random [a-z0-9]>`. `X-Tracks`
     lists the tracks in mix order with title, artist and bpm from the `song_list`'s SwedeBeat facts and the
     played duration in seconds. It is UTF-8 JSON of at most 16 KB, with titles cut to fit. The returned URL is
     stored.
- **MOWL attach:**
  - Poll the user's MOWL mix listing with backoff for about 30 minutes.
  - Attach **only** the entry whose `URL` has the stored S3 object key and whose duration is within tolerance.
    **Never by name.**
  - Attach once and store the `PlaylistID`. The attach is the external operation `mowl_attach_studio_mix`,
    written through `operations.perform` directly rather than the SwedeBeat write path (`Producer.write`,
    whose `WriteFailure` mapping is the gateway's): its guard holds the user's MOWL connection at the
    opened generation **and** the approval's (the SwedeBeat connection and the draft).
  - If nothing matches, the state is `published_not_listed`, with **Check again**. The mix is never published
    again.
- **Per-output states** are shown with the action that is safe for each. For example: "Playlist created, not
  yet public: Publish" (same id); "Mix: not sent, safe to retry".
- A 403 from the gateway (writes off) is a normal state the user sees.
- There are limits per class and per user per day, in the `design/mixlimit.py` pattern.

### 7. Visibility (amends AGENTS.md's "everything we create is private")

- Spotify playlists, MOWL workouts and the working copy stay private.
- A SwedeBeat playlist or mix is published **only when the user chose that output for that class** on the
  consent screen. Nothing is published by default without that screen, and nothing is published by a model.

### 8. The gateway paths classbeat may use

These are exactly the paths classbeat may call. `test_swedebeat_gateway_guard.py` pins them, together with the
client module's methods, and each PR opens only its own paths:

| Path | Opened by |
|---|---|
| `/auth/login`, `/auth/session`, `/catalog/match` | already open (ADR 0035) |
| `GET /tracks/{reference}/download` | C2 |
| `POST /playlists`, `POST /playlists/{id}/publish`, `POST /playlists/merge-publish`, `GET /playlists/shared` (reconciliation only) | C7 |

**Deadlines** amend ADR 0035 §1, where every call is clamped to 1–150 s. Two calls get their own deadline:
- the track download: a 120 s deadline for the whole download, with its own 60 MB cap;
- `merge-publish`: 900 s, with a streamed body.

The 150 s clamp stays for every other call.

These stay forbidden:
- `PATCH /playlists/{id}`: classbeat makes a new published playlist only from a new approval, and never edits
  a published one in place;
- `/catalog/search`;
- the match's `playlist` mode.

### 9. The Spotify lifecycle and classbeat-player

**Spotify.** The user's own playlist is never modified. The working copy follows each revision. The final
playlist is fresh and private. Sync state belongs to a revision:
- **Writes to one Spotify playlist are serialized** by a lock keyed by the playlist. An old request still in
  flight always finishes before a newer one starts.
- The intended revision is re-checked **just before each external write**. A stale write hands over to the
  newer revision's sync without writing.
- Every write replaces the whole list and is **read back and compared** before the state becomes `synced`.
- The states are `synced`, `pending` and `failed`, shown with **Sync again**. A failure never blocks the
  SwedeBeat outputs.

**classbeat-player.** The model is destination-independent, and player format 1.2 (a later ADR 0034
amendment) carries it:
- the workout;
- the source: `none`, `spotify` with its tracks, or `swedebeat_studio` with the mix spec and the outputs'
  stored `playlist_id` and mix URL.

The player can follow Spotify or play the SwedeBeat mix or spec. A studio class can be sent to the player
without MOWL.

### 10. Live activation: gates per output (the owner's)

`CLASSBEAT_SWEDEBEAT_STUDIO` is a comma-separated list, empty by default. Its accepted values are `design`
(the studio itself), `swedebeat_playlist`, `swedebeat_mix` and `mowl`. Every other value refuses to start the
app.
- An output value needs `design`.
- `mowl` needs `swedebeat_mix`.

Each value's gates must all hold first. They are listed in `docs/status.md` "Waiting for the owner".
- **The final Spotify playlist** of a studio class has no value of its own. It follows Spotify's existing live
  activation (the M4 steps), as every Spotify playlist does.
- **classbeat-player** follows ADR 0034's own activation, once player format 1.2 exists.

| Enables | Gates |
|---|---|
| The studio (design only, no SwedeBeat write) | **L4a**: the rights decision to download and analyse catalogue tracks |
| SwedeBeat playlist | L4a; **L5**: the gateway's `ALLOW_SWEDEBEAT_WRITES` turned on, for every client; **L6**: a playlist created and published through the gateway appears in the app with the designed cuts and crossfades. No MOWL check, no render. |
| SwedeBeat mix | L4a; L5; **L4b**: the rights decision to render and publish a mix; **L2**: what a merged mix shows others; **L3**: the same 3-track spec rendered in the app and by classbeat, with the timing compared |
| MOWL export of a studio class | the mix gates plus **L1**: the merge-published mix appears in MOWL with a matching `URL`, and plays. If it cannot be identified, the one gateway issue is a pass-through of the id `mergemp3api` returns. |

Agents never call a real gateway, never enable writes, and never run a gate. Tests use the fake gateway and
the fake MOWL.

### 11. The work (milestone "SR")

Each PR is tested on fakes. Security-critical PRs get an Opus Developer and an Opus Reviewer.

| Item | Repo | Scope | Dev / Review |
|---|---|---|---|
| SR0 | classbeat | This ADR; roadmap Amendment 24; the AGENTS.md and agent-rule updates; notes in ADR 0008, 0016, 0021 and 0035 | Architect / Opus |
| MW1 | magpern/mowl-workouts | `music.analyze_track` | Sonnet / Sonnet |
| MW2 | magpern/mowl-workouts | `window_map`, `compose_map`, `mix_offsets`, the duration rule; `MAP_VERSION` bump | Sonnet / Sonnet |
| C1 | classbeat | Sandboxed render child taking a render plan as data (cuts, crossfades, MP3 encode, length check) | Sonnet / Opus |
| C2 | classbeat | Gateway `download_track` with the 60 MB cap; keep `reference`; guard (§8) | Opus / Opus |
| C3 | classbeat | Studio song stage without MOWL: facts from the match, evidence retry, swaps with records and undo | Opus / Opus |
| C4 | classbeat | Wheel bump (MW1); per-track analysis job and per-owner cache (migration, RLS, retention, GDPR) | Opus / Opus |
| C5 | classbeat | Wheel bump (MW2); `StudioRevisionMusic`, `mix_plan_policy`, design and revise on the composed map, prompts | Opus / Opus |
| C6a | classbeat | Timeline API, the output choice and the approval binding | Opus / Opus (approval binding) |
| C6b | classbeat | Timeline view, output choice and consent; sv and da | Sonnet / Sonnet |
| C7 | classbeat | `produce_outputs`: write intents, `class_outputs`, resume and reconcile, render once, merge-publish, playlist then publish, MOWL attach by URL | Opus / Opus |
| C8 | classbeat | Spotify sync state per revision, serialized, read back; e2e including a studio class with no MOWL | Opus / Opus (Spotify external writes) |

## Alternatives

- **The gateway renders (its PL2).** Rejected: the owner keeps the gateway a thin proxy, and classbeat needs
  the audio for analysis anyway.
- **A private draft playlist finished in the app** (PR #1089). Rejected: it is invisible in the app.
- **A trim map over Spotify playback.** Rejected, as in ADR 0021: no player honours it, and there is no
  Spotify audio.
- **A shared, cross-owner analysis cache.** Rejected: it would be an ADR 0002 exception for little gain.
- **Rendering before approval to preview the exact mix.** Rejected: the composed map is authoritative,
  rendering twice costs downloads, and an audio preview is deferred.
- **ffmpeg for the render.** Not needed: libsndfile encodes MP3. Adding ffmpeg would add a large parser to the
  image.

## Consequences

- classbeat downloads, decodes, renders and uploads SwedeBeat audio. It never keeps it: work directories are
  private and swept, as in ADR 0016.
- Published outputs are permanent records in the user's SwedeBeat account, made only on the user's per-class
  choice.
- The rights to analyse and to render are **open** (L4a and L4b). The code ships, but stays off.
- Two gateway pass-through issues are raised, and the gateway gains no logic:
  - **recommended:** list one's own playlists, including unpublished ones, so a playlist in doubt can be
    settled;
  - **optional:** a search-error status in `/catalog/match`.
- New threat-model rows (to be added with C2 and C7):
  - T127: audio downloaded through the gateway: a size cap, the sandbox, no storage;
  - T128: published outputs: the consent binding, at most once per output, no auto-attach by name;
  - T129: the model proposing cuts: validated against the analysis, never from model text.

## Amendment (2026-11-21, owner): a song may appear more than once in a mix (#1331)

Status: **Accepted (owner decisions 2026-11-21; Opus review PASS on #1332).** Review findings applied in
#1341. Amends §4.2 ("a permutation of the approved references") and adds
to §4.4, §5, §6 and §10. Authorized by the owner on 2026-11-21, with these product decisions the same day:
the same song may appear many times in a class; the same window may be reused anywhere, not only next to
itself (for example one 1-minute recovery piece used at every recovery); back-to-back pieces of one song are
a loop (for example a chorus two or three times in a row); and a whole track is not repeated unless the user
asked for it. Roadmap Amendment 33 records it. Everything not named here is unchanged. Nothing is built by
this amendment; the implementation issue is #1342.

**Terms.** A **piece** is one entry of the mix spec's `tracks`. A **repeat** is a piece whose `reference`
appeared in an earlier piece. A **loop join** is a change of piece where both pieces have the **same**
reference (adjacent pieces of one song), and a **loop run** is a maximal run of adjacent pieces of one song
(one piece or more). A **loop piece** is a piece on either side of a loop join. A **non-adjacent repeat** is
a repeat that is not a loop piece.

### A1. The spec shape does not change

`{tracks: [{reference, start_sec, end_sec}], crossfades, map_version}` already holds a list of pieces, and the
render plan is a list of audio pieces. No field is added: a repeat is the same `reference` in another piece. The
model still sees indexes, never references (§3.4); a repeat names the same index again. Every hint of this
amendment (A2, A5) names a track or piece **index**, never a reference.

### A2. Coverage and bounds

- **Each approved reference plays at least once, and nothing else plays.** A gap still never goes into a mix.
  The reason code stays `not_a_permutation` (a stable code the model hints and tests know); its text
  becomes "each approved track at least once, and only those".
- **No per-song repeat cap and no share-of-class cap** (owner). The only bounds are the existing safety bounds:
  at most `MAX_TRACKS` (40) **pieces** in all (`too_many_tracks`) and 150 minutes (`mix_too_long`). There is no
  `too_many_repeats` reason.
- **Soft hint `one_song_dominates`:** one reference's windows, less their outgoing crossfades, fill more than
  half of the composed duration, and that reference has two or more pieces **and** the mix has two or more songs
  (a song played once is never flagged, however long, and neither is a class of one song). It reaches the model
  as a named hint and is shown on the timeline; it never refuses a draft.

### A3. Non-adjacent repeats are ordinary pieces

A non-adjacent repeat follows exactly the rules of any piece of its track: the cut rules of #580 and #1328 for
its timing class, at least `MIN_PLAYED_SEC` (60 s) played, the crossfade rules and the duration rule. It needs
no phrase point. It is allowed for every timing class that allows an ordinary cut of that track. Its window may
be identical to an earlier piece's or different.

### A4. A loop join is musical

1. **`exact` only.** Both sides of a loop join are the same track, and that track's stored timing decision
   (ADR 0040 §3, `timing_class_policy`) must be `exact`. `approximate`, `unusable`, or no stored timing decision
   (spotdown off or not yet run for the track) refuses it: `loop_needs_exact_timing`. Such a song may still
   repeat non-adjacently (A3).
2. **On phrase points, else downbeats.** At each loop join the outgoing piece's `end_sec` and the incoming
   piece's `start_sec` each land within **30 ms** (the lock tolerance of #1330) of a **trusted** candidate of
   #1328: a phrase start or phrase end when the track has a trusted phrase grid, otherwise a trusted downbeat.
   The point compared is the **snapped cut** (after A7 step 3, on the `TrackFacts` bar grid) against the
   **trusted candidate's own time** (spotdown's measured point, not re-snapped). Late in a long track the two
   grids can drift apart by more than 30 ms; the check then refuses (it fails closed), and the implementation
   tests a long track to show where. A piece **between two loop joins** therefore plays a whole number of bars;
   the first and last pieces of a run have one ordinary outer edge and need not. Otherwise
   `loop_not_on_phrase`. The run's **outer** edges (into the first piece and out of the last) are
   ordinary transitions.
3. **No crossfade at a loop join.** Its crossfade is **0**: the two pieces share one tempo and one grid, so a
   cut on a phrase point is seamless, while a whole-second overlap would put the grids out of step. Otherwise
   `loop_crossfade_not_zero`. *(Reversible: if #1330 brings an accepted proposal for crossfades of whole bars, a
   loop join may take one.)*
4. **The minimum length of a loop piece** is `MIN_LOOP_BARS` = **8 bars** (one phrase of four-four), on the
   grid the snap uses: `cs(end) - cs(start) >= cs(MIN_LOOP_BARS * 240 / bpm)` with the track's
   `TrackFacts.bpm`; otherwise `loop_piece_too_short`. `MIN_PLAYED_SEC` does not apply to a single loop
   piece. It applies to the **loop run**: the run's windows together (its joins have no crossfade) play at least
   60 s, so a song still never plays for less than a minute at a stretch (`window_too_short`). A run of one
   piece is an ordinary piece and keeps the 60 s rule.

### A5. Whole tracks are not repeated by default (soft)

A repeat is normally part of a song: a recovery minute, a chorus, a strong section. The design and revise
prompts say: do not use the same whole track twice unless the user asked for it in the brief or in a revise.
The backend cannot verify the user's intent from model text, so this is a **soft check**, never a
`MixPlanRefused`: **`whole_track_repeated`** is reported when one reference has **two or more pieces that each
cover at least 90 %** of the track's analysed length (`cs(end) - cs(start) >= 0.9 * cs(length)`). It reaches
the model as a named hint (as #1330's lock hint) and is shown on the timeline; the draft stands if the model
keeps it.

### A6. Duration, crossfades and the 10 % tolerance with repeats

- **The one duration rule is unchanged:** the sum of the pieces' windows less the sum of the crossfades, with
  every piece counted, a repeat as often as it plays, and a loop join adding 0. `compose_map` and `mix_offsets`
  take the pieces in order, the same analysis once per piece. **Checked (#1342, [comment
  7062866236](https://github.com/magpern/classbeat/issues/1342#issuecomment-7062866236)):** `mowl.compose`
  (wheel 0.12.0) does not key pieces by track, so no mowl-workouts change is needed.
- **The crossfade rules are unchanged per piece:** one crossfade per change of piece, whole seconds 0 to 30,
  shorter than **both** neighbouring pieces' windows, and for every middle piece the incoming plus the outgoing
  crossfade shorter than its window. A4.3 adds only that a loop join's crossfade is 0.
- **The 10 % class-length tolerance** applies to the composed duration as today, with repeats counted.
- **The song stage is unchanged:** its list-length check (§3.3, on SwedeBeat durations, upper bound relaxed)
  still applies to the approved list, each song once. Repeats are a design choice and never let a list below
  the lower bound through.

### A7. The validation order (extends #580's fixed order)

`mix_plan_policy.checked` stays the only validator (no second spec shape). First the spec's shape checks
(`unknown_field`, `list_has_gap`, at least one piece, `too_many_tracks` on pieces). Then **each piece runs all
its own checks before the next piece's**: its fields (`unknown_field`, `tempo_change`), its **reference on the
approved list** (else `not_a_permutation`, since step 1 needs that track's facts), then steps 1 to 4 for that
piece. Steps 5 to 10 then run on the whole mix, in this order: coverage, then the loop joins, then
`window_too_short` and the rest. The fixed order is:

1. the range check of the piece's proposed cut (`cut_out_of_range`);
2. candidate selection (#1328): the nearest **usable** candidate within `min(1.0 s, 0.45 × bar)` of the
   proposed cut, else the proposed cut. Usable means trusted **and** on an `exact` track (an `approximate`
   candidate is advisory and never sets a cut; an `unusable` track has none). A downbeat is chosen only when
   no other kind is in reach, and a tie in distance goes to the earlier candidate. A cut at an edge (0, or
   within 0.01 s of the length) is never moved, and an edge candidate is never selected;
3. the bar-grid snap on the `TrackFacts` grid, for an `exact` track **or a track with no stored timing
   decision** (a local analysis of the played bytes, the played recording itself), as before #1328. An
   `approximate` or `unusable` track is never snapped: its cut is only rounded to 0.01 s. Edges stay edges.
   *(Architect, 2026-11-21, #1350; reversible. A loop join still needs a stored `exact` decision, A4.1.)*
4. the range check again after snapping;
5. **coverage** (A2): each approved reference at least once (`not_a_permutation`; "nothing else" is already
   held by the per-piece check above);
6. **loop joins** (A4), for each join in mix order: `loop_needs_exact_timing`, then `loop_not_on_phrase`;
7. **lengths:** each loop piece at least `MIN_LOOP_BARS` (`loop_piece_too_short`); every other piece and every
   loop run at least `MIN_PLAYED_SEC` (`window_too_short`);
8. **the crossfades:** `crossfade_count`, `crossfade_not_whole`, `crossfade_out_of_range`,
   `loop_crossfade_not_zero`, `crossfade_too_long`, `crossfades_overlap`;
9. the total duration (`mix_too_long`);
10. then the **soft checks**, which never refuse: `whole_track_repeated`, `one_song_dominates` (and #1330's
    lock and tempo hints).

Every constraint is checked after snapping. A piece's `why` (the model's note on a cut, #1328) is model text:
it never enters the stored spec or the `content_hash`. The new refusal reasons are `loop_needs_exact_timing`,
`loop_not_on_phrase`, `loop_piece_too_short` and `loop_crossfade_not_zero`; the new hints are
`whole_track_repeated` and `one_song_dominates`. The loop checks live in a small pure module beside
`mix_plan_policy` (a rule module stays near 150 lines, CLAUDE.md), called from `checked` at step 6.

### A8. Outputs with repeats

| Output | A draft with any repeat |
|---|---|
| SwedeBeat mix (rendered) | **allowed.** The render plan has one entry per piece. Each distinct reference is downloaded once per merge attempt and its sha256 checked once (§6, Mix step 1); the render, its length check and its determinism are unchanged. |
| SwedeBeat playlist | **not available** until the owner's live check (A9) shows the gateway and the app accept the same track twice with distinct cuts. A class with repeats is **mix-only**. |
| MOWL workout | follows the mix, unchanged (attach by URL). |
| Final Spotify playlist | each song **once**, in the order of its first piece. Spotify plays whole tracks, so it is a song list, not the mix's timing. |
| classbeat-player | carries the spec with its pieces (format 1.2), when built. |

- **No evidence exists** that the gateway's `POST /playlists` or the SwedeBeat app accept one reference twice in
  a playlist. The gateway contract, ADR 0035, §6 and §8 here and `docs/providers/swedebeat-mixing-findings.md`
  are silent on it (the app's per-track editor was observed, never a duplicate). Until A9 settles it,
  `output_choice_policy` refuses `swedebeat_playlist` for a draft whose spec has any repeated reference, and
  `produce_outputs` checks it again when the job runs (defence in depth): nothing is written.
  - **The reason is `playlist_has_repeats`.** `refusal` takes a "has repeats" fact derived from the draft's
    spec, and its fixed order becomes `invalid_input`, `mowl_needs_mix`, `playlist_has_repeats`, `output_off`.
  - At job time `outputs_admit.bound` re-runs `refusal`; `outputs_kind` maps `playlist_has_repeats` to a job
    failure of the same name (never the `invalid_input` fallback).
  - The copy keys are `studio.reason.playlist_has_repeats` (the refusal) and `studio.out.playlistHasRepeats`
    (the note on the disabled option), in en, sv and da.
- **The output choice and the consent screen say so:** the playlist option is off and disabled with "This class
  repeats a song, so it can be produced as a mix only" (en, sv, da). The design prompt tells the model that
  repeats make a class mix-only; a user who wants the playlist asks for a version without repeats through
  `revise_draft`. With the mix gates (§10) not yet held, a class with repeats has no live SwedeBeat output
  until they are.
- **X-Tracks** lists one entry per **loop run** (one song playing on), and a non-adjacent repeat as its own
  entry, each with the played duration until the next entry starts, so the durations still add up to the mix.
- **The timeline** shows one row per piece and marks loop runs and repeats.

### A9. Live checks and rights (the owner's)

- **Duplicate tracks in a SwedeBeat playlist (gate L6, extended).** One playlist with the same reference twice,
  with different cuts, created and published through the gateway, appears in the app in order with each cut and
  crossfade. If it does, a later amendment lifts the mix-only rule; if not, it stays. Agents run no live check
  and never call a real gateway.
- **Rights (L4b).** Whether the owner's L4b decision to render and publish a mix covers **repeated or looped
  sections** of a catalogue track is the owner's decision. The L4b answer counts for a mix with repeats **only
  when it explicitly covers repeated or looped sections**; a general L4b answer does not. The owner turns
  `swedebeat_mix` on (§10), so this is the owner's control, and no mix is published before L4b holds.

### A10. Approval

The mix spec, repeats included, is in the studio draft's `content_hash` (§1), so a change to the repeats is a
new draft and a new hash, like any cut, and the consent binding sees it. The model proposes the pieces; the
backend validates them against the stored analyses and timing decisions, never against model text (T129). The
model may **reorder** the approved list (#1330's clarification of 2026-11-21) and **repeat** within it (this
amendment, #1331, owner 2026-11-21); a song swap still goes through `revise_songs`.

### A11. The work

One implementation issue, #1342, Opus Developer and Opus Reviewer (the mix spec feeds the external writes). Its
first task, the `mowl.compose` check of A6, is done (no change needed). Then: the loop
module and `checked` order, the soft hints, `output_choice_policy` and the job's re-check, X-Tracks, the render
download of each distinct reference once, the timeline marks, the prompts and their `prompt_version`s, the
consent text in en, sv and da, and tests: table-driven cases for every new reason and hint, the existing
`test_mix_plan_policy.py` unchanged except the reworded `not_a_permutation` text, the duration rule, the
crossfade rules, the 10 % tolerance and a long-track loop (A4.2) with repeats, and mutation checks on the
`exact` gate, the coverage rule, the phrase tolerance, the loop-join crossfade and the playlist refusal. The loop joins depend on #1328's trusted
candidates; non-adjacent repeats do not.
