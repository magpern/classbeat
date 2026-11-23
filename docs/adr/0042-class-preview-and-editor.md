# ADR 0042: Class Preview & Editor: audition and edit a class before its outputs are made

Status: **Accepted by the owner, 2026-11-21** (plan [class-preview-editor.md](../plans/class-preview-editor.md),
frozen after five review rounds). It gets its fresh Opus review in its PR (AGENTS.md review gate; first review
of a new security design). Nothing is built by this ADR. Milestone CP (roadmap Amendment 35); this ADR is
#1389. The work is E1 #1390, E2 #1391, E3 #1396, R1 #1392, R2 #1394, R3 #1397, S1 #1393, S2 #1395 and S3 #1398.

Depends on: [ADR 0036](0036-swedebeat-studio.md) (the studio, its mix spec, render and outputs), including
its **amendment B** (#1366, the DJ mix opt-in, `mix_remap_calc`), which this ADR builds on (see "Relation to
ADR 0036 amendment B" below).

Amends, when accepted:
- [ADR 0036](0036-swedebeat-studio.md):
  - §5 ("A playable audio preview is **deferred**") and amendment B3 ("the owner designs it
    separately"). This ADR is that design.
  - §4.2 and A7: the mix spec gains cut pins (§5 below), which also amends **A1** ("no spec-shape change"),
    and amendment **B4, B9 and B10** (§8 below: what an edited draft is, `applied_form`, and the hash).
  - §1 (the studio draft's `content_hash` is `studio_hash`): a draft with an edit overlay is hashed over the
    overlay too (§3, "Where the overlay lives").
  - Alternatives ("Rendering before approval to preview the exact mix. Rejected"): preview **windows** are
    rendered before approval. The output render is unchanged: once per merge attempt, after approval, from
    fresh downloads.
  - Consequences ("It never keeps it"): a preview cache, scoped per owner, may keep source tracks and clips
    (§6.5).
- [ADR 0016](0016-swedebeat-music.md): the same exception to "never keeps audio", for the preview cache only.
- [ADR 0007](0007-classes-revisions-and-saving.md): user-edit revisions with their own write path, the edit
  overlay and §2's `content_hash`, the revision cap, and optimistic concurrency on revision writes, approval
  and `POST /classes/{id}/current` (§3, §4).
- [ADR 0005](0005-jobs-and-leases.md): the `preview_render` job kind, which has no `class_id`. R2 widens the
  `jobs_class_id` CHECK (migration 0052, NULL only for `player_remove` today) for it and keeps it outside
  `jobs_one_open_per_class`.
- [ADR 0009](0009-spotify-connection.md): §1, two optional preview scopes and `preview_granted`; §10, the
  playback calls and their egress paths, with the rule that a browser-sent id is never used (§7).
- [ADR 0036](0036-swedebeat-studio.md) §4.1 ("the audio is deleted at once"): the same exception as ADR 0016,
  for the preview cache (§6.5).
- [ADR 0021](0021-use-part-of-a-song.md): **no change to its Spotify rule.**
  - Spotify songs still play whole, in MOWL and everywhere.
  - A Spotify "start position" in the editor is only an audition position.
  - The alignment correction (§7.4) is not a trim.

Related: [ADR 0002](0002-tenant-isolation.md) (new owned tables, RLS),
[ADR 0010](0010-live-activation-permissions.md),
[ADR 0014](0014-account-export-and-deletion.md) (deletion and export of the new tables),
[ADR 0038](0038-composer-flow.md) (the class's one open job), [ADR 0040](0040-spotdown-analysis-service.md)
(analysis, timing classes, alignment `offset_s`).

## Context

Today an instructor sees the suggested songs, the workout and, for a studio class, a timeline of cuts and
crossfades. They cannot hear any of it before the outputs become permanent:
- SwedeBeat has no delete;
- MOWL and Spotify are external writes.

ADR 0036 §5 deferred audio ("timeline first, and audio later").

The owner asked (2026-11-21) for one **Class Preview & Editor**, placed before approval, where the instructor
can:
- audition the music against the zone blocks;
- edit the workout timeline and the audio timeline;
- then approve.

It has two audio backends: SwedeBeat audition, and remote control of the user's own Spotify. The owner set
one hard limit: classbeat is **not** a gym player, so it has no class-performance mode.

**Facts that shape the design:**
- classbeat renders the SwedeBeat mix itself (`studio/render.py`, `renderchild.py`): deterministic, sandboxed,
  a 320 kb/s CBR MP3 through libsndfile. `merge-publish` receives exactly those bytes. A preview made by the
  same renderer from the same render plan is faithful to the **mix** output.
- The SwedeBeat **playlist** output is different: the SwedeBeat app applies the cuts and crossfades itself
  (§6, gates L3 and L6). Its preview is labelled approximate.
- Spotify audio never reaches classbeat. The Web API can start a track at `position_ms`, seek, and report
  `progress_ms`, `is_playing` and `timestamp`. That needs `user-modify-playback-state`,
  `user-read-playback-state` and Premium. Spotify documents neither latency nor ordering ("The order of
  execution is not guaranteed").
- `mix_cut_policy.placed()` snaps a proposed cut to a trusted candidate on an `exact` track, falling back to the
  bar grid when no candidate is near (and for a track with no stored timing decision), and only rounds it on an
  `approximate` one, so on an `exact` track a manual cut would be moved silently today.
- Workout edits go only through the model today. The beat-1 correction (migration 0029) is the one direct,
  per-song correction.

## Decision

### 1. Decision summary

| # | Decision |
|---|---|
| D1 | One editor with interchangeable **transports**. The timeline works in class time |
| D2 | **Two edit layers**, each with its own operations and invalidation: the workout timeline (zone blocks) and the audio timeline (pieces, cuts, crossfades, alignment) |
| D3 | Edits are ADR 0007 revisions. Undo and redo move a cursor over the revision chain through `POST /classes/{id}/current`, with optimistic concurrency |
| D4 | Instructor edits survive AI regeneration as **overrides, locks and anchors** |
| D5 | Cut pins have a **requested**, an **effective** and a **confirmed** position. Reanalysis never moves a confirmed cut |
| D6 | SwedeBeat audition renders **windows** with the existing renderer. No new engine, no ffmpeg, no second audio pipeline |
| D7 | The deterrent is a **15-minute continuous listening session** for classbeat-served audio, counted as actual playback in the browser, plus no player mode. The server authorizes sessions and caps delivery. It is a deterrent, not DRM. No watermark, no session count, no daily quota |
| D8 | Spotify audition is normal planned functionality, isolated: no workout-editor or SwedeBeat code imports Spotify-preview code |
| D9 | Spotify accuracy is **measured, not promised**. The cursor comes from observed playback state only |
| D10 | Preview caches are owned data with retention: 14 idle days, a per-owner cap, purged with the class or the account |
| D11 | **Legal and licensing assumption (owner, 2026-11-21).** classbeat is a personal, non-commercial project. The owner handles every legal, licensing and provider permission for Spotify audition and SwedeBeat preview. This is recorded once, here. No feature flag, gate or approval workflow exists for it, and later issues and plans do not raise it again. Provider-enforced technical limits still apply: scopes, Premium, rate and quota limits, development-mode user limits |

### 2. Positions, provenance and transports

**Coordinate spaces.**

| Space | Meaning |
|---|---|
| `classMs` | Time in the class, 0 to the total |
| `piece` | `{index, occurrence, recording}` |
| `recording` | `{kind: "swedebeat", reference, sha256}` or `{kind: "spotify", track_id, analysed_copy: {spotdown_source_id, sha256}}` |
| `sourceMs` | Position in the **analysed file** of that recording |
| `transportMs` | Position the transport plays (the rendered clip, or the Spotify recording) |

**Provenance.** Every mapping result carries:

```
{analysis: {map_version, spotdown_job, timing_class},
 alignment: {offset_ms, source: "spotdown"},
 correction: {offset_ms, set_against_analysis} | null}
```

- The editor shows provenance on hover.
- The analysis fact (spotdown's alignment) and the instructor's correction are never merged into one
  number, and a correction never writes an analysis.

**Studio mapping.**
- `classMs → [(piece, sourceMs, gain)]` is **new** (E3's `timeModel.ts`, and a pure backend twin where needed):
  `timeline_calc.py` has no per-instant gain today. It is derived from the composed map plus the render curves.
- A crossfade instant maps to both pieces, and both are audible in the rendered clip.
- `transportMs = classMs − clip.start_ms`.

**Spotify mapping.**
- The class was fitted with a crossfade constant `C` (9.54 s, `CROSSFADE_SEC`, MOWL's own value since #1427, amendment of 2026-11-22 to ADR 0006; it was 9 s before, and stored maps keep their 9.0).
  `C` is **read from the class's music map**, never hard-coded.
- Song *i* occupies `[S_i, E_i)`, with `E_i = S_i + L_i` and `S_{i+1} = E_i − C`. `[S_{i+1}, E_i)` is an
  **overlap region**: in MOWL the rider's own Spotify crossfades there.
- The Spotify transport plays **one original track at a time** (`uris=[track]`), whole, from a position. It
  never overlaps or fades, and `capabilities.crossfades = false`.
- Overlap regions are hatched in the editor: "In the class the songs overlap here; this preview plays one song
  at a time". classbeat can neither read nor control the user's Spotify crossfade setting. Playing a single
  track leaves nothing to fade into, and the editor says so once.
- **A click at *t* inside an overlap** offers both songs as lanes:
  - the **incoming** song (the default), at `sourceMs = t − S_{i+1}`;
  - the outgoing song, at `t − S_i`.

  The choice is explicit, and the cursor stays on that lane.
- **The cursor from an observed `(track_id, transportMs)`**, in this order:
  1. a `track_id` that is not the requested recording → `external` (no cursor, "Spotify is playing something
     else"; nothing auto-resumes);
  2. `timing_class` `unusable`, or no alignment → `unaligned` (the lane is highlighted, with no position);
  3. otherwise `sourceMs = transportMs − alignment.offset_ms − correction.offset_ms` and
     `classMs = S_i + sourceMs`:
     - inside `[S_i, E_i)` → a cursor with an uncertainty band, labelled inside overlaps;
     - outside → `outside_window` ("This part of the Spotify recording is not in the class").

  After the track ends, Spotify stops or autoplays; autoplay shows as `external`.

**The transport contract** (`frontend/src/preview/`, a pure `timeModel.ts` plus the transports):

```
interface PreviewTransport {
  capabilities: {exactMix, crossfades, seekGranularityMs, kind}
  audition(req: {piece, sourceMs, classMs}): Promise<Ack>   // a user gesture each time
  pause(): Promise<void>
  observe(): TransportState                                  // never inferred from commands
}
TransportState = {status, piece?, transportMs?, observedAt, uncertaintyMs, reason?}
```

- `status` is one of `idle`, `starting`, `playing`, `paused`, `external`, `unaligned`, `outside_window`,
  `no_device` or `error`.
- The cursor is drawn only from `observe()`.
- There are three transports:
  - `SilentTransport`: a clock-driven cursor with no audio;
  - `RenderedClipTransport`;
  - `SpotifyConnectTransport`.
- `capabilities.exactMix` is true only for a studio draft whose chosen output includes the mix.

### 3. Workout timeline editing

**The editable unit** is the **block**: one `Interval` (`agent/schemas.py`: `sec`, `zone`, `rpm`, `standing`,
`game`, `cue`), inside its `Segment`.

**Operations.** All are pure (`workout_edit_policy.py`), whole seconds:

| Operation | Behaviour |
|---|---|
| **Drag boundary** (default) | Block *k* grows and block *k+1* shrinks by the same amount, so the **total is kept**. Clamped to each block's minimum (`workout.validate`), stopped at a locked block. Across a segment boundary it moves that boundary |
| **Move** | Swaps a block with its neighbour. The total is kept |
| **Split** | At *t* inside a block: two blocks with the same attributes, each at least the minimum |
| **Merge** | Two adjacent blocks of one segment take the first block's attributes. The UI asks when the zones differ |
| **Set attributes** | `zone`, `rpm`, `standing`, `cue`, `game`. **Never timing, never the music** |
| **Change class duration** | A separate, explicit operation (below) |

**Class duration.**
- ADR 0036 amendment B, item 2, establishes that `agent.design.check` fits without `fit` and **keeps the
  workout's own total**: intervals snap to a beat or a bar within 6 s.
- So the workout total is not forced to equal the music total. It is bounded by the class-length and plan
  gates (B6: `class_length_out_of_tolerance`, `plan_class_too_long`).
- An explicit duration change therefore resizes the **last unlocked block** and is checked against those
  gates. This is the plan's defined fallback, now confirmed.
- An audio edit that changes the composed total moves the workout by B4a's song-anchored remap
  (`mix_remap_calc`), never by a model.

**Every edit is validated** by `agent.design.check(draft, map)`: `workout.validate`, `fit_to_music`, the timing
gate. These are the same rules as a model draft.

**Every audio edit runs amendment B's one order of checks.** The editor's audio edits are the **cut pin**
operations (set, confirm, re-confirm, clear, §5) and **change class duration**. Reordering, removing a song or
changing a crossfade are not offered: such a spec is not `applied_form` and is refused `mix_not_applied`.
A workout-only edit changes no composed total and skips the first two steps:
1. `mix_plan_policy.checked` (cuts placed, hard rules);
2. `class_length_out_of_tolerance` (B6, the 10 % class-length rule) on the composed total;
3. the remap and fit, refused `workout_does_not_fit` if the workout cannot follow (B4a);
4. `plan_class_too_long` (`max_draft_seconds`, a plan entitlement) and `plan_not_allowed`.

A pin changes the composed total like any cut, so it is no exception. A refused edit stores nothing. B9's
`applied_form` (§8) re-runs steps 1 and 2 on the pinned spec. Step 4 runs again wherever
`check_stored_draft` runs today (MOWL Save and Update, and `POST /classes/{id}/current`); `outputs_admit` has no
plan gate and gets none. E1 and E2 test each refusal with a pin present.

**Overrides, locks and anchors** live in the draft's **edit overlay**.

**Where the overlay lives, and the hash (amends ADR 0007 §2, ADR 0036 §1 and B10).**
- The overlay is a new nullable column `edit_overlay jsonb` on `class_revisions` (E2's migration), **not** a
  field of `content`. So it is never sent to MOWL, never counted against the 64 KB `content` check, and the
  strict `WorkoutDraft` is unchanged. It has its own bound: at most 200 records and 32 KB.
- `content_hash` stays exactly what it is today for a revision with no overlay (`NULL`), so no existing row,
  approval or saved program changes. A revision **with** an overlay is hashed over the canonical
  `{content, mix_spec?, overlay}` (`mix_spec` only for a studio draft), by a new `edited_hash` beside
  `studio_hash`. This holds for Spotify drafts too. An approval binds the overlay, and a lock-only or pin-only
  edit is a real revision with a different hash.
- **One hash function, chosen by whether `edit_overlay` is NULL, at every place that recomputes and compares a
  stored hash.** Those places are at least `saving/approval.py` (MOWL Save approval),
  `swedebeat_studio/current_draft.py` (the outputs flow) and `swedebeat_studio/revise_kind.py`, plus
  `design/revision.py`'s write. They all call it, so no integrity check is weakened or skipped for an edited
  draft. E2 tests Save, `current_draft.load` and the outputs admit with an edited draft, and that a tampered
  overlay is refused at each.
- Pin records (§5) are part of the overlay.

| Record | Meaning | Created by | On AI regeneration |
|---|---|---|---|
| **Override** | The instructor's value for one field: `{target, field, value, ai_value_at_set}` | Automatically, by any direct edit (a zone change, a drag as field `boundary`, a cue, rpm) | **Re-applied** to the matching block, with no lock needed. A Green→Yellow change survives |
| **Lock** | Automation must not change this block: `{target, scope: all \| timing \| attributes}` | An explicit "Lock" | **Enforced.** The model is told. An output that changes the block within the scope is refused `overlay_violated` and counts as a correction round |
| **Anchor** | A boundary sits on a musical event: `{boundary: (target, start \| end), event: {recording, sourceMs, kind: drop \| downbeat \| section, provenance}}` | "Snap to music" | Kept if its target matches. On audio edits the boundary follows the event (the B4a remap, then a refit). If the event's piece leaves the mix, the anchor is **orphaned**: a notice, no effect |

- **Targets** are `{segment_kind, span: [startMs, endMs)}` at the time of setting, because blocks have no
  stable id across regeneration.
- **Reconciliation** after regeneration, or after a B4a remap, picks the block with the greatest class-time
  overlap with the record's span, if that overlap is at least 50%. The record's span then updates. With no
  such block, the record is **unapplied**: listed in a banner, kept, and without effect until re-targeted
  or cleared.
- **The order is fixed:** locks are checked, then boundary overrides applied, then attribute overrides, then
  anchors refit, then `agent.design.check`. A failure stores nothing and names the record.
- **Clearing.** Per record: **Use AI value**, **Unlock**, **Remove anchor**; for everything: **Clear all my
  edits**. Each clear is a revision, so it can be undone. A chat instruction never silently clears an
  override; the model's differing value shows as "Your edit kept (AI suggested Green)".
- **An audio edit that would move a timing-locked block** refuses `locked_block_conflict` and names the
  block. Attribute overrides are never touched by audio edits.

### 4. Revisions, undo and concurrency

- A committed edit (on drop or blur) is a new `draft` revision with provenance `origin: "user_edit"`. There is
  no model call and **no job**: `design/revision.py`'s write is fenced by a job, so a user edit has **its own
  write path** (the `classes/duplicate.py` pattern): session auth, CSRF, the owner from `UserContext`, the
  class row locked, `base_revision_id` checked in the same transaction, the same validation
  (`agent.design.check` and the overlay order) and the same hash.
- **Cap.** `CLASSBEAT_REVISIONS_PER_CLASS` counts only `revise_draft` today and bounds storage (ADR 0007 §4).
  User edits get their own cap (200 per class, `409 revision_limit`). The client **coalesces** edits (a drag is
  one edit, committed on drop). Past the cap a user can still undo and redo (they move the pointer and store
  nothing); a **clear** is an edit and counts against the cap, so alternating clear and undo cannot grow storage
  without bound. A fully cleared overlay is stored as **NULL**, so the hash returns to the no-overlay form.
- **Which flows offer the editor before approval.**
  - A **studio draft** before its outputs consent, and a **Spotify draft** (a class with a `draft` revision)
    before Save, are editable.
  - The **composer flow** (ADR 0038) has no draft revision before approval: the design is the `preview`
    inside the waiting `song_list`, and the class's one open job is `waiting_for_approval`. The editor is
    **not offered** there. After approval there is a draft and the editor is offered. A user who wants to edit
    earlier uses ADR 0038 §6's "Ask for changes", which replaces the waiting job.
- **A job running on the class** blocks edits, as today (`class_busy`).
- **Undo and redo** move along the chain with `POST /classes/{id}/current`. A new edit after an undo
  branches and clears the client's redo stack.
- **Optimistic concurrency.** Every write (edit, undo, redo, clear, approve) carries `base_revision_id`. The
  server checks it against the current revision **in the same transaction**; a mismatch is
  `409 stale_revision` with the current id.
  - The client reloads, keeps the unsent edit, and offers to reapply it.
  - `POST /current` gains `expected_current_id`, with the same 409.
- **Approval is guarded the same way.** The approve request carries `base_revision_id` and is refused
  `409 stale_revision` if the current revision moved.
- **Drag and the fit.** `agent.design.check` returns the **fitted** spec, and `fit_to_music` snaps interval
  starts to a beat or a bar within 6 s, so a dropped boundary can move. An override stores the **fitted**
  value (what the user will get), and the UI shows the snapped position immediately and says so
  ("snapped to the beat, +1.4 s"). Nothing moves silently. A block with `ftp_test` is not split, merged or
  resized by drag (its `sec` must match), and the duration operation skips it.

### 5. Cut pins (the mix spec; amends ADR 0036 §4.2 and A7)

**Pin records live in the edit overlay** (§3), the source of truth, keyed by a target
`{reference, occurrence, edge: start | end}`: the song's SwedeBeat reference and which occurrence of it in
the mix (A3 allows repeats; the model sees only indexes, so the target is the song, never a position). The
stored `mix_spec` carries the **derived** pins: `MixTrack` (`studio/mix_spec_model.py`, `extra="forbid"`)
gains optional `start_pin` and `end_pin`, absent when there is none, so a spec without a pin is byte-identical
to today's and keeps its hash. `mix_plan_policy._track` accepts the two fields and keeps refusing every other
unknown one. This **amends A1**.

```
{requested_sec,          # the instructor's request, 0.01 s
 mode: "bar" | "exact",
 confirmed: bool,        # "Confirm cut" after listening
 confirmed_sec?,         # the effective cut when confirmed
 confirmed_against?}     # the analysed file's sha256 when confirmed
```

**How the effective cut is set.** It stays in `start_sec` and `end_sec`. In `mix_plan_policy._track`, a pinned
cut **skips `selected()`**, so an AI candidate never replaces a pin:
- `bar` → `mix_cut_policy.snapped(requested_sec)`;
- `exact` → `requested_sec`, with a non-blocking `off_beat_cut` hint (`mix_hint_policy`);
- confirmed → exactly `confirmed_sec`, never re-snapped.

**Reanalysis** of the same sha256:
- unconfirmed `bar` pins re-snap, shown as "moved by reanalysis: +0.12 s";
- confirmed pins **never move**, and an off-beat one gets only `off_beat_cut`.

**A new sha256** (`music_changed`) puts every pin of that track into "listen again". Nothing moves. Approval
is refused by the **server**, not only the UI: **`pin_unconfirmed`**, placed in B9's fixed order immediately after
`mix_not_applied` and before `playlist_has_repeats`
(`invalid_input`, `mowl_needs_mix`, `mix_not_chosen`, `mix_not_applied`, **`pin_unconfirmed`**,
`playlist_has_repeats`, `output_off`), at create and again when the job runs (`outputs_admit`), until each such pin is
re-confirmed or cleared (the beat-one pattern, migration 0029). E1's acceptance criteria and tests include it.

**The hard rules stay**, whether or not a cut is pinned:
- `cut_out_of_range`, `window_too_short`, `not_a_permutation`;
- every crossfade rule;
- `mix_too_long`;
- `loop_needs_exact_timing` and `loop_not_on_phrase`: owner default, **no exact-pin override of the loop
  rules in this milestone**.

A refused pin leaves the last valid revision, and the UI shows the reason.

**Pins and regeneration.** A new model spec never carries pins. The overlay's pin records are re-applied to
it by target (`reference`, `occurrence`); a record whose song is no longer in the mix is **unapplied**
(listed, kept, without effect). A song replaced by another drops its pins with a notice.

**Pins and amendment B.**
- Pins exist only on a song that has a cut, under `dj_mix` with `trim`.
- A song made whole (`keep_whole`, or `trim` off) has no cut, so its pins become **unapplied**: listed, kept,
  without effect.
- `mix_choice_policy` derives the spec first; the overlay's pins are then applied to the derived spec and
  checked by `checked` as above. §8 states the consequence for B4 and B9.

### 6. SwedeBeat audition

#### 6.1 Windowed render

- The render plan (`render_policy`) gains `window: {start, end}` in output samples. `validate_plan` refuses it
  on an output plan, so the output path cannot carry it.
- **Per-piece state, no pre-roll guess.** The child decodes every piece that overlaps the window **exactly as
  the full render does**: `renderchild.track_blocks` decodes from the file's first sample through one soxr
  stream and drops samples until the piece's `src_start`; it does not seek. The window keeps that, so the
  resampler state and the fade-curve state from the fade start are the same. It only limits **writing** to the
  window. A decode from the file's start costs more for a late piece or a loop piece (case 12), which R1
  measures; a real seek would change a source not at 44.1 kHz (case 11), so none is used.
- `validate_plan` tells an output plan from a preview plan by a new explicit `purpose: "output" | "preview"`
  field (default `output`); `window` is valid only with `preview`, and the output path builds `output`.
- The implementing PR audits every stateful stage: decoder seek, resampler, fade gains, bass-swap filters,
  mixer buffer. If one carries state across pieces, the window decodes from the earliest piece whose state
  reaches it.
  - **The audit (R1, #1392).** No stage carries state from one piece to another, so the earliest piece
    that *meets the window* is the earliest to decode; a piece that does not meet it is not opened.
    | Stage | State | Where it starts |
    |---|---|---|
    | decoder (libsndfile) and soxr stream | the decoder's and the resampler's history | the file's first sample, per piece (no seek) |
    | fade gains | none: a function of the sample's position in its piece | any sample |
    | bass-swap bands (`render_curves`) | one held fade with `HALF` samples of context, the FIR reads zeros outside | the fade, per piece |
    | mixer buffer | the samples not yet written | the window's start; pieces add in plan order, as the full render does |
    A piece that meets the window is fed through `shaped` from its first block; only the blocks outside
    the window are not added to the mixer. So the window's samples are the full render's, bit for bit
    (the tests assert the 1e-6 tolerance of §6.2; the measured maximum difference over the 12 cases is 0).
  - **The decode cost of a late or loop piece (case 12), measured** on one cloud container core: reaching a piece's
    first sample by decoding the file from its start costs about 0.06 s per minute of file position for a
    44.1 kHz MP3 and about 0.1 s for a 48 kHz one (one soxr stream): 0.3 s at 5 min and 0.5 s at 8 min (44.1
    kHz), 0.5 s and 0.9 s (48 kHz). A whole child run for a 4-minute window whose first piece began 5 min
    earlier took 9.7 s, against 9.1 s for the window at the mix start: the 320 kb/s MP3 encode (about 2.3 s per
    minute of output) dominates and the late piece adds about half a second. No pre-roll or seek is worth it.
  - `validate_plan` also **requires** a `window` on a `preview` plan (a preview with none would be a whole
    mix outside the output path), and bounds it: whole samples, `0 <= start < end <= composed`, at most 6 min
    (`MAX_WINDOW_FRAMES`). The published path (`render_files`) names `purpose: "output"` itself.
- **Quality:** 320 kb/s CBR MP3, the same libsndfile encoder as the final mix, one encode per window. The
  bass swap follows the same `render_plan` rule and setting as the output (amendment B8: off by default).

#### 6.2 Fidelity

**The criterion.** A clean window's PCM, through a test-only float WAV output of the same child, equals the
same span of a full WAV render. The expectation is bit-exact, with a tolerance of 1e-6.

**The cases:**
1. inside one piece;
2. starting mid-crossfade;
3. ending mid-crossfade;
4. a whole crossfade;
5. a bass swap with its swap point and ramp;
6. starting inside a bass-swap fade;
7. mix start;
8. mix end;
9. a loop join;
10. a gap;
11. a source not at 44.1 kHz;
12. the earliest overlapping piece starting ≥ 5 min before the window.

**What is not claimed, and what is checked.**
- MP3 windows are **not** claimed to be byte-identical to spans of the final MP3, because of encoder framing,
  delay and padding.
- An MP3 window's decoded length is within `render_policy.length_ok`'s 100 ms.
- The published render is unchanged: the same plan gives the same sha256 as before this work.

#### 6.3 Listening sessions: the 15-minute limit

| Action | Effect |
|---|---|
| **Play**, an explicit gesture | A **new** session from the current position, with the counter at 0 |
| Seek, timeline click, track change, any workout or audio edit, while playing | **Same** session; the counter is not reset |
| Manual Pause | **Ends** the session |
| 15:00 of accumulated playback | Auto-pause, ending the session, with "Preview paused after 15 minutes. Press Play to continue." |

**The counter (client).**
- It is **elapsed listening time on a monotonic clock** (`performance.now()`), never `currentTime`
  differences, which jump on a seek.
- It starts on the active element's `playing` event. It stops on `pause`, `waiting`, `stalled`, `seeking`,
  `ended` and `emptied`, and restarts on the next `playing`. The total is `Σ (stop − start)`.
- So buffering, pauses, seeks in progress, rendering and the gaps between windows are excluded.
- A 1 s tick only checks the threshold; it never adds time.
- The counter is not persisted.

**The server keeps authorization and reasonable limits only.**
- `preview_sessions(owner_id, class_id, id, created_at, expires_at, delivered_ms)` is owned, with RLS, and
  purged after a day.
- `POST /api/classes/{id}/preview/sessions` uses session auth and CSRF, as other writes. The lifetime is
  30 min.
- Each clip request carries the session:
  - another owner's or class's session → `404`;
  - after `expires_at`, or once `delivered_ms` would pass 25 min of audio → `410 preview_session_ended`.
  - `delivered_ms` counts **each distinct clip once per session** (its length when first served in the
    session), never bytes or requests, because a media element re-requests a clip with `Range` on a seek or a
    reload. R2 tests that repeated `Range` requests for one clip do not use up the cap.
  - Session creation and `preview_render` jobs are **rate-limited per owner** (resource protection, not a
    session count or a quota: D7 stands).
- **The server cannot verify how long audio was heard, and does not try.** A modified client gets at most one
  lifetime's delivery per Play, and a script can keep pressing Play. This is a product-use deterrent, not DRM
  and not access control, and no further mechanism is added for it.
- Spotify playback has no session; Spotify controls it.

#### 6.4 Windows, joins and cache

**Placement** (`preview_window_calc.py`, pure). The input is a start *p*: Play or seek gives the current
position, and **Review transition** gives the fade start − 15 s. Then:
1. The nominal window is `[p, p + 4 min)`.
2. **A selected transition is whole** inside the window, plus 15 s after its fade end.
3. **Ends avoid transitions by extension, never by shortening.** The guard zones are each crossfade ± 15 s
   and each hard cut ± 15 s. An end inside a zone, or a chain of touching zones, moves **later** to its end,
   up to 6 min.
   - If 6 min still falls inside a zone, the window ends at 6 min, and that join may fall in a transition. This
     is documented, rare, and never the selected transition.
4. No window is shorter than 60 s, except at the mix end.
5. The next window of a session starts at the previous one's end.

**Joins.**
- The first implementing PR measures libsndfile's MP3 encoder delay and end padding, and whether a gapless
  (LAME or Xing) tag is written, from a known click. It also measures Chromium's decode, and records both
  here.
- The first release uses **no gapless technique and no second audio engine**: two `<audio>` elements, the next
  window preloaded and switched on `ended`.
- The short gap at a join is **accepted and documented**. A trivial correction (starting the next clip at
  the measured delay) may be applied if the measurement shows it is enough; nothing beyond that.
- **The measurement (R1, #1392).** A known click (8 samples of 0.9 in a silent stereo file) was written
  through the child's own encoder (`soundfile` 0.14.0 over libsndfile 1.2.2, LAME 3.100 inside it; 320 kb/s
  CBR, 44.1 kHz) and decoded three ways, for a direct encode and for a real child window render
  (`{"purpose": "preview", "window": [8 s, 14 s)}` over a source with the click at 10 s):
  - **A gapless tag is written.** libsndfile writes an `Info` frame (the LAME header of a CBR file) first,
    with `LAME3.100` and the encoder delay **576** samples and end padding **756** samples. The encoded
    stream is `frames x 1152` samples: for 3 s (132300 samples) it is 116 frames, 133632 samples,
    132300 + 576 + 756. A decoder adds its own 529 samples of delay, so a decoder that **ignores** the
    tag shows the click **1105 samples (25.1 ms) late** and a stream 1332 samples (30.2 ms) long
    (measured with the `Info` frame cut off, decoded by ffmpeg).
  - **Decoders that read the tag are exact.** libsndfile (`soundfile.read`), ffmpeg and **Chromium**
    (Google Chrome for Testing 153, `decodeAudioData` and an `<audio>` element's `duration`) all gave
    exactly the written length (132300 samples for the 3 s file; 264600 for the 6 s window, 6.000 s) and
    the click at exactly its written sample (44100 and 88200), so a window's decoded length is the window's
    to the sample, well inside `length_ok`'s 100 ms, and a window's audio starts and ends at its edges.
  - **So the first release needs no correction at a join**: two `<audio>` elements switched on `ended`
    differ from gapless only by the element switch, which is the accepted gap. A client that ignores the
    tag would add about 25 ms at the start and 5 ms of padding at the end of each clip; nothing is done
    for such a client.

**The cache key** is `sha256` of a canonical JSON of:
- the sha256 of the **stored plan**: the reference-keyed `output_body_policy.render_plan`, not the child's plan
  with local paths and `output`;
- every source file's sha256;
- the window in absolute class time `[start_ms, end_ms)`;
- the encoder settings;
- the `libsndfile` and `soxr` versions;
- `RENDERER_VERSION`, kept current by a test that hashes `renderchild`, `render_curves` and `render_policy`.

The session is not in the key.

**Accepted trade-off.**
- Any audio edit invalidates all of that draft's clips. Clips render on demand per window listened to, and the
  source-track cache is unaffected.
- A workout-only edit keeps the plan hash, so it is a cache hit.
- Localized sub-plan keys are deferred.

**Audition plays stored drafts only.** Amendment B's read-only `mix-choices/preview` has no audio. To hear a
choice, the user applies it: B4 makes a new draft, which can be undone.

#### 6.5 Storage, delivery and the job

- **Storage.** Two new owned tables, each with RLS: `preview_sources` (the per-owner source-track cache:
  reference plus sha256, the file's path) and `preview_clips`. Both are in the ADR 0014 registry and the
  retention registry (ADR 0007 §11).
  - `preview_clips` is **unique per `(owner_id, key)`** and the bytes live under a **per-owner path**: the
    content key alone would share a file across owners, so one owner's purge would delete another's clip and
    a hit's timing would leak across owners.
  - Both are purged after 14 idle days, with the class or the account, and under a per-owner size cap. The
    cascade covers rows only, so the delete path **removes the files** it names, and an **orphan sweep** (a
    maintenance job) deletes volume files that have no row and rows whose file is gone.
  - Output renders still use fresh downloads (ADR 0036 §6, unchanged).
- **The job.** A low-priority `preview_render` job (ADR 0005) runs the existing sandboxed child. It uses the
  gateway's read-only download, with its existing caps, and makes no external write.
  - **It does not occupy the class's job slot.** `jobs_one_open_per_class` counts every open job of a class,
    `waiting_for_approval` included (migration 0004, ADR 0038 §6), so a job carrying the `class_id` would make
    every audition `class_busy` and block edits, undo and redo. `preview_render` is created with `class_id`
    NULL and an explicit `preview_class_id` (an owner-inclusive foreign key to `classes`, `ON DELETE CASCADE`,
    and re-checked in the job's fenced store so no clip is stored for a class deleted while it rendered), for
    the purge and the account deletion, and it is outside the one-open-job index. The trade-off: it cannot be blocked by another class
    job, so the per-owner rate limit (§6.3) and the lease and fencing of ADR 0005 bound it. R2's acceptance
    criteria include that an edit and an undo succeed while a clip renders.
- **Delivery.**
  - Clips are owner-only, with `Content-Disposition: inline` and `Cache-Control: private, no-store`. These
    headers keep clips out of caches; they do **not** stop saving or recording, and nothing here claims they
    do.
  - The anti-gym-player measures are the session, Play by gesture only, and the absence of a "play class"
    mode, a queue across sessions, a rider view, or cue display beyond the editor's.

### 7. Spotify Connect audition

**7.1 Scopes (amends ADR 0009 §1).**
- An opt-in "Enable listening preview" re-consent adds `user-modify-playback-state` and
  `user-read-playback-state` through the existing PKCE flow and the BYOK wizard.
- `scope_granted` is unchanged; a new `preview_granted` check is added. A connection without the scopes keeps
  working as today.
- The granted scope is **not stored today** (`connect.py` checks it and discards it) and `authorize_url` sends
  a fixed `SCOPE`. S1 adds a column for the granted scopes (a migration) and a scope parameter for the opt-in
  flow, and reads `preview_granted` from the column.

**7.2 Proxy.**
- These owner-scoped endpoints call `PUT /v1/me/player/play` (`uris`, `position_ms`), `PUT …/pause`,
  `GET /v1/me/player` and `GET /v1/me/player/devices`:
  - `POST /api/classes/{id}/preview/spotify/audition`;
  - `POST …/pause`;
  - `GET …/state`;
  - `GET …/devices`.
- **The proxy restricts what it may play** (ADR 0009 §10: a browser-sent id is never used):
  - `audition` takes `{piece_index, position_ms}`. The server takes the track **from the class's current
    draft** by that index, rebuilds `spotify:track:<id>` from a validated id (`^[0-9A-Za-z]{22}$`), and refuses
    `position_ms` outside `[0, track length)`.
  - `device_id` is taken only from the server's own `GET …/devices` result, never from the browser.
  - A class with no current draft or a piece index outside it is refused. The endpoints cannot play anything
    else, so they are not a general remote control (D7).
  - S2's acceptance criteria and tests (#1395) include each of these refusals.
- **Egress.** The allowlists are per host today (`SPOTIFY_ALLOWLIST`, `WEB_URLLIB_HOSTS`) and nothing
  restricts a path, so S2 adds a **path guard** (like `test_swedebeat_gateway_guard.py`) for exactly those
  methods and paths. It states the client (the existing Spotify async client) and the **`spotify_call` mode in
  the web process** for these writes: no `record_result`; a token refresh runs under the row lock only inside the
  expiry margin, never per poll; a 401 flips `needs_reconnect` only after one refresh and a retry, as ADR 0009
  does. This amends ADR 0009 §10. The token never leaves the
  backend.

> **Built in S2 (#1395, 2026-11-21).** `classbeat.classes.preview_spotify` and its routes; the path guard,
> client and `spotify_call` mode are recorded in ADR 0009 §10's amendment of that date. Choices made there:
> - `piece_index` is 0-based in play order. The tracks and lengths come from the draft's approved
>   `song_list` (`music.song_list_id`, its hash and fingerprint re-checked): a draft without one (a class
>   designed directly on a MOWL link, which stores no Spotify id) is `no_draft`.
> - The device is the active one of the devices list, else the first; a restricted device or an id of an
>   unexpected shape is skipped. Device ids never reach the browser (names and types only).
> - `pause` reads the state first and is sent only while a song of this class plays.
> - `GET …/state` answers `idle` (nothing plays), `external` (another item; its id is not passed on),
>   `paused` or `playing`, with the piece indexes of the playing track; `no_device` comes from the devices
>   list and from an audition. Refusals: 404 `not_found`; 409 `not_connected`, `needs_reconnect`,
>   `preview_not_granted` (with `action: enable_listening_preview`), `no_draft`, `no_device`,
>   `premium_or_allowlist` (Spotify's 403); 422 `piece_out_of_range`, `position_out_of_range`; 429
>   `rate_limited` with `Retry-After`; 503 `upstream_unavailable`.

**7.3 State and drift.**

| Observed | State |
|---|---|
| No devices | `no_device` ("Open Spotify on a device") |
| `is_playing=false` | `paused` |
| Another item | `external` |
| A new device | still playing; a note is shown |
| 401 | reconnect |
| 403 | Premium or the development-mode allowlist |
| 429 | back off by `Retry-After` |

- **Polling.** About 1 s, only while `starting` or `playing`, stopped when the page is hidden.
- **Drift.** On each poll the expected position is `last + (now − observedAt)`. A difference over 1500 ms
  re-anchors and notes a "seek detected".
- `uncertaintyMs` is half the round trip plus the poll age. The proxy returns its own timing so the client can
  subtract it.
- **No accuracy is claimed.** A fake with injected latency, jitter and reordering tests the state machine. The
  owner's live measurement records the start error and the drift (20 auditions of a known downbeat, 60 s each)
  here, and the nudge steps (default ±0.1, 0.5, 1, 5 and 10 s) follow from it.

**7.4 Alignment correction.**
- `spotify_offset_corrections` is **class-owned**, as `beat_one_corrections` is (ADR 0007 amendment, migration
  0029): `owner_id`, an owner-inclusive `class_id` foreign key `ON DELETE CASCADE`, the music `fingerprint`,
  the 1-based `song` (play order), `offset_ms`, `set_against_analysis`, the times; RLS `owner_isolation`;
  unique per `(owner_id, class_id, fingerprint, song)`; at most 200 rows for the current fingerprint. **No
  Spotify id and no title.** A correction therefore never reaches another class of that user on the same
  track, the rule beat-one's design set.
- "Listen again" when the analysis changes uses `set_against_analysis` (the analysis `map_version` and the
  analysed file's sha256 when set): if either differs, the correction is shown but not applied until the user
  confirms it again.
- **A correction reaches the class only through a refit** that stores a **new `draft` revision**, as beat-one
  does. An approved class, its approval binding and its saved programs never change underneath the user.
  Until a refit, the correction only moves the audition cursor in the editor.
- It never edits spotdown's `offset_s`.
- Spotify songs still play whole: the owner default; ADR 0021 is unchanged.

### 8. Relation to ADR 0036 amendment B

Amendment B (#1366; its header still reads Proposed in ADR 0036 and is aligned in a later docs PR) was written
in parallel with this ADR. This ADR depends on it and **amends three of its items and A1**, listed first; the
rest is unchanged.

**Amended.**
- **B4 (the proposal).** `user_edit` is added to the origins that are **not** a proposal, beside `mix_choices`.
  The proposal B4 resolves is the nearest `design_draft` or `revise_draft` draft **past any `user_edit`
  drafts**. The overlay of the draft the job starts from is carried: `apply_mix_choices` remaps, fits, then
  re-applies the overlay by §3's order (so a user's pins and workout edits survive **Apply these choices**), and
  the new draft stores the carried overlay. Its lineage names the draft it was applied on.
- **B9 (`applied_form`).** A draft's stored spec is `applied_form` when it equals
  `with_pins(mix_choice_policy.applied(proposal, facts, choices), overlay pins)`, run through
  `mix_plan_policy.checked` and the class-length rule (B6, §3's order), so the pinned spec is held to the same
  gates. The **choices** a `user_edit` draft is checked with are those of its **nearest ancestor that is not a
  `user_edit`** (a `mix_choices` or proposal draft), so the fail-closed default does not refuse it. A `user_edit`
  draft is therefore admitted when it differs from `applied(...)` only by its overlay. A draft that differs by anything else still fails closed: `mix_not_applied`. `pin_unconfirmed`
  (§5) comes immediately after it in the order.
- **B10 and A1 (the hash and the spec shape).** The spec shape gains optional `start_pin` and `end_pin`
  (§5); the choices stay out of the spec. The hash of a draft with an overlay covers the overlay (§3), which
  is also what binds the pins to the approval. A draft without an overlay hashes as today.

**Unchanged.**
- **B3** leaves the audio preview to a separate owner design: this ADR.
- **B4a**'s `mix_remap_calc` is the song-anchored remap that §3 uses when an audio edit moves the workout.
  Overrides, locks and anchors reconcile after it (§3).
- **Item 2** establishes that the fit keeps the workout's total (§3, class duration).
- **B8**'s bass-swap setting applies to preview windows exactly as to outputs (§6.1).
- **B12** makes the mix spec and the write path security-critical, so the pin PR (E1) has an **Opus Developer
  and an Opus Reviewer**. It depends on #1367 (`mix_choice_policy`).
- The amendments above touch the approval path, so E2 (the user-edit write path, `base_revision_id` on approve,
  the overlay hash) is security-critical too: **Opus Developer and Opus Reviewer**, as approvals are in the
  review gate. They also need B9's recomputation extended in #1370's code, which E1 and E2 do in their PRs.

## Alternatives

- **Spotify Web Playback SDK** (the browser as a Spotify device). Rejected:
  - it needs the token in the browser and the `streaming` scope;
  - it makes classbeat a player.
- **A rendered full-mix preview master per draft**, with byte-range seeking. Rejected: a full render after every
  audio edit.
- **A spoken watermark** mixed into previews. Designed in revisions 3 and 4 of the plan, then replaced by the
  owner (2026-11-21) with the 15-minute session as the simpler deterrent.
- **Gapless joins through Web Audio or Media Source Extensions.** Rejected for the first release: a second audio
  engine, and MP3 encoder delay and padding make it fragile.
- **Localized sub-plan clip keys.** Deferred. Full invalidation is correct and cheap enough.
- **Server-measured listening time.** Rejected: the server cannot observe audibility.
- **A Spotify trim map, or MOWL starting Spotify songs mid-track.** Not allowed (ADR 0021; owner default).

## Consequences

**New owned tables, each with RLS (ADR 0002) and in deletion and export (ADR 0014):**
- `preview_sessions`;
- `preview_sources` (the per-owner source-track cache);
- `preview_clips`;
- `spotify_offset_corrections` (class-owned).

Plus a column `class_revisions.edit_overlay` and a column for the granted Spotify scopes.

**Other new pieces:**
- a private preview volume;
- a `preview_render` job kind, outside the class's one-open-job index;
- two optional Spotify scopes;
- a path guard for the Spotify playback paths.

**What changes in existing code:**
- The mix spec gains pins. `mix_plan_policy` skips candidate selection for pinned cuts, and every hard rule
  is kept.
- `render_policy` gains `window`, which is refused on output plans. The published render is unchanged.

**Security-critical work** (AGENTS.md):
- the pins (mix spec, approved external write content): E1, Opus and Opus;
- the user-edit write path, the approve guard and the overlay hash (approvals): E2, Opus and Opus;
- the clip service and its isolation: R2, Opus and Opus;
- the Spotify scopes and proxy (Spotify OAuth, egress): S1 and S2, Opus and Opus;
- the sandboxed child: R1, Opus review.

**Limitations, documented:**
- short gaps at window joins;
- approximate Spotify sync, with no crossfades;
- the SwedeBeat playlist output's preview is approximate;
- the 15-minute limit is a deterrent only.

**Verifications inside the implementing PRs:**
- the render-stage audit (§6.1);
- the MP3 join measurement (§6.4);
- the Spotify live measurement (§7.3), which is the owner's.

## Implementation notes (E2, #1391)

Decisions E2 took within this ADR, each safe and reversible. The two deviations have follow-up issues.

1. **Spotify drafts are not editable yet (a deviation from §4).** `POST /classes/{id}/edits` answers
   `409 edit_unavailable` for a class that is not a studio class. The check and fit a Spotify draft needs
   come from its composed map, which is built from MOWL's analyses, and E2 has no read path for them. The
   editor is offered for studio drafts only. A follow-up issue adds Spotify drafts.
2. **The jobs API's `stale_revision` carries no current id.** A Save, Update or outputs approval with a stale
   `base_revision_id` is refused `409 stale_revision` with the reason only: a job refusal has no payload. The
   page reloads the class to get its current revision. The edit and `POST /current` refusals do carry
   `current_revision_id`.
3. **Pins are not re-applied on a studio revise (a deliberate deferral of §5's "Pins and regeneration"). Superseded: #1423 (PR #1440, 2026-11-22) re-applies them; the text below is the E2 state.** A
   revise re-applies the overlay's locks, overrides and anchors to the model's workout and keeps its pin
   records as they are. It does not apply them to the model's new spec. The new draft's spec is then not the
   applied form of its choices, so the draft fails closed: an audio edit and a SwedeBeat output (the mix or the
   SwedeBeat playlist) answer `mix_not_applied` until the user undoes the revise or uses **Apply these
   choices**, which applies the pins (§8). A Spotify playlist alone plays whole songs, so pins do not apply to
   it. No mix is rendered or published without its pins. A follow-up issue re-applies the pins in the revise.
4. **Which workout is stored.** A workout edit (drag, split, merge, move, set, duration) stores the
   instructor's own timing, validated by `agent.design.check` but not replaced by its fitted spec, so a drop
   lands where the user put it. An audio edit (a pin) and **Apply these choices** store the fitted workout,
   because the mix under it changed.
5. **The base ids are optional on the API.** `base_revision_id` (Save, Update, the outputs approval) and
   `expected_current_id` (`POST /current`) may be left out, so a client from before E2 keeps working, and the
   check runs only when they are sent. E3's client must always send them.

## Implementation notes (R3, #1397)

2026-11-22. The browser side of the audition (the rendered-clip transport) plays R2's clips in `audio`
elements, and the app's CSP had `media-src 'none'`, which a browser enforces on every `audio` load. So the
CSP changes by exactly one directive: `media-src 'self'` (`classbeat.headers`; `CSP_EXPECTED` in
`tests/test_web.py` pins the whole policy, so any other change, or a wider `media-src` such as `blob:`,
`data:` or a host, fails that test). It is the only change to the CSP. Why it is safe: the clips are served
by our own `GET /api/classes/{id}/preview/clip`, which is same-origin, authorized per session and per owner,
and sent `Cache-Control: private, no-store` (section 6.5); no third-party media is loaded. The change makes
no claim that the audio cannot be recorded or saved: section 6.5 already states the preview is not DRM, and
the 15-minute limit is the browser's courtesy, not a protection.

## Implementation notes (#1423, pins on a studio revise and undo)

Written 2026-11-22. Supersedes E2 note 3.

1. **A studio revise applies the overlay's pins to the model's spec** (`studio/mix_revise_pin_policy.py`,
   `swedebeat_studio/revise_pins.py`), under the choices B9 implies for the DJ mix (`dj_defaults`): a song that
   is whole under those choices gets no pin. The pinned spec goes through the same checks as Apply (`checked`,
   the class-length rule, plan equality) before the draft is stored, and the model's workout is moved onto it
   with B4a's remap. A confirmed pin never moves.
2. **A pin the model's spec cannot hold is a correction round**, like `overlay_violated`; if it never fits the
   job ends `invalid_output` with nothing stored and the base still current.
3. **Clearing a pin after a revise** leaves the cut where the pin put it, as an ordinary cut.
4. **Undo and redo** on the overlay view now also apply to a `revise_draft` revision, through the same
   `service.pick` (class lock, `expected_current_id`, no open job).
5. **Resolved (#1452):** B9 re-derives a stored revise draft and re-places a pinned cut without its pin
   first; in a borderline case that used to answer `mix_not_applied` (it fails closed). `pinned_mix` now
   ends with `mix_revise_pin_policy.held`, B9's `holds` on the pinned spec as the revise draft, so a pin
   the produced mix does not hold is a correction round (`invalid_mix`, `pin_not_held`) and, if never
   held, `invalid_output` with nothing stored.

## Implementation notes (#1424)

2026-11-22. A Spotify draft is edited through the same path as a studio draft (`user_edit.apply`), with
the studio parts taken out.

- **Where the map comes from.** The worker stores the corrected map the draft was fitted to in the
  revision's `music.edit_map` (`design.revision.content_and_music`), capped at 256 KiB
  (`design/revision_edit_map.py`; a larger map is not stored and its draft stays read-only, with
  `music.edit_map_dropped = "map_too_large"` so the editor's read answers `map_too_large` and the page
  says why, #1453; an edit still answers `edit_unavailable`). The web
  process reads it from the revision row, so the edit makes no MOWL read and no new egress. A
  `user_edit` revision stores no map and reads the nearest earlier non-`user_edit` revision's, only
  while that revision's fingerprint matches. A draft with no stored map (made before this change), or
  a map that does not have the shape `agent.design.check` reads (`spotify_edit_state.readable`),
  answers `edit_unavailable` and stays read-only; the page still shows the audition panel.
- **What the map holds.** Timing only: the version, the length and crossfade, the songs' sections, the
  events, and per track its position, length, tempo and timing class. It holds **no Spotify id, no
  title and no artist** (ADR 0006 section 6, ADR 0007): `stored` removes them and `restored` gives each
  track neutral stand-ins when the edit reads it back, because `check` reads no identity. It lives in
  `class_revisions.music`, so it is deleted with the class and exported with the account like the rest of
  that column, and holds nothing that column did not already hold in kind.
- **The edit.** It runs the shared `studio_edit_ops.edited` against an `EditState` with a fixed map and a
  duration cap (the song that ends last, so a class cannot be made longer than its music), so one
  hash by the NULL overlay, the `base_revision_id` check in the transaction, locks and
  `locked_block_conflict` hold unchanged. Pins and anchors are refused `audio_edit_unavailable`: a
  Spotify draft has no mix to cut.
- **Section 3 ("On AI regeneration") on a Spotify draft (resolved by #1451).** #1424 first refused an
  AI revise, a song revise and a refit of an edited Spotify draft `has_user_edits`. They now carry the
  overlay as a studio revise does (`design/spotify_overlay_calc.py`, sharing
  `swedebeat_studio/revise_overlay.kept_on`): the base's hash is checked with its overlay
  (`design/revision_overlay.py`, `internal` for a tampered overlay or one that does not read); a revise
  and a song revise state the locks and overrides as fixed in the model's message and reconcile each
  submitted draft in section 3's fixed order (a lock breach is `overlay_violated`, a correction round;
  an override survives with no lock); a refit re-applies them to the new fit with no model, and a lock
  the new fit would move, or edits it cannot keep, end it `invalid_output` with nothing stored. The new
  revision stores the reconciled overlay under the one hash, and its own `music.edit_map`. A cut pin
  or an anchor on a Spotify draft (the edit never writes one) fails the run `internal`. The refusal and
  its message are gone, so a `user_edit` base whose edits were all cleared is revised like any other.
- **Duplicate.** A duplicate of an edited draft takes the nearest non-`user_edit` revision's
  provenance and the plain content hash with no overlay; the copy has no map, so its editor is
  read-only until the class is refitted.
- **Hardening (#1453).** An override's value is given in the revise message's fixed section as JSON
  (`revise_overlay.fixed_section`, shared by the studio and the Spotify revise), so a cue's quotes
  cannot end the statement's prose. The base reads and current-pointer swaps of a revise, song revise
  and refit (`refit`, `chat`, `revise_base`, `spotdown_base`, `revise_brief`, `revise_phase2`,
  `spotify_edit_draft`) are each pinned to their owner clause by `tests/test_base_reads_owner.py`,
  run with row security lifted, since `test_user_context_guard` does not read SQL. The class list and
  page carry `spotify` (the class's stored source is a Spotify playlist's) beside `soundless` and
  `studio`.
