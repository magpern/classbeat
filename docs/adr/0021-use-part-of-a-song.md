# ADR 0021: Use part of a song (trim an intro or outro, start mid-track) (#580)

Status: Proposed (issue #580). **Amended 2026-11-15: unblocked for SwedeBeat mixes through the owner's gateway (see the last section); Spotify stays blocked.** The original finding was that **the feature is blocked**:
no music source classbeat supports can play part of a song. The owner decides whether to wait for
a source that can, schedule the design below, or close the issue.

Related: [ADR 0007](0007-classes-revisions-and-saving.md) (revisions, saving),
[ADR 0008](0008-song-picking.md) (song picking, the 9 s crossfade, the 10 % check),
[ADR 0010](0010-live-activation-permissions.md) (Spotify-derived data and audio),
[ADR 0016](0016-swedebeat-music.md) (Swedebeat mixes), and the `mowl-workouts` library
(`spotify.py`, `sync.py`, `docs/mowl-api.md`, `docs/mcp-tools.md`).

## Context

Today every song counts at its full length. The song list's total is the songs' lengths less
9 s for each change of song (`agent/songs.py` `total_sec`, `CROSSFADE_SEC`), and it must be within
10 % of the target (`TOLERANCE`; `agent/choose_songs.py` `check_list`). The design stage fits a draft
to a `mowl-workouts` music map (`agent/design.py` `check`, `sync.fit_to_music`) in which each track
starts 9 s before the previous one ends (`spotify.build_map`). Nothing carries a start or an end
inside a song.

The question is whether an instructor could say "start this song at the first verse" or "cut the
last minute", and the class would be timed to that. Timing is only half of it: **the class is ridden
to music that something plays, and that player must really skip the part**. What was checked
(2026-11-05, read-only, no live call):

- **Spotify tracks.** MOWL "cannot give the audio of Spotify tracks" (`mowl-workouts`
  `spotify.py` docstring). The class's map assumes "the player plays the playlist in order, without
  shuffle, and with Spotify's crossfade" and each track plays whole. The user's Spotify client plays the
  playlist classbeat created (ADR 0008). A Spotify playlist item has no start or end offset, and
  classbeat neither plays nor controls playback. A trim would put the class timeline out of step
  with what plays from the second song on. Separately, Spotify's Developer Policy forbids ingesting
  Spotify Content into a model and restricts processing it (ADR 0010: the owner accepts that
  risk for metadata and MOWL's analysis in the beta circle, not for audio); classbeat has no Spotify
  audio at all and must not get any. Timing metadata about a trim would be harmless, but with no
  player honouring it, it would be wrong.
- **Swedebeat mixes.** A mix is "one long MP3" stitched inside the Swedebeat app, "there is no
  sanctioned API to create a mix" (ADR 0016, `docs/mcp-tools.md`). Its songs are found in the audio
  of a finished file. classbeat could not cut a part out, and playing the mix is Swedebeat's and MOWL's.
  Downloading the mix's audio is only for analysis, and the audio is deleted afterwards (ADR 0016).
- **User files.** classbeat has no upload, no stored audio and no audio processing, and
  `mowl-workouts` knows only two sources: a Spotify playlist and a Swedebeat mix (`mowl-api.md`:
  "Playlists", a program's `PlaylistID`). A user's own file is not a source MOWL plays through
  these. It would be a new product (storage, rights, playback in MOWL), not a trim option.
- **`mowl-workouts`.** It has no partial-song support. A map entry is a section with `start` and
  `end` in class time, a track is `{track, title, artist, bpm, spotify_id, sec}` and nothing says
  "from second a to b". `with_crossfade` shifts whole tracks. MOWL's workout has a `PlaylistID` and
  a timeline, no per-track offset (`mowl-api.md`).

So there is no source where the trimmed class would be what the instructor hears. The one thing that
could be built, the arithmetic, would show a timeline that no player follows.

## Decision

1. **Do not build part-of-a-song now. The safest reversible default is the current behaviour:
   every song at full length, the 9 s crossfade, the 10 % check.** Nothing in the app, the model
   prompts, the map or the stored data changes. Issue #580 stays open as `nice-to-have`, blocked
   by the preconditions below.
2. **Which sources may be trimmed: none today.**
   - Spotify tracks: **not allowed**, neither audio nor timing. No audio processing of
     Spotify content exists or may be added (ADR 0010, Spotify Developer Policy), and a timing-only
     trim would not match playback. If Spotify (or MOWL's playback of a Spotify playlist) ever offers
     a per-item start and end, this is reopened with ADR 0010's table and Spotify S2 and S3
     (`docs/providers/questions.md`).
   - Swedebeat mixes: only if Swedebeat or MOWL can play a mix song from an offset or build a mix from
     trimmed songs through a sanctioned API. Cutting the MP3 ourselves is out: the mix is theirs.
   - User files: not a source. Needs its own ADR (storage, rights, MOWL playback).
3. **Preconditions to unblock** (any one, then the design below is implemented, and this ADR is
   amended): (a) MOWL documents or confirms a per-track start and end for a playlist, or accepts a
   workout whose map is trimmed; (b) Swedebeat offers a sanctioned way to build or play a trimmed song;
   (c) the owner approves a user-file source as a separate product decision. Each is a question to a
   provider or a product decision, and so the owner's (AGENTS.md, "Escalation to the owner"). Agents
   do not contact providers or run live checks to find out (ADR 0010; a read-only capture needs the
   owner's authorization for that session).
4. **The design if unblocked** (recorded so a later PR starts from it; nothing here is built):
   - *Data.* Per song in the chosen list and in the layout, `trim: {start_sec, end_sec}`
     (whole seconds, from the start of the track; absent = the whole song). The analysis stays the
     shared, untrimmed one (`pool_view`, MOWL's BPM and sections); the trim belongs to the class
     (the `song_list` revision and `class_revisions.music`), is deleted with the class, and adds no
     Spotify-derived data beyond two integers. Limits: `0 <= start < end <= length_sec`,
     and an effective length of at least 60 s.
   - *Time.* A song's effective length is `end - start`. Class time is the sum of effective lengths
     less 9 s per change of song, the crossfade overlapping the trimmed end of one song and the
     trimmed start of the next. The 10 % check uses that total (`total_sec` takes effective lengths).
     The map shifts each track's sections by `-start`, drops sections and events outside the window,
     clips the sections that straddle an edge, and then applies the crossfade as today; BPM is
     unchanged, but **beat 1 moves**: the first downbeat is the first one at or after `start`, and
     "rpm: beat" still comes from the track under the interval. `music.MAP_VERSION` is bumped, and the
     revision's `map_version` keeps old classes valid.
   - *Model.* `get_pool` and `get_music` show the trimmed window: effective `length_sec`, and drops,
     breakdowns and sections in window time (seconds from the trimmed start), plus
     `trim: {start_sec, end_sec, full_length_sec}` so the model knows what was removed. The prompts
     say the total is computed by the backend. `SongRef` gains optional `trim_start_sec` and
     `trim_end_sec`; the model **proposes**, the backend validates the limits above against the
     analysis it loaded (an index, a bound or a length written by the model has nowhere else to go,
     as for titles and lengths today) and builds the stored trim.
   - *Who decides.* The model proposes a trim only when the brief asks for it or the user set one;
     the user sets and edits trims in the song list they approve (ADR 0008 §4: the approved list, its
     hash and "superseded or expired" include the trims, so a changed trim needs a new approval). The
     backend authorizes against the authenticated user as for every write; a trim never reaches an
     external account on its own.
   - *`mowl-workouts`.* A release (0.10.0 or later) with a pure `music.trim_map(map, trims)` and a
     `music` option in the spec, validated with the same checks, tests on fakes and a
     `MAP_VERSION` bump, only when (3) holds. classbeat consumes it through the wheel pin as for 0.9.0.

## Alternatives

- **Build the timing now and let the instructor skip by hand** (a "play from 0:45" note): rejected.
  The timeline would silently drift from the playlist, which is worse than a full-length song.
- **Trim Swedebeat or user audio ourselves:** rejected. It means storing and cutting audio, which
  classbeat does not do (ADR 0016 deletes the download), and the rights are Swedebeat's or the
  user's.
- **Spotify "start at" through the Web API's playback position:** rejected. classbeat does not
  control the player, and the playback API needs a device and the Premium app owner, a different
  product.
- **Better song choice instead:** the 10 % tolerance and the choice of songs by length already let
  the model fill the time; the cooldown and warm-up can sit on a song's quiet intro or outro.
  Any gap of seconds is a design issue, not a trim.
- **Close #580 as won't do:** possible; kept open so a provider change can reopen it. The owner decides.

## Consequences

- No code, no migration, no prompt change, no `mowl-workouts` change. Issue #580 gets a comment with
  this finding and stays `nice-to-have`.
- The preconditions (3) are product or provider questions; if the owner wants one pursued, it
  goes to `docs/status.md` "Waiting for the owner" and the questions list, like M5 in
  `docs/providers/questions.md`.
- If unblocked, the implementation PR split below applies (every PR with its tests on fakes, behind a
  setting that defaults off, cycling and running unchanged without a trim):

| PR | Scope | Acceptance criteria (how tested) | Developer / Reviewer |
|---|---|---|---|
| T1 | `mowl-workouts`: `music.trim_map`, spec `music.trims`, `MAP_VERSION` bump, docs | a trim shifts, clips and drops sections and events and moves beat 1; invalid trims are refused; an untrimmed map is byte-identical (unit tests on fixtures) | Sonnet / Sonnet |
| T2 | Wheel bump, `ChosenSong.trim`, `SongRef` fields, `total_sec` and `check_list` on effective lengths, `pool_view` window times | the backend computes the total from effective lengths; bounds, minimum length, duplicates and kept songs are checked; a model-written length has no field (unit tests); the 10 % check at both edges | Opus / Opus (song stage, approvals) |
| T3 | Design stage and map: trimmed map for `check`, `get_music`, timeline, prompts and `prompt_version` | a trimmed draft fits to window time; warnings use the trimmed duration; `rpm: beat` follows the trimmed track; eval smoke case on fakes | Sonnet / Sonnet |
| T4 | Revision, approval and save: trims in `song_list` revisions, the approval hash, duplicate and export, ADR 0007 table | an edited trim supersedes the approval; a copy keeps its trims; the saved workout matches the trimmed map (route and database tests) | Opus / Opus |
| T5 | Web: edit a song's start and end in the song list, show the effective length | shows and edits the window; invalid input is refused with the backend's message (component tests) | Sonnet / Sonnet |
| T6 | e2e on fakes | a trimmed class is designed, approved and saved with a trimmed total | Sonnet / Sonnet |

None of T1 to T6 is startable until a precondition in decision 3 holds and the owner says so.

**Amendment 2026-11-13 (#1035): section energy instead of a trim.** The owner kept #580 blocked and
asked for the honest neighbour: whole songs still play, but the model now sees each section's energy
(a number from 0 to 1 ranked within its track, from MOWL's map) in `get_music(track=i)` and `get_pool`,
and the prompts tell it to put the warm-up, recoveries and cooldown on quiet sections and hard work
on loud ones; the song stage prefers songs that stay quiet for the easy roles. It is not a trim: no
window, no `trims`, no change to a song's length or to the total. No audio is processed and no
provider is contacted; ADR 0010's note of the same date records why the number may reach the model.
`MAP_VERSION` stays 12: classbeat only reads a field the map already has (the vendored
`mowl-workouts` 0.11.0 map holds `energy` since 0.5.1, which left `MAP_VERSION` at 12), and a map
analysed before that simply has none, so the model sees no energy for it and the views leave the
field out; nothing breaks and nothing is re-analysed. The decision above and T1 to T6 are unchanged.

**Note 2026-11-13 (#1051): Swedebeat can cut songs, but only in its app.** The research for this
issue ([`docs/providers/swedebeat-mixing-findings.md`](../providers/swedebeat-mixing-findings.md),
PR #1048; summarised in [ADR 0016](0016-swedebeat-music.md)'s note of the same date) sharpens the
Context's "Swedebeat mixes" point. The app's Merge & Publish makes one MP3 from a playlist of separate
tracks, with a start and an end (1/100 s) and a tempo change (pitch kept) per track and a crossfade of
1 to 30 s per transition: the knobs decision 4 needs. But the render runs in the app on the user's
phone. No observed API renders a mix description or creates or edits a playlist, so precondition (b) of
decision 3 still does not hold. The routes that could unblock it, in order of fit:

1. **Server-side render API** (does not exist; needs Swedebeat's agreement): classbeat sends the
   tracks, cuts, tempos and crossfades for the signed-in user, Swedebeat renders and publishes the mix,
   and MOWL attaches it as it attaches mixes today (ADR 0016 (a)).
2. **Draft-playlist API** (does not exist; needs Swedebeat's agreement): classbeat creates the
   unrendered playlist in the user's account and the instructor presses Merge & Publish in the app.
   *(Inferred from a backup call after each edit: playlists are already stored server-side.)*
3. **Manual hand-off, extended** (ADR 0016 §7's approach; needs no API): classbeat shows the track list
   with cut points, tempos and crossfade lengths and the instructor builds it in the app, at a real cost
   in time per class. Adding it is a product decision for the owner, not scheduled here.

Routes 1 and 2 also need Swedebeat's licensing answer on cuts designed by a third party, and every route
relies on MOWL listing published mixes *(inferred, ADR 0016's note)*. The report's ten questions for
Swedebeat are the owner's to send; agents do not contact Swedebeat. Never a route: the app's embedded AWS
credentials, or imitating its upload or registration call. The decision is unchanged: #580 stays blocked,
nothing is built, and T1 to T6 wait for a precondition and the owner.

## Amendment 2026-11-15: unblocked for SwedeBeat mixes through the gateway (#580)

**Owner decision (2026-11-15, in chat):** classbeat no longer talks to SwedeBeat. It talks to the
owner's SwedeBeat gateway ([ADR 0035](0035-swedebeat-gateway.md)), whose contract
(`magpern/sb-gateway-service`, `docs/api.md`) has `POST /playlists` and `PATCH /playlists/{id}` with a
per-track `start_sec`, `end_sec` and `crossfade_sec`. That is route 2 of the 2026-11-13 note, a
draft-playlist API, and the owner supplies it: precondition 3(b) **holds for SwedeBeat**. The owner also
said #580 may be developed. The note's routes 1 (server render) and 3 (all by hand) are not used.
Spotify tracks stay **not allowed** (decision 2, unchanged); a class whose music is a Spotify playlist
has no trim.

### What changes in the decision

1. **Decision 1** ("do not build") is replaced by the build below, behind a setting that defaults off
   (`CLASSBEAT_SWEDEBEAT_TRIM`, ADR 0010's activation style). With it off, or with a Spotify class,
   nothing differs from today.
2. **The shape, which is simpler than T1 to T6.** classbeat does not time a trimmed song against a
   player. It writes a **private draft playlist** into the user's SwedeBeat account with the cuts, the
   instructor presses Merge & Publish in the SwedeBeat app (ADR 0016 §7, unchanged: classbeat never
   publishes), and the class is then designed on the **finished mix**, which MOWL analyses as today
   (ADR 0016 (a)). The timeline is therefore the real mix's, not arithmetic that a player might not
   follow. So `mowl-workouts` needs **no trim map** (T1 and T3 are dropped); the trim only affects
   the song stage's total, the hand-off's expected length and the draft.
3. **Data.** The song list carries per song `trim: {start_sec, end_sec}` (whole seconds, `0 <= start <
   end <= length_sec`, effective length at least 60 s) as in decision 4's *Data*. The match keeps each
   matched song's gateway `reference` (ADR 0035 §6 discarded it; **this amendment makes it the one kept
   gateway field**, an opaque catalogue id of at most 64 characters, owned, deleted with the class,
   never shown to a model). A song with no `reference` (unsure or missing) cannot be put in a draft.
4. **Time.** As decision 4's *Time*, minus the map: the class total is the sum of effective lengths
   less the 9 s crossfade per change, and the draft sets `crossfade_sec = 9` on each transition, so the
   mix and the check agree. The hand-off's expected length (ADR 0016 §7) and its verdict use the same
   total.
5. **The write is an external write**, so it follows AGENTS.md's gate for approvals and external writes:
   - Only after the user approves the song list, with its trims, in a **separate click**
     ("Create the draft in SwedeBeat") that names what happens: a draft playlist named for the class,
     private, **permanent (SwedeBeat has no delete)**, signed in with the user's own bearer.
   - **One playlist per class.** The first click calls `POST /playlists` and stores the returned
     `playlist_id`; every later click for that class calls `PATCH /playlists/{id}` (same id), so edits
     never pile up records. A class with a stored id never creates a second one. If `PATCH` answers
     502 "different id", the id is dropped, the user is told, and nothing is retried.
   - The backend validates every track against the approved list (`reference` and trim come from the
     stored list, never from the request body), caps it at 60 tracks (the gateway's limit) and sends
     only `reference`, `start_sec`, `end_sec` and the crossfade. No title, artist or free text comes
     from a model.
   - **Never `publish` or `merge-publish`.** Publishing is permanent and shows the owner's name and
     email; it stays the instructor's in the app. A guard test pins the paths: only `POST /playlists`
     and `PATCH /playlists/{id}` are allowed (ADR 0035 §7's guard is relaxed to exactly these two).
   - A 403 "writes are disabled" is a normal state: the button is hidden or says drafts are not on for
     this server; it never retries. A 401 follows ADR 0035 §5.
6. **The gateway's writes are the owner's switch** (a "Waiting for the owner" step, replacing "keep the
   gateway's writes off"): the owner turns writes on for the gateway, accepting permanent records in
   users' SwedeBeat accounts. Agents never turn it on, never call the playlist routes on a real gateway,
   and never test against a real account (AGENTS.md safety envelope). The live check is the owner's, on
   the owner's own account, with a throwaway draft.

### Revised PR split (replaces the table above)

| PR | Scope | Acceptance criteria (how tested) | Developer / Reviewer |
|---|---|---|---|
| T1 | Keep the match's `reference`; `ChosenSong.trim`; `total_sec` and `check_list` on effective lengths; limits and minimum length; the setting | the total uses effective lengths; bounds, minimum length and duplicates are refused; a model-written length or reference has no field; the 10 % check at both edges; setting off changes nothing (unit tests) | Opus / Opus (song stage, approvals) |
| T2 | Client: `create_playlist` and `update_playlist` in `swedebeat_gateway`, path guard relaxed to those two, fake gateway routes (write, 403 off, 502 different id), egress unchanged | only the two paths exist; a body carries only the allowed fields; 403, 401 and 502 map to named states; no live call (client and guard tests on the fake) | Opus / Opus (external write) |
| T3 | Revision, approval and the draft route: trims in `song_list` revisions and the approval hash, `swedebeat_drafts(owner_id, class_id, playlist_id)` with RLS, retention, GDPR export and deletion, the separate consent click, one playlist per class | an edited trim supersedes the approval; no draft without an approved list; a second click patches the stored id; a copy keeps trims but not the playlist id; another user's class is refused (route and database tests, RLS guard) | Opus / Opus |
| T4 | Hand-off: "Next, in the SwedeBeat app" says "open the draft playlist, Merge & Publish"; expected length and verdict use the trimmed total | the text and the verdict follow the total (component and unit tests) | Sonnet / Sonnet |
| T5 | Web: edit a song's start and end, show the effective length, the draft button and its states; sv and da | shows and edits the window; invalid input shows the backend's message; the button states (component tests) | Sonnet / Sonnet |
| T6 | e2e on the fake gateway | a trimmed class is approved, a draft is created once and patched on a second click, the verdict matches the trimmed total | Sonnet / Sonnet |

T1 to T3 are security-critical (approvals and an external write). Nothing here changes Spotify classes,
and the original T1 (`mowl-workouts` `trim_map`) and T3 (a trimmed map) are not built.

### Risks the owner accepts by approving this

- Every draft is a **permanent record** in the user's SwedeBeat account (no delete), and a user with
  many classes gets many drafts. The one-per-class rule and `PATCH` are the mitigation.
- The gateway, not classbeat, decides what SwedeBeat sees. If SwedeBeat objects to cuts built by a third
  party, the answer is the owner's (the report's licensing question stands).
- Cuts are metadata: SwedeBeat's app applies them when it merges. If a merge ignores a cut, the verdict
  (`too_long`) shows it and the instructor can override, as today.
