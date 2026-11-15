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
- [ADR 0035](0035-swedebeat-gateway.md) §7 ("nothing is written to SwedeBeat"), within the four gateway paths
  of §8 here.
- The draft-playlist route of the closed PR #1089, which does not work: API-created playlists are invisible
  in the app unless public.

Amends:
- [ADR 0008](0008-song-picking.md): for a studio class, the final Spotify playlist is an output of the
  approved draft (§6).
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
   - **`song_list`** is approved as today (ADR 0008 §4). A studio list also holds, per song:
     - the gateway `reference`;
     - the SwedeBeat `duration_sec` and `bpm` from the match;
     - the swap record (§3).

     The match result lives in the `song_list`; the `swedebeat_match` revision stays for the fallback flow
     only. The song approval stays a gate, because downloading and analysing tracks costs time.
   - **`draft`** stays the only kind that is current, revised or saved.
     - Its `content` is the workout.
     - Its `music` (`StudioRevisionMusic`) holds `source: "swedebeat_studio"`, the `song_list_id`, and the
       **mix spec** (§4) with its `map_version`.
     - Its `content_hash` covers the mix spec, so an edited cut makes a new draft.

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
   - The order is a permutation of the approved references, with no gaps (§3.3).
   - `0 <= start_sec < end_sec <= length`. Cuts snap to the nearest downbeat of the analysis. At least 60 s of
     each track plays.
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

  It checks `GET /auth/session` before the button. The approval binds the draft's id, its `content_hash` and
  the chosen outputs. The server re-checks them when the job runs (ADR 0007 §5).

**One security-critical job, `produce_outputs`.** It keeps state per output in an owned
`class_outputs(owner_id, class_id, revision_id, output, state, external_id, detail, updated_at)` table,
with RLS. Each external call is an ADR 0005 external operation: a write-intent row is committed before the call.

- **Every returned id is stored before the next step:** the `playlist_id`, the mix URL and MOWL's
  `PlaylistID`. A resumed output **always continues from its stored id and never creates a second record.**
  When `/publish` fails after `POST /playlists`, the next run publishes that same `playlist_id`.
- **`in_doubt`** (a timeout, a lost lease, or a 5xx after the request was sent) **must be reconciled before any
  new write** to that output, and is never retried automatically.
  - **A playlist in doubt** cannot be listed through the gateway (`/playlists/shared` lists only published
    playlists). It stays `in_doubt` for good, and the user is told it may exist. If the user explicitly asks to
    **create another anyway**, that is a **separate, new irreversible operation** with a new key and the
    duplicate risk stated. It is not a retry.
  - **A mix in doubt** is looked up in the MOWL listing by its unique name (`<class title> · cb-<6 chars>`). It
    is **shown** to the user, never attached automatically.
- **Spotify final playlist:** the ADR 0008 create and fill, from the approved draft's order. It is stored with
  the revision id.
- **SwedeBeat playlist:** `POST /playlists` with the references, cuts and crossfades of the spec, then
  `/publish`.
- **Mix:**
  1. Render **once**, from freshly downloaded tracks. A sha256 different from the analysed bytes stops the
     output with "the music changed, review again"; nothing is published.
  2. The render is sample-accurate from `mix_offsets`: cuts, equal-power crossfades, and 320 kb/s CBR MP3
     through the `soundfile` libsndfile encoder (checked: libsndfile 1.2.2 writes `MPEG_LAYER_III`; ffmpeg is
     not added).
  3. It runs in the sandboxed child and streams transition by transition, under 1 GiB.
  4. Its decoded length must equal the composed duration within 100 ms of encoder padding. A beat-alignment
     spot check runs at each transition.
  5. Then `merge-publish` with the exact bytes, the unique name and its own streamed deadline of 900 s. The 150
     s egress cap stays for every other call. The returned URL is stored.
- **MOWL attach:**
  - Poll the user's MOWL mix listing with backoff for about 30 minutes.
  - Attach **only** the entry whose `URL` has the stored S3 object key and whose duration is within tolerance.
    **Never by name.**
  - Attach once and store the `PlaylistID`.
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
client module's methods:
- the existing `/auth/login`, `/auth/session` and `/catalog/match`;
- `GET /tracks/{reference}/download`;
- `POST /playlists` and `POST /playlists/{id}/publish`;
- `POST /playlists/merge-publish`.

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

`CLASSBEAT_SWEDEBEAT_STUDIO` lists the outputs that are enabled, and it is empty by default. Each output's
gates must all hold first. They are listed in `docs/status.md` "Waiting for the owner".

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
| C1 | classbeat | Wheel bump; sandboxed render child (cuts, crossfades, MP3 encode, length check) | Sonnet / Opus |
| C2 | classbeat | Gateway `download_track` with the 60 MB cap; keep `reference`; guard (§8) | Opus / Opus |
| C3 | classbeat | Studio song stage without MOWL: facts from the match, evidence retry, swaps with records and undo | Opus / Opus |
| C4 | classbeat | Per-track analysis job and per-owner cache (migration, RLS, retention, GDPR) | Opus / Opus |
| C5 | classbeat | `StudioRevisionMusic`, `mix_plan_policy`, design and revise on the composed map, prompts | Opus / Opus |
| C6a | classbeat | Timeline API, the output choice and the approval binding | Sonnet / Opus |
| C6b | classbeat | Timeline view, output choice and consent; sv and da | Sonnet / Sonnet |
| C7 | classbeat | `produce_outputs`: write intents, `class_outputs`, resume and reconcile, render once, merge-publish, playlist then publish, MOWL attach by URL | Opus / Opus |
| C8 | classbeat | Spotify sync state per revision, serialized, read back; e2e including a studio class with no MOWL | Sonnet / Sonnet |

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
  - T128: audio downloaded through the gateway: a size cap, the sandbox, no storage;
  - T129: published outputs: the consent binding, at most once per output, no auto-attach by name;
  - T130: the model proposing cuts: validated against the analysis, never from model text.
