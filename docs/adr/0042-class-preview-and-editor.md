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
  - §4.2 and A7: the mix spec gains cut pins (§5 below).
  - Alternatives ("Rendering before approval to preview the exact mix. Rejected"): preview **windows** are
    rendered before approval. The output render is unchanged: once per merge attempt, after approval, from
    fresh downloads.
  - Consequences ("It never keeps it"): a preview cache, scoped per owner, may keep source tracks and clips
    (§6.5).
- [ADR 0016](0016-swedebeat-music.md): the same exception to "never keeps audio", for the preview cache only.
- [ADR 0007](0007-classes-revisions-and-saving.md): user-edit revisions, the edit overlay in the draft, and
  optimistic concurrency on revision writes and `POST /classes/{id}/current` (§4).
- [ADR 0009](0009-spotify-connection.md) §1: two optional preview scopes and `preview_granted` (§7).
- [ADR 0021](0021-use-part-of-a-song.md): **no change to its Spotify rule.**
  - Spotify songs still play whole, in MOWL and everywhere.
  - A Spotify "start position" in the editor is only an audition position.
  - The alignment correction (§7.4) is not a trim.

Related: [ADR 0002](0002-tenant-isolation.md) (new owned tables, RLS),
[ADR 0005](0005-jobs-and-leases.md) (the `preview_render` job), [ADR 0010](0010-live-activation-permissions.md),
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
- `mix_cut_policy.placed()` always replaces a proposed cut with a trusted candidate or the bar grid, so a
  manual cut would be moved silently today.
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
- `classMs → [(piece, sourceMs, gain)]` comes from the composed map (`timeline_calc.py`).
- A crossfade instant maps to both pieces, and both are audible in the rendered clip.
- `transportMs = classMs − clip.start_ms`.

**Spotify mapping.**
- The class was fitted with a crossfade constant `C` (today 9 s, `CROSSFADE_SEC`; #1348 investigates 9.54 s).
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

**Overrides, locks and anchors** live in the draft's **edit overlay**, which the draft revision stores. It is
covered by `content_hash`, and so by the approval.

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

- A committed edit (on drop or blur) is a new `draft` revision with provenance `origin: "user_edit"`, through
  the existing revision write. There is no model call.
- **Undo and redo** move along the chain with `POST /classes/{id}/current`. A new edit after an undo
  branches and clears the client's redo stack.
- **Optimistic concurrency.** Every write (edit, undo, redo, clear, approve) carries `base_revision_id`. The
  server checks it against the current revision **in the same transaction**; a mismatch is
  `409 stale_revision` with the current id.
  - The client reloads, keeps the unsent edit, and offers to reapply it.
  - `POST /current` gains `expected_current_id`, with the same 409.
- While the class's one open job runs (ADR 0038), edits get the existing job-running conflict.

### 5. Cut pins (the mix spec; amends ADR 0036 §4.2 and A7)

`MixTrack` (`studio/mix_spec_model.py`) gains optional `start_pin` and `end_pin`:

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

**A new sha256** (`music_changed`) puts every pin of that track into "listen again". Nothing moves, and
approval waits until each pin is re-confirmed or cleared (the beat-one pattern, migration 0029).

**The hard rules stay**, whether or not a cut is pinned:
- `cut_out_of_range`, `window_too_short`, `not_a_permutation`;
- every crossfade rule;
- `mix_too_long`;
- `loop_needs_exact_timing` and `loop_not_on_phrase`: owner default, **no exact-pin override of the loop
  rules in this milestone**.

A refused pin leaves the last valid revision, and the UI shows the reason.

**Pins and regeneration.** Pins behave as overrides of the cut: they are re-applied on regeneration. A
swapped-out song drops its pins with a notice.

**Pins and amendment B.**
- Pins exist only on a song that has a cut, under `dj_mix` with `trim`.
- A song made whole (`keep_whole`, or `trim` off) has no cut, so its pins become **unapplied**: listed, kept,
  without effect.
- `mix_choice_policy` derives the spec first; pins are then applied to the derived spec and checked by
  `checked` as above.

### 6. SwedeBeat audition

#### 6.1 Windowed render

- The render plan (`render_policy`) gains `window: {start, end}` in output samples. `validate_plan` refuses it
  on an output plan, so the output path cannot carry it.
- **Per-piece state, no pre-roll guess.** The child decodes every piece that overlaps the window **from that
  piece's own `src_start`**, exactly as the full render does: the same seek, the same soxr stream, the same
  curve state from the fade start. It only limits **writing** to the window.
- The implementing PR audits every stateful stage: decoder seek, resampler, fade gains, bass-swap filters,
  mixer buffer. If one carries state across pieces, the window decodes from the earliest piece whose state
  reaches it.
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

**The cache key** is `sha256` of a canonical JSON of:
- the full render plan's sha256;
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

- **Storage.** Source tracks are cached per owner (reference plus sha256). Clips go in `preview_clips`, owned
  with RLS; the metadata is in Postgres and the bytes on a private volume.
  - Both are purged after 14 idle days, with the class or the account (ADR 0014; the retention registry,
    ADR 0007 §11), and under a per-owner size cap.
  - Output renders still use fresh downloads (ADR 0036 §6, unchanged).
- **The job.** A low-priority `preview_render` job (ADR 0005) runs the existing sandboxed child. It uses the
  gateway's read-only download, with its existing caps, and makes no external write.
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

**7.2 Proxy.**
- These owner-scoped endpoints call `PUT /v1/me/player/play` (`uris`, `position_ms`), `PUT …/pause`,
  `GET /v1/me/player` and `GET /v1/me/player/devices`:
  - `POST /api/classes/{id}/preview/spotify/audition`;
  - `POST …/pause`;
  - `GET …/state`;
  - `GET …/devices`.
- The egress allowlist gains exactly those methods and paths. The token never leaves the backend.

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
- `spotify_offset_corrections(owner_id, spotify_track_id, offset_ms, set_against_analysis)` is owned, with RLS.
  It follows the `beat_one_corrections` pattern, including "listen again" when the analysis changes.
- It shifts the map `fit_to_music` uses, so MOWL timing improves too.
- It never edits spotdown's `offset_s`.
- Spotify songs still play whole: the owner default; ADR 0021 is unchanged.

### 8. Relation to ADR 0036 amendment B

Amendment B (Proposed, #1366) was written in parallel with this ADR. This ADR depends on it and changes none of
it:
- **B3** leaves the audio preview to a separate owner design: this ADR.
- **B4a**'s `mix_remap_calc` is the song-anchored remap that §3 uses when an audio edit moves the workout.
  Overrides, locks and anchors reconcile after it (§3).
- **Item 2** establishes that the fit keeps the workout's total (§3, class duration).
- **B8**'s bass-swap setting applies to preview windows exactly as to outputs (§6.1).
- **B12** makes the mix spec and the write path security-critical, so the pin PR (E1) has an **Opus Developer
  and an Opus Reviewer**. It depends on #1367 (`mix_choice_policy`).

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
- `preview_clips`;
- `spotify_offset_corrections`.

**Other new pieces:**
- a private preview volume;
- a `preview_render` job kind;
- two optional Spotify scopes;
- exact new egress paths.

**What changes in existing code:**
- The mix spec gains pins. `mix_plan_policy` skips candidate selection for pinned cuts, and every hard rule
  is kept.
- `render_policy` gains `window`, which is refused on output plans. The published render is unchanged.

**Security-critical work** (AGENTS.md):
- the pins (mix spec, approved external write content): E1, Opus and Opus;
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
