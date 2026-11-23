# Plan: Class Preview & Editor (Revision 6, frozen)

**Status: FROZEN, 2026-11-21.**
- The owner approved this plan for freeze after five review rounds. It is the implementation baseline.
- The decision record is [ADR 0042](../adr/0042-class-preview-and-editor.md), Accepted by the owner. It gets
  its fresh Opus review in its PR. Where this plan and the ADR differ, the ADR wins.
- **Go given (owner, 2026-11-21):** the milestone CP (roadmap Amendment 35) and its issues #1389 to #1398.
  Implementation follows the review gate, starting after ADR 0042's review PASS.
- No deploy, live-account test, or real SwedeBeat, MOWL or Spotify call by any agent (AGENTS.md safety
  envelope). The legal and licensing assumption is ADR 0042 D11 and is not raised again.

## Goal

**What the editor is.** One Class Preview & Editor, placed before approval, where the instructor:
- auditions the music against the workout's zone blocks;
- edits the workout timeline: drag, move, split, merge, zones, locks, anchors, undo and redo;
- edits the audio timeline: cut pins and Spotify alignment;
- approves.

**The two audio backends:**
- SwedeBeat windows rendered by classbeat's own renderer, faithful to the published mix;
- remote control of the user's own Spotify, original tracks one at a time.

**What it must never become.** classbeat is not a gym player:
- a 15-minute continuous listening session;
- Play by gesture only;
- no class-performance mode.

## Issues (one PR each; opened 2026-11-21)

**Dependencies:**
- ADR 0 → R1, S1 (these start at once);
- ADR 0 + #1367 + #1370 → E1 (amendment B's `mix_choice_policy` and the B9 recomputation);
- E1 + #1369 + #1370 → E2 (E2 owns the server write of pins, §3 and §4);
- E1 + E2 → E3;
- R1 → R2 → R3 (and E3);
- S1 → S2 → S3 (and E3);
- **E1 needs #1367 and #1370; E2 needs E1, #1369 and #1370** (ADR 0036 amendment B).

| PR | Scope (ADR 0042 §) | Acceptance criteria | Focused tests | Dev / Reviewer |
|---|---|---|---|---|
| **0** #1389 | ADR 0042 (this freeze's PR) | Fresh review PASS | docs only | Architect / **Opus** |
| **E1** #1390 | Cut pins in `mix_spec_model`, `mix_plan_policy`, `mix_cut_policy` and `mix_hint_policy` (§5) | A pin is never replaced by an AI candidate; confirmed pins never move; `music_changed` blocks approval until each pin is re-confirmed; every hard rule still refuses, the loop rules included; pins unapplied on a whole song (amendment B) | table tests per mode × state × reanalysis × `music_changed`; each refusal reason with a pin present; pins after `mix_choice_policy` | **Opus / Opus** (mix spec, amendment B12) |
| **E2** #1391 | `workout_edit_policy` (drag, move, split, merge, set, duration); the overlay (overrides, locks, anchors); the user-edit revision route, including the **server write of cut pins** (set, confirm, re-confirm, clear, each run through §3's audio-edit order); `base_revision_id` and `expected_current_id` (§3, §4) | Drag, move, split and merge keep the total; `set` never changes the mix spec; Green→Yellow survives regeneration with no lock; a lock violation refuses; orphaned and unapplied records have no effect; stale base → 409 | property tests: the total is invariant; locked blocks unchanged; ≥ 50% reconciliation; Use AI value; edit during a job → conflict; undo after a branch clears redo; reconciliation after a B4a remap | Opus / Opus (Opus review of ADR 0042: approvals) |
| **E3** #1396 | Editor page: `timeModel.ts`, the `PreviewTransport` interface, `SilentTransport`, block, overlay and pin UI, provenance hover, undo and redo. Reuses `StudioTimeline`, `ProfileChart`, `SongStrip`, `BeatOnePanel` patterns (§2–§5) | Every E1 and E2 operation reachable by keyboard and mouse; the cursor drawn only from `observe()` | Vitest on the `timeModel` mappings (overlaps, lanes, provenance); e2e on fakes: drag, lock, undo, regenerate | Sonnet / Sonnet |
| **R1** #1392 | `window` in `render_policy` and `renderchild`; per-piece decode from `src_start` plus the stage audit; `preview_window_calc`; `RENDERER_VERSION`; the MP3 delay and padding measurement recorded in ADR 0042 (§6.1, §6.2, §6.4) | Clean-window PCM equals the full-render span (bit-exact expected, 1e-6); output plans refuse `window`; published sha256 unchanged; placement rules 1–5 | the 12 cases of §6.2; property tests: a selected transition always whole, no window under 60 s except at the mix end, ends extended (never shortened) out of guard zones, at most 6 min | Sonnet / **Opus** (sandboxed child) |
| **R2** #1394 | `preview_sessions`, the `preview_render` job, the clip endpoint, the cache key, `preview_clips` and the volume, purge and cap (§6.3–§6.5) | Owner-only clips; 30 min lifetime and 25 min delivery caps; no claim of audible-time verification; purge on idle, class and account deletion | another owner's session → 404; expired or over the delivery cap → 410; each key input changes the key; a workout-only edit is a hit; an audio edit is a miss; netguard and the fake gateway | **Opus / Opus** |
| **R3** #1397 | `RenderedClipTransport`: the monotonic playback counter, two-element continuation, auto-pause and its message, Play = new session, Pause ends it (§6.3, §6.4) | **The counter measures elapsed listening time on a monotonic clock (`performance.now()`), only between `playing` and a stop event (`pause`, `waiting`, `stalled`, `seeking`, `ended`, `emptied`), never `currentTime` differences**; auto-pause at 15:00 accumulated, with "Preview paused after 15 minutes. Press Play to continue."; no second audio engine | Vitest with a fake media element and a fake monotonic clock: **(1)** seeking forward and backward while playing adds only the elapsed playing time, never the jump; **(2)** switching windows carries the counter over and does not count the gap; **(3)** `waiting` and `stalled` stop the counter until `playing`; **(4)** Pause ends the session, and Play restarts at 0; **(5)** auto-pause exactly at 15:00 accumulated across several intervals, windows and seeks, with the message. e2e on the fake gateway: continuation across a window end | Sonnet / Sonnet |
| **S1** #1393 | Preview scopes, re-consent, `preview_granted` (§7.1) | Existing connections are unaffected | fake OAuth with and without the scopes | **Opus / Opus** |
| **S2** #1395 | Proxy endpoints and egress paths (§7.2) | Owner-scoped; no token in any response; 401, 403 (Premium or allowlist) and 429 mapped | the egress allowlist's exact paths; every row of the §7.3 table on the fake | **Opus / Opus** |
| **S3** #1398 | `SpotifyConnectTransport`: §2 states and lanes, drift and uncertainty, `spotify_offset_corrections` and its UI (§2, §7.3, §7.4) | The cursor only from observation; corrections kept apart from spotdown's facts | fake with latency, jitter and reordering; autoplay → `external`; `unusable` → `unaligned`; an intro → `outside_window`; overlap lane choice; device switch; no device; pause | Sonnet / Opus |

**Order.** E1 → E2 → R1 → E3 → R2 → R3, with S1 → S2 → S3 alongside when a PR slot is free.
- The running spotdown programme and review capacity set the pace (no fixed PR limit, roadmap Amendment 34).
- E1 also waits for #1367.

## Verification

- Every PR is tested on fakes only (netguard, the fake gateway, the fake Spotify). There are no live calls.
- Locally: lint, types and the targeted tests. CI runs the full suite.
- Live checks are the owner's:
  - L3 and L6 for the SwedeBeat playlist output;
  - the Spotify measurement of ADR 0042 §7.3;
  - a by-ear join check in Safari and Firefox.

## Notes at freeze (2026-11-21)

- **ADR 0036 amendment B** (#1366, Accepted; merged to `main` as #1375 while this plan was reviewed) is
  reconciled in ADR 0042 §8. Nothing in it changes this plan's architecture:
  - E1 gets an Opus Developer per B12 and depends on #1367;
  - pins are unapplied on whole songs;
  - E2 reuses B4a's `mix_remap_calc` for audio-driven workout moves;
  - B3's deferred audio preview is ADR 0042.
- **E2's workout-total verification is answered** by amendment B, item 2: the fit keeps the workout's total,
  bounded by the B6 class-length and plan gates. So the plan's fallback applies: an explicit duration change
  resizes the last unlocked block.
- **Still verified inside their PRs** (no gate, a defined fallback each):
  - the render-stage audit (R1);
  - the MP3 join measurement (R1, recorded only).

## Revision history

| Rev | Change |
|---|---|
| 1 | One editor, three transports; SwedeBeat clips, Spotify Connect; cut pins; a 15-minute cap |
| 2 | Findings 1–27: the transport contract, the Spotify state machine, measured accuracy, workout edit operations, locks, undo, windowed render, PCM tests, the cache key, pin states; the owner's licensing assumption D11 |
| 3 | Spotify overlap semantics, clean versus watermarked tests, a focus-aware watermark schedule, overrides versus locks versus anchors, provenance, concurrency, per-piece decode instead of pre-roll, end of window |
| 4 | The watermark replaced by a server-enforced 15-minute session |
| 5 | The session counted as actual playback in the browser, the server keeping authorization and caps only; reliable two-element joins (no gapless engine); full clip invalidation accepted; transition-preserving window placement; at freeze, R3's monotonic counter (owner correction) |
| 6 | The ADR review (PR #1399): amendments to B4, B9, B10 and A1; overlay column and hash; class-owned offset corrections; `preview_render` outside the class job slot; server-chosen Spotify track; E2 Opus / Opus; every audio edit runs B's one order of checks. No PR structure or dependency change |
