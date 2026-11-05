# ADR 0021: Use part of a song (trim an intro or outro, start mid-track) (#580)

Status: Proposed (issue #580, nice-to-have). The finding is that **the feature is blocked today**:
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
