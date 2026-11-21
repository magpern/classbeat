# Plan: spotdown as a music-analysis service for classbeat (Revision 2, frozen)

**Status: FROZEN, 2026-11-20.** The owner approved this plan for freeze after three review rounds (Revision 1; Revision 2; Revision 2 with final corrections). It is the architectural baseline for the programme. **Every phase is authorized for implementation (owner, 2026-11-20, after the freeze)**, except the owner decisions #1297 to #1299 and magpern/spotdown#1 (#1296 was accepted on 2026-11-20, see the note below); no deploy, live-account test or real YouTube, SwedeBeat, MOWL or Spotify call by any agent. Roadmap [Amendment 31](../roadmap.md). The decision record will be ADR 0040 (#1300); where this plan and that ADR differ, the ADR wins once it is accepted, and the plan is fixed in the PR that changes it.

Companion repository: [magpern/spotdown](https://github.com/magpern/spotdown), [docs/integration-plan.md](https://github.com/magpern/spotdown/blob/main/docs/integration-plan.md). Tracking issue: #1308.

**Note 2026-11-20 (#1300):** ADR 0040 §5 supersedes two names in this plan: the lookups' query parameter is `cache_scope=` (was `scope=`) and the `rate_limited` field is `retry_after_s` (was `retry_after`). They are renamed below; no decision changed.

**Note 2026-11-20 (#1296, roadmap Amendment 32):** decision 1 of "Decisions that need the owner" is answered. The owner accepted the terms-of-service risk and deploys spotdown with YouTube switched on, and Spotify classes get their analysis through spotdown (the MOWL sections are the fallback, not the default). P6 is no longer gated by #1296; the default switch still waits for #1298. classbeat does not police how the analysis service sources audio. The retention of the global entries is settled in [ADR 0040](../adr/0040-spotdown-analysis-service.md), amendment "#1296 accepted". The issue table row for P6 below, which lists #1296 as its blocker, is superseded by this note (#1296 is answered; only the default switch, #1298, remains). The body below is frozen and unchanged.

## Issues

| Phase | Issue | Repo | Depends on | Owner blocker |
|---|---|---|---|---|
| planning | #1307 freeze this plan | classbeat | – | – |
| planning | [spotdown#12](https://github.com/magpern/spotdown/issues/12) link the plan | spotdown | – | – |
| P0 | #1300 ADR 0040 | classbeat | #1307 | – (its ADR 0010/0021 amendment takes effect on #1296) |
| P0 | [spotdown#2](https://github.com/magpern/spotdown/issues/2) governance bootstrap | spotdown | #1307 | then [spotdown#1](https://github.com/magpern/spotdown/issues/1) ruleset |
| P0 | [spotdown#3](https://github.com/magpern/spotdown/issues/3) contract and schema v1 | spotdown | spotdown#2, with #1300 | – |
| P1 | [spotdown#4](https://github.com/magpern/spotdown/issues/4) service mode, auth, upload, url, sandbox | spotdown | spotdown#2, #3 | merge after spotdown#1 |
| P1 | [spotdown#5](https://github.com/magpern/spotdown/issues/5) caches, lookups, provenance | spotdown | spotdown#4 | – |
| P2 | [spotdown#6](https://github.com/magpern/spotdown/issues/6) quality harness and baseline gates (bounded) | spotdown | spotdown#4, #5 | real-music gate runs: #1298 |
| P2+ | [spotdown#7](https://github.com/magpern/spotdown/issues/7) labels, [#8](https://github.com/magpern/spotdown/issues/8) phrases, [#9](https://github.com/magpern/spotdown/issues/9) downbeats (never blocking) | spotdown | spotdown#6 | – |
| P3 | [spotdown#10](https://github.com/magpern/spotdown/issues/10) YouTube matcher and source | spotdown | spotdown#4, #5 | live enablement: the owner's deployment setting (#1296 accepted 2026-11-20) |
| P3 | [spotdown#11](https://github.com/magpern/spotdown/issues/11) alignment layer | spotdown | spotdown#3, #5 | – |
| P4 | #1301 client, egress, fake, policies, shadow | classbeat | #1300, spotdown#3, #4, #5 | – |
| P5 | #1302 studio tracks via spotdown | classbeat | #1301, spotdown#6 | default switch: #1298 |
| P6 | #1303 Spotify flow via spotdown | classbeat | #1301, spotdown#6, #10, #11 | default switch: #1298 (#1296 accepted 2026-11-20) |
| P7 | #1304 mix via spotdown | classbeat | #1302, spotdown#6 | default switch: #1298 |
| P7 | #1305 ADR for `user_audio` | classbeat | #1300, #1302 | – |
| P7 | #1306 retirement review | classbeat | #1302, #1303, #1304 | **#1299** |

Owner decisions: #1296 (ToS risk, YouTube; **accepted 2026-11-20**: Spotify classes get their analysis through spotdown, MOWL sections only as the fallback; ADR 0040 amendment "#1296 accepted"), #1297 (cross-user reuse; blocks nothing), #1298 (real-music corpus), #1299 (retirement), [spotdown#1](https://github.com/magpern/spotdown/issues/1) (ruleset). GitHub milestones could not be created with the agents' tools; the phase is the `SD-Pn` prefix in each title (creating milestones is optional for the owner).

## Context

**Approved in principle:**
- classbeat owns Spotify user integration, music selection, workout generation and publishing.
- spotdown owns source matching, audio analysis and derived music intelligence.
- spotdown needs no per-user Spotify OAuth.
- SwedeBeat audio is preferred when the workout uses SwedeBeat.
- Existing classbeat analysis paths stay until the replacement's quality is shown.

**Revision 2** covers the eight review points:
1. a staged flow: match → select → analyse;
2. recording identity and timing classes;
3. a generic spotdown;
4. the analysis output and its confidence;
5. layered caches;
6. a failure taxonomy;
7. migration gated on quality;
8. proportionate scope.

### Facts the plan rests on (checked in code)

**spotdown (`magpern/spotdown`)**
- It is FastAPI with Redis/RQ and SQLite.
- Spotify use is metadata only, with Client Credentials (`app/spotify.py:_get_token`) and an oEmbed fallback. That means no user OAuth and no callback.
- Matching is a yt-dlp flat search: Topic channels first, then the closest duration (`app/sources.py:_best_match`).
- Analysis is librosa in the worker (`app/analyze.py:analyze_track`, analyzer v4). It gives BPM and candidates, beats, bars with an assumed 4/4, energy curves, drops, build-ups, segments with heuristic labels, and alignment to the Spotify clock.
- It has no upload, no auth, and CORS `*`. It serves and keeps audio under `/files`. The analyse endpoint looks Spotify up again for the duration.

**classbeat**
- Spotify classes use MOWL's sections for the pool playlist (`design/music.py` → `mowl.library.spotify_analysis`). This is MOWL's compute, not ours, and it is not stored.
- Studio tracks:
  - they are already analysed **only after the songs are selected**: a gateway download (≤60 MB), then `mowl.music.analyze_track` in the sandboxed child (`design/track_analysis.py`, `design/mixchild.py`);
  - results are cached per owner (`design/track_cache.py`).
- The SwedeBeat mix: a MOWL temporary link, then `design/mixfetch.py`, then `design/mixanalysis.py`, cached in `swedebeat_mix_maps`.
- The swap rules already exist (ADR 0036 §3):
  - a second match after a pause;
  - all missing counts as suspected outage, with no automatic swaps;
  - at most 3 swap rounds;
  - swaps are recorded and can be undone.
- `agent/revise.py` and the revision kinds of ADR 0007 already allow partial edits.
- ADR 0010 and ADR 0021 §2 currently forbid audio processing tied to Spotify tracks. ADR 0012 provides an eval suite.

**Consequence: spotdown needs no Spotify API in the integration.** classbeat sends the title, artists, duration and ISRC it already has. The Client Credentials client stays an optional adapter for spotdown's standalone UI. There is no exception to this boundary.

## 1. Flow: match → select → analyse → refine → design

| Step | Who | Cost | What happens |
|---|---|---|---|
| 1. Candidates | classbeat | model | `pick_pool` runs unchanged and gives a pool larger than the final list |
| 2. Cheap facts | classbeat and spotdown, in parallel | low | Spotify classes: today's MOWL pool sections (BPM, sections), unchanged. spotdown `POST /v2/match` for the whole pool, **no download**: per candidate, a status, recording confidence, duration check, up to 2 alternates, and a summary of any **already cached** analysis (free). Studio: the gateway `/catalog/match` as today. |
| 3. Select and replace | classbeat | model | `choose_songs` picks the final list from the available candidates. Unavailable picks are replaced from the ranked spares first, then by the song agent (≤3 rounds, ADR 0036 §3). No model call when a spare fits. |
| 4. Deep analysis | spotdown | high | Only the selected tracks: `POST /v2/analyses`. Cache hits are returned at once. |
| 5. Suitability refinement | classbeat (pure `track_fit_policy`) | low | Checks each analysed track: timing class (§2), usable BPM for the class plan, usable length, and the needed structure (for example, a warm-up track needs a low-energy start). An unsuitable track is replaced **alone**, from the spares first. Bounded: ≤2 refinement rounds and ≤⅓ of the list per round, then accept with a warning or fall back for that track. |
| 6. Design | classbeat | model | `design` runs unchanged, fed by a pure adapter. After approval, a track change goes through `revise` for that track's part, not a full regeneration. |

The whole pool is never analysed. The only speculative work is the free cache summary in step 2.

## 2. Recording identity and timeline correctness

spotdown computes three separate numbers. classbeat turns them into one **timing class** per track with a pure `timing_class_policy`. The backend enforces it; it is never left to the model.

**1. Recording-match confidence (0–1): is this the same recording?**
- **Positive signals:** ISRC equal (when the source exposes one); an auto-generated release (`- Topic`) or the official artist channel; normalised title and artist equal.
- **Negative signals:** version markers (live, remix, acoustic, sped up, slowed, nightcore, cover, karaoke, instrumental, extended, radio edit) present on one side only.
- **Duration** feeds in as below.
- The bands are tuned on the eval set: `confident` ≥ 0.85, `uncertain` from 0.6 to 0.85, below 0.6 not the same recording.

**2. Duration validation, twice**
- Before download, from search metadata: |Δ| ≤ 2 s ok, ≤ 5 s tolerable, > 5 s rejected.
- After decoding, on the measured content duration. Silence-trimmed and untrimmed hypotheses are both kept, as `analyze.py` does today.

**3. Alignment confidence: how well file time maps to the reference clock**
- From the residual after the offset hypothesis.
- Optionally, classbeat cross-checks against **MOWL's sections for the same Spotify track** when it has them. These anchors are structural only. They can raise confidence that a track is `approximate` or move it down to `unusable`. **They never make a track `exact`.** The check is pure and lives in classbeat, so spotdown stays generic.

**Timing classes**

| Class | When | What classbeat may do with it |
|---|---|---|
| `exact` | (a) the analysed file **is the played recording**: the same SwedeBeat reference that is published, the user's own uploaded file that is played, or the mix that is played; or (b) **verified recording equivalence**: a beat-level reference of the played audio is available (its own beat grid or audio fingerprint, not structure or metadata), and after the offset the beat grids agree with a median error ≤ 20 ms and ≥ 95 % of beats within ±50 ms over the whole track | beat-, bar- and drop-precise cues |
| `approximate` | **every other usable match, including every YouTube match for a Spotify track today**. Requires a confident or uncertain recording match, duration within tolerance, and a residual ≤ 2 s. A confident metadata match, an equal ISRC, a similar duration and agreeing sections are **not** proof of identical audio. | BPM, section-level energy and events with a window of ±2 s. Cues snap to section boundaries with a margin. No beat- or drop-precise instruction. Shown in the UI as "timing approximate". |
| `unusable` | not the same recording, residual > 2 s, or alignment failed | the analysis is discarded. Use the MOWL sections for that track (Spotify classes), or replace it. |

**Rules:**
- The design validator refuses any beat- or bar-precise instruction on a track that is not `exact`. That covers cue times, drop hits and countdowns.
- The model sees each track's class.
- The class and the evidence behind it are stored with the decision (§4a).

**Consequence:** for Spotify classes, spotdown first adds richer section-level energy, build, drop and breakdown information. Beat precision comes from SwedeBeat, the mix and user audio. Path (b) for Spotify needs a beat-level reference of the played audio, which classbeat does not have today; that is a later enhancement, not part of this plan's scope.

**A track that matched but failed later** (acquisition, decode, analysis or alignment) falls through, in order:
1. the next alternate from step 2;
2. the MOWL sections for that track;
3. replacement.

The per-kind rules are in §6.

## 3. spotdown stays generic

Its layers:

| Layer | Role | Required? |
|---|---|---|
| Core analyser | audio file in → analysis on the **file's own clock**. Knows no Spotify, no workouts, no users. | yes |
| Sources | `upload` (bytes pushed), `url` (https, host allowlist set by the operator), `youtube` (yt-dlp, off by default) | `upload` yes; the others optional |
| Matchers | metadata `{title, artists, duration_s, isrc?}` → ranked source candidates with confidences. YouTube first. | optional |
| Alignment | `{reference: {kind, id, duration_s}}` → offset, residual, confidence. "spotify" is just one reference kind. | optional |
| Standalone extras | web UI, Spotify URL resolution, file downloads | off in service mode |

- The schema is source-neutral: today's `start_spotify` and `drops_spotify` are replaced by an `alignment` object that the consumer applies.
- Callers pass an opaque `client_ref` and, for private sources, an opaque `cache_scope` (§5).
- Nothing classbeat-specific enters spotdown.

**API v2 (service mode):**
- `POST /v2/match`
- `PUT /v2/uploads`
- `POST /v2/analyses` (by `upload_id`, `source_ref` or `url`; optional `reference` for alignment)
- `GET /v2/jobs/{id}`
- `DELETE /v2/scopes/{scope}`

Two separate lookups, both limited to the caller's scope (§5):
- **By source reference:** `GET /v2/analyses/lookup?source=<kind>:<ref>&cache_scope=…` (for example `youtube:<video id>`). It returns the cached analysis id if there is one. Used for YouTube, where classbeat never has the bytes. `/v2/match` also returns this id for each candidate.
- **By content:** `GET /v2/analyses/lookup?sha256=…&cache_scope=…` when classbeat holds the bytes (SwedeBeat, the mix, an upload). It runs **before** any upload.

classbeat polls from its existing job steps: a checkpoint plus an extended lease, or the fenced hand-back with a backoff `available_at` (`jobs/transitions.py:hand_back`). No inbound endpoint is needed in classbeat.

## 4. Analysis output (schema v1)

Every feature carries `confidence` (0–1) and `method`:
- `measured`: signal-derived and checkable;
- `estimated`: model or algorithm inference on measured data;
- `heuristic`: a rule-based label.

classbeat's backend validations use only `measured` and `estimated` features above a threshold. `heuristic` labels reach the model as hints, marked as such.

| Group | Fields | Method |
|---|---|---|
| Provenance | analyzer name and version, component versions (librosa…), file sha256, decoded-audio fingerprint, source kind and ref, sample rate, decoded duration, created_at | – |
| Signal | duration, content start and end, silence regions, fade-in and fade-out, loudness and energy curves (full, percussive, low band) at 1 s | measured |
| Tempo | BPM, ½× and 2× candidates, tempo stability, tempo-change points | estimated (BPM confidence from the beat-tracker consistency) |
| Grid | beats, downbeats and bars (meter estimate, not assumed), phrases (8 and 16 bar groups) | estimated; downbeats and phrases with lower confidence |
| Structure | segment boundaries (novelty-based, a confidence for each), repetition groups | estimated |
| Events | build-ups, drops, breakdowns, transitions (hard stop, fade, tempo change), each with strength and confidence | estimated (energy-edge detection) |
| Labels | intro, verse, chorus, bridge, outro, breakdown, for each segment, with a label confidence | heuristic |
| Alignment (optional) | reference, offset, residual, alignment confidence, the recording-match confidence carried from the match | estimated |

**Baseline and enhancements:**
- **Baseline** (gated in §7, needed for the integration): tempo, beats, energy, silence and fades, structural boundaries, build-ups, drops and breakdowns, plus confidence on each.
- **Incremental enhancements** (never blockers): verse/chorus/bridge labels, phrases, better downbeats and meter.
  - Until an enhancement passes its own gate, the field is either absent or marked `heuristic` with a low confidence, and classbeat ignores it for validation.
  - Today's analyzer covers most of the baseline, but its downbeats assume 4/4.

Which analyser spotdown uses internally (librosa today, possibly a downbeat or structure model later) stays inside spotdown, behind `analyzer_version`.

## 4a. Provenance (kept after the audio is deleted)

The audio is deleted at the end of every job. The metadata kept with each analysis is enough to:
- reproduce a decision;
- invalidate caches;
- compare analyser versions;
- diagnose alignment problems.

**spotdown keeps, per analysis:**
- **Analyser:** name, `analyzer_version`, component versions (librosa, numpy, ffmpeg, yt-dlp), and a hash of the configuration and parameters.
- **Audio identity:**
  - file sha256 and the decoded-audio fingerprint;
  - codec, bitrate, sample rate and channels;
  - file duration, and content start, end and duration.
- **Source:**
  - its kind and reference (for example a YouTube video id; no file name for uploads);
  - for matched sources, the match record: the normalised query, the candidates with their scores and duration deltas, the chosen candidate, and `matcher_version`.
- **Alignment:**
  - the reference (kind, id, duration);
  - the hypotheses tried, the chosen offset, the residual and the confidence;
  - the anchor agreement, when it is provided;
  - `alignment_version`.
- **Run:** job id, timings, status and failure kind.

**classbeat keeps, per track in a class:**
- the analysis id;
- `analyzer_version` and the audio identity;
- the timing class with its reason and evidence (the numbers from §2);
- the versions of `timing_class_policy`, `track_fit_policy` and `spotdown_map_policy`;
- the refinement and replacement history (already stored as swaps).

This is kept under the per-owner retention of ADR 0007 §11.

**Never kept:** audio, user tokens, signed URLs, or file names of uploads. When the source still exists, the provenance is enough to re-run the analysis. When it does not, the record stands as the evidence.

## 5. Caching: four separate layers

| Layer | Where | Key | Lifetime | Sharing |
|---|---|---|---|---|
| Source match | spotdown | normalised metadata fingerprint (ISRC, or title/artist/duration bucket) + matcher version | days; misses shorter | global (public search results) |
| Source reference → analysis identity | spotdown | `(source kind, source ref, scope)` → audio identity, then the analysis. This is how YouTube analyses are found without bytes. | until the source changes or the analyser version changes | YouTube: global scope. Other sources: the caller's scope |
| Audio analysis | spotdown | **file sha256** + `analyzer_version`. The decoded-audio fingerprint is stored as an **extra equivalence signal**: it flags likely re-encodes, links duplicates and helps diagnosis, but **a cached analysis is reused only on an equal sha256, or through the source-reference link to the same audio identity, never on the fingerprint alone** | until the analyser version changes | YouTube: global. `upload`/`url`: limited to the caller's `cache_scope`; a lookup never crosses scopes |
| Alignment | spotdown | audio identity + reference (kind, id, duration) + alignment version | as above | as for the analysis |
| Workout data | classbeat | unchanged: `track_cache` (owner, reference, analyser version, sha256), `swedebeat_mix_maps`, the MOWL path in memory only | ADR 0007 §11 | per owner (ADR 0002) |

- classbeat sends an HMAC of `owner_id` as `cache_scope` for SwedeBeat, mix and user audio, so private-source analyses never cross users by default. Account deletion calls `DELETE /v2/scopes/{scope}`.
  *(Note, 2026-11-21, owner answer to #1297, #1338: a SwedeBeat **studio track** is analysed in one scope shared by every owner (the same catalog file for everyone), because spotdown requires a scope for uploads; mixes and user audio keep the owner's scope. ADR 0040, amendment "studio-track analyses are shared".)*
- **YouTube:** the match result already says whether an analysis is cached for each candidate, so a cached analysis is never downloaded or analysed again.
- **Sources classbeat holds the bytes for:** classbeat checks its own per-owner cache (by reference and sha256), then spotdown's lookup by sha256 within the scope, and only then uploads. Nothing is analysed or uploaded twice.

## 6. Failure handling

Per-track statuses from spotdown, and how classbeat reacts:

| Status | Meaning | Reaction (bounded) |
|---|---|---|
| `not_found` | search worked, no candidate | genuinely missing → spare, then replacement round |
| `uncertain` | best candidate below the confident band | not used for timing. Keep the song with MOWL sections (Spotify), or replace it if `exact` timing is needed for that slot. |
| `source_unavailable` | removed, region-blocked or age-gated | next alternate (≤2), then replacement |
| `provider_error` | bot challenge, 5xx, network | 1 retry with backoff. If ≥50 % of a batch (and ≥3 tracks) fail this way, it is a **provider outage**: no swaps; the class falls back to MOWL sections (Spotify), or the user gets "Try again later" or "Swap anyway" (studio, as today) |
| `rate_limited` | with `retry_after_s` | fenced requeue at `retry_after_s`; does not count as an attempt; the total wait is capped |
| `processing_failed` | decode error, timeout, out of memory | deterministic, so no retry on the same audio (except 1 retry after a timeout); then an alternate, then the MOWL sections, then replacement |
| `alignment_failed` | analysed, but the timeline is unusable | the timing class becomes `unusable`, as in §2 |
| spotdown unreachable | – | the whole class uses today's paths (MOWL sections, or the local studio and mix analysis). spotdown is never a hard dependency. |

*Note 2026-11-20 (#1303, #1325; ADR 0040, the amendment "the match stage of the Spotify flow"): for the
Spotify match stage, `rate_limited` is waited for **in the run** instead of a fenced requeue: each wait
is charged to the class's wait budget before it is made and made only when it fits, with checkpoints at
least every 10 s. That amendment supersedes the `rate_limited` row above for that stage; the other
stages are unchanged by it.*

*Note 2026-11-21 (#1303, #1329; ADR 0040, the amendment "the analysis and refinement stage of the
Spotify flow"): the same holds for the Spotify **analysis** stage (SD-P6 stage B). A `rate_limited`
start or job is waited for in the run, charged to the same per-class wait budget before the wait and
made only when it fits, instead of a fenced requeue (which would rerun `pick_pool` and
`choose_songs`). That amendment supersedes the `rate_limited` row above for that stage too; the studio
and mix stages are unchanged by it.*

*Note 2026-11-21 (#1303; ADR 0040, the stage A, B and D amendments): the "fenced requeue" above is
therefore not used by the Spotify flow. The Spotify stages (match, analysis) wait **in the run**, charged
to one 600 s wait budget per class, and when it is spent the class falls back to MOWL's sections (no
requeue). Phase 2 (stage D, the read of a ready analysis after the Approve) **never waits**: a
`rate_limited` read falls back per track to MOWL's sections at once. The fenced requeue stays for the
studio and mix paths.*

**Overall bounds per class:**
- match retry: 1;
- alternates per track: ≤2;
- replacement rounds: ≤3;
- refinement rounds: ≤2;
- spotdown wait budget: about 10 min, then fall back.

A failure in one track changes only that track. Before design that is a selection change. After design it goes through `revise` for that part.

## 7. Migration and quality verification

**Quality corpus:**
- **Committed fixtures:** synthetic tracks (click and structure generators with exact ground truth) and CC-licensed tracks, with human annotations (beats, downbeats, boundaries, drops, silence edges).
- **Owner-run corpus:** real indoor-cycling music (SwedeBeat library, typical class genres from 60 to 180 BPM, long intros, fades, tempo changes, about 30 tracks). It is analysed locally by the owner; **only the annotations and derived results are committed, never the audio**.

**Corpus sizes:**
- at least 20 synthetic tracks;
- at least 20 annotated CC tracks;
- **real workout music** (owner-run):
  - at least 30 Spotify-catalogue tracks matched through YouTube;
  - at least 20 SwedeBeat studio tracks;
  - at least 5 SwedeBeat mixes;
  - spread over typical class genres and 60–180 BPM.

Ground truth (beats, boundaries, drops, silence) is annotated on at least 15 of the real tracks. The rest are compared against the baseline only.

### Quality gates (initial; numbers live in a versioned `quality_gates` file in spotdown)

Metrics are mir_eval-style. "Baseline" means today's path for that source:
- MOWL sections for Spotify;
- `mowl.music.analyze_track` for studio tracks;
- `design/mixanalysis.py` for mixes.

"Tolerance" is the largest regression allowed against the baseline on any metric where both have a value.

**Gates for every source (signal level)**

| Metric | Initial threshold |
|---|---|
| BPM within ±2 % (octave-aware) | ≥ 95 % of tracks; exact octave ≥ 85 % |
| Beat F-measure (±70 ms) | ≥ 0.85 on four-on-the-floor; ≥ 0.75 overall |
| Boundary F (±3 s) | ≥ 0.60 (±1.5 s is reported only) |
| Silence and fade edges within ±0.25 s | ≥ 95 % |
| Drops (exact sources, ±1 beat) | precision ≥ 0.75, recall ≥ 0.65 |
| Confidence calibration (BPM, boundaries) | expected calibration error ≤ 0.10 |
| Robustness | ≥ 99 % of corpus jobs succeed; p95 ≤ 60 s for a 5-minute track on the box; peak memory ≤ 1.5 GiB |
| Downbeats, phrases, labels | reported only; gated only when an enhancement claims them (§4) |

**Per-source acceptance**

| Source | Gate against the baseline (tolerance) | Source-specific gates |
|---|---|---|
| **Spotify** (YouTube, `approximate`) | BPM agreement and boundary F ≥ MOWL − 0.02; quality-report warnings ≤ MOWL + 5 % | match precision on `confident` ≥ 95 % (correct recording); recall ≥ 80 %; wrong-version accepted ≤ 2 %; residual ≤ 2 s on ≥ 90 % of the `approximate` tracks; **false `exact` = 0** |
| **Studio tracks** (`exact`) | beat F ≥ baseline − 0.02; BPM ≥ baseline − 1 point; boundary F ≥ baseline − 0.03 | the signal gates above |
| **Mixes** (`exact`) | the song start and end within the mix ±2 s on ≥ 95 % (and ≥ baseline); BPM per song ±2 % on ≥ 95 % | stays within today's 180 s and 1.5 GiB limits |

**Workout-level gates** (ADR 0012 eval suite, design run on fixture briefs with each analyser):
- on `exact` tracks, ≥ 95 % of beat cues fall within ±80 ms;
- ≥ 80 % of intensity changes fall within ±1 bar (`exact`) or ±3 s (`approximate`) of an energy change;
- quality-report warnings are no more than the baseline + 5 %;
- in a blind review by the owner or an instructor, spotdown classes are preferred or tied in ≥ 60 % of at least 10 pairs.

**"Equivalent or better":** every gate passes, and no metric regresses beyond its tolerance.

**Changing a threshold:**
- needs evaluation evidence in the PR and the usual review;
- is recorded in ADR 0040.

Two gates are **hard and never relaxed**: false `exact` = 0, and no audio kept after a job.

**Rollout per source:**
1. shadow: spotdown runs alongside, and only the comparison metrics are recorded, without titles;
2. a setting switches the source on for an allowlist;
3. it becomes the default.

The existing implementations (MOWL sections, `mixanalysis`, `mixchild`, `track_analysis`) stay as fallbacks. They are removed only after the corpus and workout acceptance pass, a period of production use with no regression in the fallback rate, and the owner's sign-off. Schema compatibility or numeric parity alone is never enough.

## 8. Proportionate scope

- **Hosting:** spotdown runs as extra services in classbeat's compose (api, worker, redis) on the same box, with ADR 0026 stage 1 CPU and memory ceilings. No new hosting environment.
- **Auth:** one shared service key from a secrets file, on the internal compose network, with no published port. spotdown has no user accounts.
- **SwedeBeat and MOWL audio:** classbeat keeps its existing capped downloads and **streams the bytes** to `PUT /v2/uploads`. No signed-ticket feature is needed.
- **Sandboxing in spotdown:** the analysis runs in a subprocess with rlimits (memory, CPU time, decoded length) inside the container's ceiling. It does not copy classbeat's seccomp/Landlock machinery.
- **Audio:** in service mode, temporary audio is deleted at the end of each job and nothing is served.
- **classbeat side:** one new egress builder (`spotdown` origin setting, modelled on the player URL, `tests/egress_scan.py` updated), a fake spotdown for tests, and a contract test against spotdown's versioned schema file. This is the one Opus-reviewed security piece.
- **YouTube:**
  - optional, off by default, enabled per deployment by the owner;
  - documented: downloading from YouTube breaks YouTube's terms, and using it for Spotify-catalogue classes touches Spotify's Developer Policy. Returning only metadata and being noncommercial do **not** remove that risk; the risk is the owner's to accept;
  - licensed sources (SwedeBeat, user audio) always win when they exist;
  - agents and CI never call YouTube.

## 9. Governance (both repositories)

`magpern/spotdown` uses classbeat's workflow (AGENTS.md, CLAUDE.md) unchanged and is not given a weaker policy:

**Roles**
- Opus is the Architect and Orchestrator;
- Sonnet is the Developer;
- Haiku is the Explorer.

**Reviews**
- **Opus reviews every PR of this programme in both repositories.** This is the owner's instruction for this work. It is stricter than AGENTS.md's Sonnet review for non-security PRs, and it is recorded in roadmap Amendment 31.
- Each PR's first review is a fresh reviewer. Later rounds resume the same reviewer.

**Merge controls**
- a recorded `Agent review (<model>): PASS <exact head SHA>`;
- green required checks;
- merge only with `gh pr merge <n> --squash --match-head-commit <sha>`;
- never `--auto` or `--admin`, never an approving review.

**Work rules**
- Conventional Commits;
- one issue per PR;
- worktrees;
- tests never touch the network.

**spotdown bootstrap (part of P0):**
- an `AGENTS.md` and `CLAUDE.md` that point to these rules;
- CI (lint, types, tests) as the required `ci` check;
- a `pr-title` commitlint check;
- `.claude/agents` role definitions.

The ruleset on spotdown's `main` (PR required, required checks, squash only) is set by the **owner**, because agents never change rulesets.

## Milestones (revised)

| | Scope | Repo | Depends on |
|---|---|---|---|
| **P0** | ADR 0040 "spotdown analysis service": the boundary, the flow (§1), timing classes (§2), provenance (§4a), cache layers (§5), the failure taxonomy (§6), the quality gates (§7), the amendment to ADR 0010 and 0021 §2 (the owner's acceptance is needed before P6 only). Contract doc and schema v1 draft in spotdown. Status update. spotdown governance bootstrap (§9). | both | – |
| **P1** | spotdown generic core and service mode: layer split (core, sources, alignment), `upload` and `url` sources, v2 API with both lookups, service key, caps and subprocess, the cache layers with scopes, the provenance record, the confidence fields, delete-after-job, the standalone extras behind a flag. Tests with faked yt-dlp and fixture audio. | spotdown | P0 |
| **P2** | **Bounded:** the quality harness (annotated fixtures, the metrics runner, the `quality_gates` file, the owner-run corpus procedure) and the **baseline** features to their gates: tempo, beats, energy, silence, boundaries, events, confidences, calibration. | spotdown | P1 (parallel with P3 and P4) |
| **P2+** | Enhancements, each one PR with its own gate and **never a dependency**: verse/chorus/bridge labels, phrases, better downbeats and meter. | spotdown | P2 |
| **P3** | Matching and alignment adapters: YouTube matcher with recording confidence, two-stage duration validation, alternates, the source-reference cache, failure statuses and rate-limit handling; the alignment layer. | spotdown | P1 |
| **P4** | classbeat client: egress builder, settings, fake spotdown, pure `spotdown_map_policy` (to the existing track map and `MusicData`), `timing_class_policy` (strict `exact`), `track_fit_policy`, the provenance fields stored per track; **shadow mode** for studio tracks. | classbeat | P1 schema |
| **P5** | Studio tracks via spotdown (`exact` timing, bytes relayed, sha256 lookup before upload), the local path kept behind a setting. | classbeat | P2 studio gates, P4 |
| **P6** | Spotify flow: the match stage beside MOWL sections, selection with spares, analysis of the selected tracks, the refinement loop, the `approximate` rules enforced in the design validator, the failure taxonomy, MOWL fallback, UI labels. | classbeat | P3, P4, P2 Spotify gates, owner acceptance of the amendment |
| **P7** | Mix analysis via spotdown (bytes relayed, mix gates); a separate ADR for the `user_audio` music source (upload UX, quotas, retention, deletion); the **retirement review** of the local analysis (§7 gates, owner sign-off). | both | P5, P6, P2 mix gates |

## Decisions that need the owner
1. **The amendment to ADR 0010 and 0021 §2:** accept the documented terms-of-service risk of YouTube-derived analysis for Spotify-catalogue classes, and whether YouTube is enabled in production at all. This gates P6 only; P1–P5 need no YouTube.
2. **Cross-user reuse of analyses of private-source audio** (SwedeBeat, the MOWL mix, user audio): a licence or entitlement question (ADR 0026 (a)). The default is no; each analysis is scoped per owner.
3. **The quality corpus:** which real workout music the owner analyses locally, who annotates the ground truth, and who does the blind workout review.
4. **Retiring** the local and MOWL-based analysis paths once §7's evidence is in.
5. **The ruleset and required checks on spotdown's `main`** (§9). Only the owner can set them.

The following were decided by default, and the owner can override them:
- the same-box hosting;
- a single service key;
- relaying bytes instead of signed tickets;
- the initial quality thresholds (§7).

## Verification of the plan itself (when built)
- **spotdown:**
  - unit tests per layer;
  - no audio left after a job;
  - 401 without the key;
  - `/files` returns 404 in service mode;
  - `url` source SSRF tests;
  - results validate against schema v1;
  - the metrics runner on the committed fixtures in CI.
- **classbeat:**
  - table-driven tests for the timing-class, fit and map policies;
  - egress scan;
  - fake-spotdown job tests for every row in §6, including an outage and a fallback;
  - the design validator refuses precise cues on approximate tracks;
  - a YouTube track never becomes `exact` (a table test with a confident match, an equal ISRC and agreeing anchors);
  - a fingerprint-only match never reuses an analysis;
  - neither lookup crosses scopes;
  - the provenance fields are present on every stored analysis;
  - partial replacement leaves the other tracks unchanged;
  - e2e against the fake.
- **Quality:** the §7 corpus and the workout-level comparison before any default switch or removal.
