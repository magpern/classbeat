# ADR 0040: spotdown as classbeat's music-analysis service (#1300)

Status: Accepted. This ADR itself changes no code, setting, compose file or runtime behaviour.
**Every phase it describes (SD-P0 to SD-P7, and the SD-P2+ enhancements) was authorized by the owner
on 2026-11-20**, except the owner decisions #1296 to #1299 and magpern/spotdown#1; no deploy, live-account test or real YouTube, SwedeBeat, MOWL or Spotify call by any agent (roadmap
[Amendment 31](../roadmap.md)). Its amendment to ADR 0010 and ADR 0021 §2 (§12 below) took effect
on 2026-11-20, when the owner accepted #1296 (the amendment "#1296 accepted" at the end; roadmap
[Amendment 32](../roadmap.md)).

Source: the frozen plan [docs/plans/spotdown-integration.md](../plans/spotdown-integration.md)
(Revision 2, frozen 2026-11-20; tracking issue #1308). This ADR records its decisions without
changing any; section references "plan §n" point to it. Once this ADR is accepted it wins where the
two differ, and the plan is fixed in the PR that changes it. spotdown's side of the contract (the
contract document and the versioned schema v1 file) is [spotdown#3](https://github.com/magpern/spotdown/issues/3);
its own copy of the plan is
[spotdown docs/integration-plan.md](https://github.com/magpern/spotdown/blob/main/docs/integration-plan.md).

Amends (effective 2026-11-20, the owner's acceptance of #1296; §12 and the amendment "#1296 accepted"):
- [ADR 0021](0021-use-part-of-a-song.md) §2, the sentence that forbids audio processing tied to
  Spotify tracks.
- [ADR 0010](0010-live-activation-permissions.md): a new accepted-risk entry and table row for
  YouTube-derived analysis of Spotify-catalogue tracks.

Related: [ADR 0002](0002-tenant-isolation.md) (isolation), [ADR 0005](0005-jobs-and-leases.md)
(jobs, leases, fencing, the hand-back), [ADR 0006](0006-agent-runs.md) §9 (the worker's secrets and
outbound allowlist), [ADR 0007](0007-classes-revisions-and-saving.md) §11 (retention) and the
revision kinds, [ADR 0008](0008-song-picking.md) (pool and final lists),
[ADR 0012](0012-eval-suite-and-model-providers.md) (the eval suite),
[ADR 0016](0016-swedebeat-music.md) (the mix analysis), [ADR 0026](0026-scaling-music-analysis.md)
(scaling analysis; §13 below), [ADR 0034](0034-classbeat-player-connection.md) §2 (the player URL
setting the new egress builder is modelled on), [ADR 0035](0035-swedebeat-gateway.md) and
[ADR 0036](0036-swedebeat-studio.md) (the gateway, studio tracks, the swap rules of §3).

## Context

The owner approved in principle (plan, Context):
- classbeat owns Spotify user integration, music selection, workout generation and publishing.
- spotdown owns source matching, audio analysis and derived music intelligence.
- spotdown needs no per-user Spotify OAuth.
- SwedeBeat audio is preferred when the workout uses SwedeBeat.
- Existing classbeat analysis paths stay until the replacement's quality is shown.

**spotdown today** (`magpern/spotdown`, checked for the plan): FastAPI with Redis/RQ and SQLite.
Spotify use is metadata only, with Client Credentials (`app/spotify.py:_get_token`) and an oEmbed
fallback, so no user OAuth and no callback. Matching is a yt-dlp flat search, Topic channels first,
then the closest duration (`app/sources.py:_best_match`). Analysis is librosa in the worker
(`app/analyze.py:analyze_track`, analyzer v4): BPM and candidates, beats, bars with an assumed 4/4,
energy curves, drops, build-ups, segments with heuristic labels, and alignment to the Spotify clock.
It has no upload and no auth, CORS `*`, serves and keeps audio under `/files`, and its analyse
endpoint looks Spotify up again for the duration.

**classbeat today:**
- Spotify classes use MOWL's sections for the pool playlist (`design/music.py` →
  `mowl.library.spotify_analysis`). That is MOWL's compute, not ours, and it is not stored.
- Studio tracks are analysed only after the songs are selected: a gateway download (≤ 60 MB,
  `swedebeat_gateway.track_policy.DOWNLOAD_MAX`), then `mowl.music.analyze_track` in the sandboxed
  child (`design/track_analysis.py`, `design/mixchild.py`); results are cached per owner
  (`design/track_cache.py`, `swedebeat_track_maps`).
- The SwedeBeat mix: a MOWL temporary link, then `design/mixfetch.py`, then `design/mixanalysis.py`,
  cached in `swedebeat_mix_maps`.
- The swap rules exist (ADR 0036 §3): a second match after a pause; all missing is a suspected
  outage with no automatic swaps; at most 3 swap rounds; swaps recorded and undoable.
- `agent/revise.py` and the revision kinds of ADR 0007 already allow partial edits.
- ADR 0021 §2 forbids audio processing of Spotify content, citing ADR 0010 and the Spotify
  Developer Policy (§12). ADR 0012 provides an eval suite.

**Consequence: spotdown needs no Spotify API in the integration.** classbeat sends the title,
artists and duration it already has, and the ISRC when it has one. **classbeat reads no ISRC today**
(`isrc` appears nowhere in `backend/src`), so until a later change reads one, the ISRC signal of §3 and
the ISRC key of §7 are inactive for classbeat's requests. The Client Credentials client
stays an optional adapter for spotdown's standalone UI. There is no exception to this boundary.

## Decision

### 1. The boundary and the four concerns

classbeat keeps its four concerns: **Spotify user integration** (PKCE, tokens, search, playlists),
**music selection**, **workout generation** and **publishing**. spotdown owns source matching,
temporary audio processing and derived analysis metadata. spotdown never returns audio, never
receives a user token, a signed URL or a password, and needs no per-user Spotify OAuth. Today's
analysis paths (MOWL sections, `mixanalysis`, `mixchild`, `track_analysis`) stay as fallbacks until
§9's gates and the owner's sign-off (#1299). spotdown is never a hard dependency (§8).

### 2. Flow: match → select → analyse → refine → design (plan §1)

| Step | Who | Cost | What happens |
|---|---|---|---|
| 1. Candidates | classbeat | model | `pick_pool` runs unchanged and gives a pool larger than the final list |
| 2. Cheap facts | classbeat and spotdown, in parallel | low | Spotify classes: today's MOWL pool sections (BPM, sections), unchanged. spotdown `POST /v2/match` for the whole pool, **no download**: per candidate a status, recording confidence, duration check, up to 2 alternates, and a summary of any **already cached** analysis (free). Studio: the gateway `/catalog/match` as today |
| 3. Select and replace | classbeat | model | `choose_songs` picks the final list from the available candidates. Unavailable picks are replaced from the ranked spares first, then by the song agent (≤ 3 rounds, ADR 0036 §3). No model call when a spare fits |
| 4. Deep analysis | spotdown | high | Only the selected tracks: `POST /v2/analyses`. Cache hits are returned at once |
| 5. Suitability refinement | classbeat (pure `track_fit_policy`) | low | Checks each analysed track: timing class (§3), usable BPM for the class plan, usable length, and the needed structure (a warm-up track needs a low-energy start). An unsuitable track is replaced **alone**, from the spares first. Bounded: ≤ 2 refinement rounds and ≤ ⅓ of the list per round, then accept with a warning or fall back for that track |
| 6. Design | classbeat | model | `design` runs unchanged, fed by a pure adapter. After approval a track change goes through `revise` for that track's part, not a full regeneration |

The whole pool is never analysed. The only speculative work is the free cache summary in step 2.

### 3. Recording identity and timing classes (plan §2)

spotdown computes three separate numbers. classbeat turns them into one **timing class** per track
with a pure `timing_class_policy`. The backend enforces the class; it is never left to the model.

1. **Recording-match confidence (0–1): is this the same recording?** Positive signals: an equal
   ISRC (when the source exposes one); an auto-generated release (`- Topic`) or the official artist
   channel; equal normalised title and artist. Negative signals: version markers (live, remix,
   acoustic, sped up, slowed, nightcore, cover, karaoke, instrumental, extended, radio edit) on one
   side only. Duration feeds in as below. Bands, tuned on the eval set: `confident` ≥ 0.85,
   `uncertain` 0.6 to 0.85, below 0.6 not the same recording.
2. **Duration validation, twice.** Before download, from search metadata: |Δ| ≤ 2 s ok, ≤ 5 s
   tolerable, > 5 s rejected. After decoding, on the measured content duration, keeping both the
   silence-trimmed and the untrimmed hypotheses, as `analyze.py` does today.
3. **Alignment confidence: how well file time maps to the reference clock,** from the residual after
   the offset hypothesis. Optionally classbeat cross-checks against **MOWL's sections for the same
   Spotify track** when it has them. These anchors are structural only: they can raise confidence
   that a track is `approximate` or move it down to `unusable`, and **they never make a track
   `exact`**. The check is pure and lives in classbeat, so spotdown stays generic.

| Class | When | What classbeat may do with it |
|---|---|---|
| `exact` | (a) the analysed file **is the played recording**: the same SwedeBeat reference that is published, the user's own uploaded file that is played, or the mix that is played; or (b) **verified recording equivalence**: a beat-level reference of the played audio is available (its own beat grid or audio fingerprint, not structure or metadata), and after the offset the beat grids agree with a median error ≤ 20 ms and ≥ 95 % of beats within ±50 ms over the whole track | beat-, bar- and drop-precise cues |
| `approximate` | **every other usable match, including every YouTube match for a Spotify track today.** Requires a confident or uncertain recording match, duration within tolerance, and a residual ≤ 2 s. A confident metadata match, an equal ISRC, a similar duration and agreeing sections are **not** proof of identical audio | BPM, section-level energy and events with a window of ±2 s. Cues snap to section boundaries with a margin. No beat- or drop-precise instruction. Shown in the UI as "timing approximate" |
| `unusable` | not the same recording, residual > 2 s, or alignment failed | the analysis is discarded. Use the MOWL sections for that track (Spotify classes), or replace it |

**The strict `exact` rule:** only (a) or (b) above makes a track `exact`; nothing else does.

Rules:
- The design validator refuses any beat- or bar-precise instruction on a track that is not `exact`:
  cue times, drop hits and countdowns.
- The model sees each track's class.
- The class and the evidence behind it are stored with the decision (§6).

For Spotify classes spotdown first adds richer section-level energy, build, drop and breakdown
information. Beat precision comes from SwedeBeat, the mix and user audio. Path (b) for Spotify needs a
beat-level reference of the played audio, which classbeat does not have today; that is a later
enhancement, outside this ADR's scope.

A track that matched but failed later (acquisition, decode, analysis or alignment) falls through, in
order: the next alternate from step 2; the MOWL sections for that track; replacement. The per-kind
rules are in §8.

### 4. A generic spotdown and the v2 API (plan §3)

| Layer | Role | Required? |
|---|---|---|
| Core analyser | audio file in → analysis on the **file's own clock**. Knows no Spotify, no workouts, no users | yes |
| Sources | `upload` (bytes pushed), `url` (https, host allowlist set by the operator), `youtube` (yt-dlp, off by default) | `upload` yes; the others optional |
| Matchers | metadata `{title, artists, duration_s, isrc?}` → ranked source candidates with confidences. YouTube first | optional |
| Alignment | `{reference: {kind, id, duration_s}}` → offset, residual, confidence. "spotify" is just one reference kind | optional |
| Standalone extras | web UI, Spotify URL resolution, file downloads | off in service mode |

- The schema is source-neutral: today's `start_spotify` and `drops_spotify` are replaced by an
  `alignment` object that the consumer applies.
- Callers pass an opaque `client_ref` and, for private sources, an opaque `cache_scope` (§7).
- Nothing classbeat-specific enters spotdown.

**API v2 (service mode):** `POST /v2/match`; `PUT /v2/uploads`; `POST /v2/analyses` (by
`upload_id`, `source_ref` or `url`; optional `reference` for alignment); `GET /v2/jobs/{id}`;
`DELETE /v2/scopes/{scope}`.

**Two separate lookups,** both limited to the caller's scope (§7):
- **By source reference:** `GET /v2/analyses/lookup?source=<kind>:<ref>&cache_scope=…` (for example
  `youtube:<video id>`) returns the cached analysis id if there is one. Used for YouTube, where
  classbeat never has the bytes. `/v2/match` also returns this id for each candidate.
- **By content:** `GET /v2/analyses/lookup?sha256=…&cache_scope=…` when classbeat holds the bytes
  (SwedeBeat, the mix, an upload). It runs **before** any upload.

classbeat polls from its existing job steps: a checkpoint plus an extended lease, or the fenced
hand-back with a backoff `available_at` (`jobs/transitions.py:hand_back`, ADR 0005). No inbound
endpoint is needed in classbeat.

### 5. Analysis output: schema v1 (plan §4)

Every feature carries `confidence` (0–1) and `method`:
- `measured`: signal-derived and checkable;
- `estimated`: model or algorithm inference on measured data;
- `heuristic`: a rule-based label.

classbeat's backend validations use only `measured` and `estimated` features above a threshold.
`heuristic` labels reach the model as hints, marked as such.

| Group | Fields | Method |
|---|---|---|
| Provenance | analyzer name and version, component versions (librosa…), file sha256, decoded-audio fingerprint, source kind and ref, sample rate, decoded duration, created_at | – |
| Signal | duration, content start and end, silence regions, fade-in and fade-out, loudness and energy curves (full, percussive, low band) at 1 s | measured |
| Tempo | BPM, ½× and 2× candidates, tempo stability, tempo-change points | estimated (BPM confidence from the beat-tracker consistency) |
| Grid | beats, downbeats and bars (meter estimate, not assumed), phrases (8 and 16 bar groups) | estimated; downbeats and phrases with lower confidence |
| Structure | segment boundaries (novelty-based, a confidence for each), repetition groups | estimated |
| Events | build-ups, drops, breakdowns, transitions (hard stop, fade, tempo change), each with strength and confidence | estimated (energy-edge detection) |
| Labels | intro, verse, chorus, bridge, outro, breakdown, per segment, with a label confidence | heuristic |
| Alignment (optional) | reference, offset, residual, alignment confidence, the recording-match confidence carried from the match | estimated |

- **Baseline** (gated in §9, needed for the integration): tempo, beats, energy, silence and fades,
  structural boundaries, build-ups, drops and breakdowns, plus confidence on each.
- **Incremental enhancements** (never blockers): verse/chorus/bridge labels, phrases, better
  downbeats and meter. Until an enhancement passes its own gate, the field is absent or marked
  `heuristic` with a low confidence, and classbeat ignores it for validation. Today's analyser covers
  most of the baseline, but its downbeats assume 4/4.
- Which analyser spotdown uses internally (librosa today, possibly a downbeat or structure model
  later) stays inside spotdown, behind `analyzer_version`.

The schema file itself is versioned in spotdown (spotdown#3); classbeat runs a contract test against
it (§10).

**Contract details: spotdown's `docs/contract-v2.md` (spotdown#3, final at spotdown#15) is the source
of the exact shapes,** within this ADR's decisions; this ADR confirms its choices where it goes beyond
the plan. `retry_after_s` (seconds) for `rate_limited` (§8; renamed from the plan's `retry_after`; the plan now uses the new names).
`cache_scope` is the one name for the caller's scope, the lookups' query parameter included (renamed from the
plan's `scope=`). It is required for `upload` and `url` sources, for uploads and for the sha256 lookup,
and optional for YouTube-only requests; a missing one is `422 scope_required`. The match answer is
`{results: [...]}`, one entry per track; `/v2/match` returns the five statuses of §8 that apply to
matching plus `matched` (`processing_failed` and `alignment_failed` occur only on jobs), and the band
`not_same` below `uncertain` (below 0.6, §3). Each match candidate may carry a cached-analysis id and a
short summary of that cached analysis (plan §1, §3, §5); the exact field names are in
`docs/contract-v2.md`. Limits: at most 50 tracks per match, a 24 h
idempotency window, a 1 h upload lifetime. `alignment.offset_s` has the sign
`reference_time = file_time + offset_s`. Added: `GET /v2/analyses/{id}`, an `Idempotency-Key` header
(`409` when reused with another body), auth `Authorization: Bearer <service key>` (§10), and the error
envelope `{"error": {"code", "message"}}` with `422 source_disabled` for a source that is off. Jobs
(job-v1) are `queued`, `running`, `done` or `failed`, with an `outcome` and a `failure_kind` of
`decode_error`, `timeout`, `out_of_memory`, `analysis_error` or `alignment_failed`. A
`processing_failed` job has no analysis. An `alignment_failed` job is `done` with a cached analysis on
the file's own clock whose `provenance.alignment` result is failed; classbeat classes that track
`unusable` (§3). The alignment record carries its result and the reference. A lookup (lookup-v1)
answers 404 on a miss. `provenance.source.ref` follows its kind: none for `upload`, the 11-character
video id for `youtube`, and for `url` the https URL's host and path only (no userinfo, query or fragment), or
`sha256:…`. `DELETE /v2/scopes/{scope}` removes that scope's private analyses, jobs and run
records, and **not** the global YouTube entries (§7's known conflict). Any other detail is the
contract's and needs no amendment here.

### 6. Provenance, kept after the audio is deleted (plan §4a)

The audio is deleted at the end of every job. The metadata kept with each analysis is enough to
reproduce a decision, invalidate caches, compare analyser versions and diagnose alignment problems.

**spotdown keeps, per analysis:**
- **Analyser:** name, `analyzer_version`, component versions (librosa, numpy, ffmpeg, yt-dlp), and a
  hash of the configuration and parameters.
- **Audio identity:** file sha256 and the decoded-audio fingerprint; codec, bitrate, sample rate and
  channels; file duration, and content start, end and duration.
- **Source:** its kind and reference (for example a YouTube video id; no file name for uploads); for
  matched sources, the match record: the normalised query, the candidates with their scores and
  duration deltas, the chosen candidate, and `matcher_version`.
- **Alignment:** the reference (kind, id, duration); the hypotheses tried, the chosen offset, the
  residual and the confidence; the anchor agreement when provided; `alignment_version`.
- **Run:** job id, timings, status and failure kind.

**classbeat keeps, per track in a class:** the analysis id; `analyzer_version` and the audio
identity; the timing class with its reason and evidence (the numbers of §3); the versions of
`timing_class_policy`, `track_fit_policy` and `spotdown_map_policy`; the refinement and replacement
history (already stored as swaps). This is kept under the per-owner retention of ADR 0007 §11.

**Never kept:** audio, user tokens, signed URLs, or file names of uploads. When the source still
exists, the provenance is enough to re-run the analysis; when it does not, the record stands as the
evidence.

### 7. Caching: four layers in spotdown, plus classbeat's own (plan §5)

| Layer | Where | Key | Lifetime | Sharing |
|---|---|---|---|---|
| Source match | spotdown | normalised metadata fingerprint (ISRC, or title/artist/duration bucket) + matcher version | days; misses shorter | global (public search results) |
| Source reference → analysis identity | spotdown | `(source kind, source ref, scope)` → audio identity, then the analysis. This is how YouTube analyses are found without bytes | until the source changes or the analyser version changes | YouTube: global scope. Other sources: the caller's scope |
| Audio analysis | spotdown | **file sha256** + `analyzer_version`. The decoded-audio fingerprint is stored as an **extra equivalence signal**: it flags likely re-encodes, links duplicates and helps diagnosis, but **a cached analysis is reused only on an equal sha256, or through the source-reference link to the same audio identity, never on the fingerprint alone** | until the analyser version changes | YouTube: global. `upload`/`url`: limited to the caller's `cache_scope`; a lookup never crosses scopes |
| Alignment | spotdown | audio identity + reference (kind, id, duration) + alignment version | as above | as for the analysis |
| Workout data | classbeat | unchanged: `track_cache` (owner, reference, analyser version, with the sha256), `swedebeat_mix_maps`, the MOWL path in memory only | ADR 0007 §11 | per owner (ADR 0002) |

- classbeat sends an HMAC of `owner_id` as `cache_scope` for SwedeBeat, mix and user audio, so
  private-source analyses never cross users by default (cross-user reuse is the owner's decision,
  #1297; the default is no). Account deletion calls `DELETE /v2/scopes/{scope}`.
- **YouTube:** the match result already says whether an analysis is cached for each candidate, so a
  cached analysis is never downloaded or analysed again.
- **Sources classbeat holds the bytes for:** classbeat checks its own per-owner cache (by reference
  and sha256), then spotdown's lookup by sha256 within the scope, and only then uploads. Nothing is
  analysed or uploaded twice.

**Known conflict with ADR 0010 and ADR 0007 §11 (not resolved by this ADR).** The global spotdown
entries for Spotify-catalogue tracks are derived from Spotify metadata and are not tied to any class:
the source-match cache (keyed on the ISRC or title/artist, kept for days), the YouTube source-reference
link and the YouTube analysis (kept until the source or the analyser version changes), and the match
record with its normalised title and artist query that §6 keeps with each analysis. `DELETE
/v2/scopes/{scope}` removes only scoped entries, never global ones. This contradicts two accepted
statements:
- ADR 0010's accepted risk, "a shared music analysis is deleted once no class of any user refers to
  it, so nothing Spotify-derived is kept indefinitely", and its table row "Keeping track lists, timing
  and shared analyses for saved classes: allowed while a class refers to them, then deleted";
- ADR 0007 §11, which requires any shared analysis store to come with its own ADR, the analysis id
  recorded on each owned row, and a purge driven by class and job references.

This retention is **part of what the owner accepts in #1296** (§12 item 2). The owner accepted it
on 2026-11-20 and the hold is lifted; YouTube sourcing is the owner's deployment choice (§12), so
such entries exist only where the owner enabled it. The proposed resolution
point, for the owner and the Architect to choose and not decided here: either a bounded lifetime or
purge for the global entries in spotdown#5 (caches, lookups, provenance), or a follow-up ADR that
applies ADR 0007 §11's reference-driven purge to them, settled before SD-P6 (#1303) starts.
*(Settled 2026-11-20: the owner accepted #1296, and the Architect's default, a bounded lifetime of 90
days unused or until the version changes, with an operator takedown, is item 5 of the amendment
"#1296 accepted" at the end.)*

### 8. Failure taxonomy and its bounds (plan §6)

| Status | Meaning | Reaction (bounded) |
|---|---|---|
| `not_found` | search worked, no candidate | genuinely missing → spare, then a replacement round |
| `uncertain` | best candidate below the confident band | not used for timing. Keep the song with MOWL sections (Spotify), or replace it if `exact` timing is needed for that slot |
| `source_unavailable` | removed, region-blocked or age-gated | next alternate (≤ 2), then replacement |
| `provider_error` | bot challenge, 5xx, network | 1 retry with backoff. If ≥ 50 % of a batch (and ≥ 3 tracks) fail this way, it is a **provider outage**: no swaps; the class falls back to MOWL sections (Spotify), or the user gets "Try again later" or "Swap anyway" (studio, as today) |
| `rate_limited` | with `retry_after_s` | fenced requeue after `retry_after_s`; does not count as an attempt; the total wait is capped |
| `processing_failed` | decode error, timeout, out of memory | deterministic, so no retry on the same audio (except 1 retry after a timeout); then an alternate, then the MOWL sections, then replacement |
| `alignment_failed` | analysed, but the timeline is unusable | the timing class becomes `unusable` (§3) |
| spotdown unreachable | – | the whole class uses today's paths (MOWL sections, or the local studio and mix analysis). spotdown is never a hard dependency |

**Overall bounds per class:** match retry 1; alternates per track ≤ 2; replacement rounds ≤ 3;
refinement rounds ≤ 2; spotdown wait budget about 10 min, then fall back.

A failure in one track changes only that track. Before design that is a selection change; after
design it goes through `revise` for that part.

### 9. Quality gates and the rollout (plan §7)

**Quality corpus.** Committed fixtures: synthetic tracks (click and structure generators with exact
ground truth) and CC-licensed tracks, with human annotations (beats, downbeats, boundaries, drops,
silence edges). An owner-run corpus of real indoor-cycling music (SwedeBeat library, typical class
genres from 60 to 180 BPM, long intros, fades, tempo changes), analysed locally by the owner; **only
the annotations and derived results are committed, never the audio** (#1298).

**Corpus sizes:** at least 20 synthetic tracks; at least 20 annotated CC tracks; real workout music
(owner-run): at least 30 Spotify-catalogue tracks matched through YouTube, at least 20 SwedeBeat
studio tracks and at least 5 SwedeBeat mixes, spread over typical class genres and 60–180 BPM.
Ground truth (beats, boundaries, drops, silence) is annotated on at least 15 of the real tracks; the
rest are compared against the baseline only.

**Gates** (initial; the numbers live in a versioned `quality_gates` file in spotdown). Metrics are
mir_eval-style. "Baseline" is today's path for that source: MOWL sections for Spotify,
`mowl.music.analyze_track` for studio tracks, `design/mixanalysis.py` for mixes. "Tolerance" is the
largest regression allowed against the baseline on any metric where both have a value.

Every source (signal level):

| Metric | Initial threshold |
|---|---|
| BPM within ±2 % (octave-aware) | ≥ 95 % of tracks; exact octave ≥ 85 % |
| Beat F-measure (±70 ms) | ≥ 0.85 on four-on-the-floor; ≥ 0.75 overall |
| Boundary F (±3 s) | ≥ 0.60 (±1.5 s is reported only) |
| Silence and fade edges within ±0.25 s | ≥ 95 % |
| Drops (exact sources, ±1 beat) | precision ≥ 0.75, recall ≥ 0.65 |
| Confidence calibration (BPM, boundaries) | expected calibration error ≤ 0.10 |
| Robustness | ≥ 99 % of corpus jobs succeed; p95 ≤ 60 s for a 5-minute track on the box; peak memory ≤ 1.5 GiB |
| Downbeats, phrases, labels | reported only; gated only when an enhancement claims them (§5) |

Per source:

| Source | Gate against the baseline (tolerance) | Source-specific gates |
|---|---|---|
| **Spotify** (YouTube, `approximate`) | BPM agreement and boundary F ≥ MOWL − 0.02; quality-report warnings ≤ MOWL + 5 % | match precision on `confident` ≥ 95 % (correct recording); recall ≥ 80 %; wrong-version accepted ≤ 2 %; residual ≤ 2 s on ≥ 90 % of the `approximate` tracks; **false `exact` = 0** |
| **Studio tracks** (`exact`) | beat F ≥ baseline − 0.02; BPM ≥ baseline − 1 point; boundary F ≥ baseline − 0.03 | the signal gates above |
| **Mixes** (`exact`) | the song start and end within the mix ±2 s on ≥ 95 % (and ≥ baseline); BPM per song ±2 % on ≥ 95 % | stays within today's 180 s and 1.5 GiB limits |

Workout level (ADR 0012 eval suite, design run on fixture briefs with each analyser): on `exact`
tracks ≥ 95 % of beat cues fall within ±80 ms; ≥ 80 % of intensity changes fall within ±1 bar
(`exact`) or ±3 s (`approximate`) of an energy change; quality-report warnings no more than the
baseline + 5 %; in a blind review by the owner or an instructor, spotdown classes are preferred or
tied in ≥ 60 % of at least 10 pairs.

**"Equivalent or better":** every gate passes, and no metric regresses beyond its tolerance.
**Changing a threshold** needs evaluation evidence in the PR and the usual review, and is recorded in
this ADR (as a dated amendment). **Two gates are hard and never relaxed:** false `exact` = 0, and no
audio kept after a job.

**Rollout, per source:** (1) shadow: spotdown runs alongside and only the comparison metrics are
recorded, without titles; (2) a setting switches the source on for an allowlist; (3) it becomes the
default (#1298). The existing implementations (MOWL sections, `mixanalysis`, `mixchild`,
`track_analysis`) stay as fallbacks. They are removed only after the corpus and workout acceptance
pass, a period of production use with no regression in the fallback rate, and the owner's sign-off
(#1299). Schema compatibility or numeric parity alone is never enough.

### 10. Proportionate scope (plan §8)

- **Hosting:** spotdown runs as extra services in classbeat's compose (api, worker, redis) on the
  same box, with ADR 0026 stage 1 CPU and memory ceilings. No new hosting environment.
- **Auth:** one shared service key from a secrets file, on the internal compose network, with no
  published port. spotdown has no user accounts.
- **SwedeBeat and MOWL audio:** classbeat keeps its existing capped downloads and **streams the
  bytes** to `PUT /v2/uploads`. No signed-ticket feature; a MOWL temporary link never leaves
  classbeat.
- **Sandboxing in spotdown:** the analysis runs in a subprocess with rlimits (memory, CPU time,
  decoded length) inside the container's ceiling. It does not copy classbeat's seccomp/Landlock
  machinery.
- **Audio:** in service mode temporary audio is deleted at the end of each job and nothing is served.
- **classbeat side:** one new egress builder (a `spotdown` origin setting, modelled on the player URL
  setting of ADR 0034 §2; `tests/egress_scan.py` updated), a fake spotdown for tests, and a contract
  test against spotdown's versioned schema file. This is the one Opus-reviewed security piece (#1301).
  "Modelled on" means the shape (one operator-set origin, one host, no redirects, a size cap), not the
  rule: `player/base_url_policy.py` accepts only https on port 443 with a public DNS name and refuses
  single-label, `.internal` and localhost names (and ADR 0034 §2 refuses private addresses), so it
  refuses an internal compose origin such as `http://spotdown-api:8000`. **P4 (#1301) needs its own
  origin rule, for an internal origin only, and an amendment to the `egress.py` rule that "the list is
  code, not a setting"**, as ADR 0034 §2 made for the player.
- **YouTube:** optional, off by default, enabled per deployment by the owner. Downloading from
  YouTube breaks YouTube's terms, and using it for Spotify-catalogue classes touches Spotify's
  Developer Policy. Returning only metadata and being noncommercial do **not** remove that risk; the
  risk is the owner's to accept (§12, #1296; accepted 2026-11-20). Licensed sources (SwedeBeat, user audio) always win when
  they exist. Agents and CI never call YouTube.

Decided by default, and the owner can override: the same-box hosting; a single service key; relaying
bytes instead of signed tickets; the initial quality thresholds (§9).

### 11. Governance, both repositories (plan §9)

`magpern/spotdown` uses classbeat's workflow (AGENTS.md, CLAUDE.md) unchanged and gets no weaker
policy.
- **Roles:** Opus is the Architect and Orchestrator; Sonnet is the Developer; Haiku is the Explorer.
- **Reviews:** **Opus reviews every PR of this programme in both repositories** (the owner's
  instruction, stricter than AGENTS.md's Sonnet review for non-security PRs; roadmap Amendment 31).
  Each PR's first review is a fresh reviewer; later rounds resume the same reviewer. *(Amended
  2026-11-20, owner: docs-only PRs may be reviewed by Sonnet; see the amendment "review and developer
  process" at the end.)*
- **Merge controls:** a recorded `Agent review (<model>): PASS <exact head SHA>`; green required
  checks; merge only with `gh pr merge <n> --squash --match-head-commit <sha>`; never `--auto` or
  `--admin`, never an approving review.
- **Work rules:** Conventional Commits; one issue per PR; worktrees; tests never touch the network.
- **spotdown bootstrap (P0, [spotdown#2](https://github.com/magpern/spotdown/issues/2)):** an
  `AGENTS.md` and `CLAUDE.md` that point to these rules; CI (lint, types, tests) as the required `ci`
  check; a `pr-title` commitlint check; `.claude/agents` role definitions. The ruleset on spotdown's
  `main` (PR required, required checks, squash only) is set by the **owner**
  ([spotdown#1](https://github.com/magpern/spotdown/issues/1)), because agents never change rulesets.

### 12. Amendment to ADR 0010 and ADR 0021 §2 (effective 2026-11-20: the owner accepted #1296)

**Where the ban is.** The explicit ban is in **ADR 0021 §2**: "No audio processing of Spotify content
exists or may be added (ADR 0010, Spotify Developer Policy)". ADR 0010 itself states no such ban; it
records the Spotify Developer Policy risks the owner accepted for the beta circle (§III 14, §III 10,
§IV 2, §IV 3.1(a)) and the feature table that the live activation gate reads. ADR 0021 §2 cites both.

**Accepted by the owner on 2026-11-20** (the amendment "#1296 accepted" at the end). Before that,
ADR 0010 and ADR 0021 §2 stood exactly as written: no YouTube source was enabled, and SD-P6 (#1303)
was held. SD-P1 to SD-P5 are not affected by
this amendment, as none needs live YouTube (SD-P3 builds the YouTube matcher on faked yt-dlp,
with no live enablement); analysing SwedeBeat tracks, the mix and user audio is not processing of Spotify
content.

**Effective since the owner's acceptance of #1296 (2026-11-20):**
1. **ADR 0021 §2** is amended to read, for Spotify tracks: classbeat processes no Spotify audio, and
   spotdown never fetches audio from Spotify. A Spotify-catalogue track may be analysed by spotdown
   **from a non-Spotify source of the same recording** (the optional YouTube source, off by default
   and enabled per deployment by the owner), with the timing class `approximate` at best (§3: such a
   match is never `exact`). The rest of ADR 0021 §2 is unchanged: part of a Spotify song stays not
   allowed, neither audio nor timing.
2. **ADR 0010** gains an accepted risk and a table row for the beta circle: YouTube-derived analysis
   of Spotify-catalogue tracks. Downloading from YouTube breaks YouTube's terms; using it for
   Spotify-catalogue classes touches Spotify's Developer Policy (classbeat sends spotdown the
   Spotify-derived title, artists, duration and ISRC when it has one, and spotdown searches YouTube
   with them); returning only metadata and being noncommercial do not remove that risk. The owner
   also decides whether YouTube is enabled in production at all. **The risk also covers retention:**
   spotdown's global caches and match records for Spotify-catalogue tracks (§7) are kept without any
   class reference, so ADR 0010's "nothing Spotify-derived is kept indefinitely" and ADR 0007 §11's
   reference-driven purge do not hold for them until the resolution point named in §7 (a bounded
   lifetime or purge in spotdown#5, or a follow-up ADR) is settled (settled 2026-11-20: item 5 of the
   amendment "#1296 accepted"). Beyond the circle the row needs the
   providers, as the rest of ADR 0010's table does.

The acceptance date (2026-11-20) is recorded in ADR 0010 and ADR 0021 as dated amendments that point
here. *(Before the answer: if the owner had declined, this section would have lapsed and SD-P6 would
not be built.)*

### 13. ADR 0026 stage 3, option (iii), is met by spotdown

ADR 0026 stage 3 option (iii) is "the main worker does the MOWL calls and hands the analysis host only
audio it fetched, so the host holds no KEK". spotdown has that shape: classbeat's worker keeps every
token, the KEK and the downloads, and streams only the bytes to spotdown (§10), which holds no KEK, no
model key, no user token and no signed URL and has no database access to classbeat. spotdown runs on
the same box today (§10), so ADR 0026's stage 3 trigger and its second-host preconditions (private
network to Postgres, secrets on a second host, a second deploy) are not invoked; if spotdown later
moves to its own host, it does so with option (iii) already in place and needs no KEK there.

### 14. The work and the owner's decisions

| Phase | Issue | Repo | Depends on | Owner blocker |
|---|---|---|---|---|
| P0 | #1300 this ADR | classbeat | #1307 | – (§12 took effect on #1296, 2026-11-20) |
| P0 | [spotdown#2](https://github.com/magpern/spotdown/issues/2) governance bootstrap | spotdown | #1307 | then [spotdown#1](https://github.com/magpern/spotdown/issues/1) ruleset |
| P0 | [spotdown#3](https://github.com/magpern/spotdown/issues/3) contract and schema v1 | spotdown | spotdown#2, with #1300 | – |
| P1 | [spotdown#4](https://github.com/magpern/spotdown/issues/4) service mode, auth, upload, url, sandbox | spotdown | spotdown#2, spotdown#3 | merge after spotdown#1 |
| P1 | [spotdown#5](https://github.com/magpern/spotdown/issues/5) caches, lookups, provenance | spotdown | spotdown#4 | – |
| P2 | [spotdown#6](https://github.com/magpern/spotdown/issues/6) quality harness and baseline gates (bounded) | spotdown | spotdown#4, spotdown#5 | real-music gate runs: #1298 |
| P2+ | [spotdown#7](https://github.com/magpern/spotdown/issues/7) labels, [spotdown#8](https://github.com/magpern/spotdown/issues/8) phrases, [spotdown#9](https://github.com/magpern/spotdown/issues/9) downbeats (never blocking) | spotdown | spotdown#6 | – |
| P3 | [spotdown#10](https://github.com/magpern/spotdown/issues/10) YouTube matcher and source | spotdown | spotdown#4, spotdown#5 | live enablement: #1296 accepted; the deployment setting is the owner's |
| P3 | [spotdown#11](https://github.com/magpern/spotdown/issues/11) alignment layer | spotdown | spotdown#3, spotdown#5 | – |
| P4 | #1301 client, egress, fake, policies, shadow | classbeat | #1300, spotdown#3, spotdown#4, spotdown#5 | – |
| P5 | #1302 studio tracks via spotdown | classbeat | #1301, spotdown#6 | default switch: #1298 |
| P6 | #1303 Spotify flow via spotdown | classbeat | #1301, spotdown#6, spotdown#10, spotdown#11 | #1296 accepted 2026-11-20; default switch: #1298 |
| P7 | #1304 mix via spotdown | classbeat | #1302, spotdown#6 | default switch: #1298 |
| P7 | #1305 ADR for `user_audio` | classbeat | #1300, #1302 | – |
| P7 | #1306 retirement review | classbeat | #1302, #1303, #1304 | **#1299** |

Owner decisions: #1296 (the §12 amendment: the YouTube terms-of-service risk for Spotify-catalogue
classes, and whether YouTube is enabled in production at all; **accepted 2026-11-20**, so P6 is no
longer gated by it); #1297 (cross-user reuse
of private-source analyses, a licence or entitlement question, ADR 0026 (a); default no; blocks
nothing); #1298 (the real-music quality corpus, the annotators and the blind review; blocks the
default switches); #1299 (retiring the local and MOWL-based paths); spotdown#1 (spotdown's ruleset).

## Alternatives

- **Keep all analysis in classbeat** (extend `track_analysis` and `mixanalysis`): rejected. It keeps
  heavy, hostile-input decoding next to the worker's KEK and tokens and gives Spotify classes nothing
  beyond MOWL's sections. The local paths stay, as fallbacks (§9).
- **Analyse the whole pool:** rejected. Only the selected tracks are analysed (§2); the pool gets the
  free cache summary only.
- **spotdown calls the Spotify API (Client Credentials) in the integration:** rejected. classbeat
  already holds the metadata; the adapter stays only for spotdown's standalone UI (Context).
- **Treat a confident metadata match, an equal ISRC or agreeing MOWL sections as `exact`:** rejected.
  None is proof of identical audio; a false `exact` is a hard gate (§3, §9).
- **Reuse a cached analysis on an equal decoded-audio fingerprint:** rejected; only an equal sha256 or
  the source-reference link to the same audio identity (§7).
- **Share private-source analyses across users:** not by default; the owner's licence decision
  (#1297).
- **A separate analysis host, per-caller keys, signed download tickets:** not now (§10's defaults,
  each reversible by the owner). ADR 0026 stage 3 still governs a second host.
- **An inbound callback endpoint in classbeat:** rejected; polling from fenced job steps needs none
  (§4).

## Consequences

- Nothing changes in code, settings, compose or deployment with this ADR. The phases were authorized
  on 2026-11-20 (roadmap Amendment 31; the amendment below), except the owner decisions #1296 to #1299 and magpern/spotdown#1.
  #1296 was accepted the same day (roadmap Amendment 32; the amendment "#1296 accepted").
- The boundary is fixed before code: spotdown gets bytes or public metadata and an opaque scope,
  never a token, a signed URL or a password, and classbeat alone decides timing classes, fitness and
  what the model may do with a track.
- New pure modules in classbeat when built (P4): `timing_class_policy`, `track_fit_policy`,
  `spotdown_map_policy`, each with table-driven tests; the design validator gains the non-`exact`
  refusal; one new egress builder with the Opus review (#1301). The threat model carries the planned
  rows for the new boundary (T130 to T137).
- spotdown becomes a second repository under the same gate, with Opus reviewing every PR of the
  programme.
- Removing today's analysis paths is never automatic: it needs §9's evidence, production use and the
  owner's sign-off (#1299).
- The tests the plan requires when built (plan, "Verification"): in spotdown, unit tests per layer,
  no audio left after a job, 401 without the key, `/files` 404 in service mode, `url` source SSRF
  tests, results valid against schema v1, the metrics runner on the committed fixtures in CI; in
  classbeat, table-driven policy tests, the egress scan, fake-spotdown job tests for every row of §8
  (including an outage and a fallback), the validator refusing precise cues on approximate tracks, a
  YouTube track never `exact` (a confident match, an equal ISRC and agreeing anchors), a
  fingerprint-only match never reusing an analysis, neither lookup crossing scopes, provenance on
  every stored analysis, partial replacement leaving the other tracks unchanged, and e2e against the
  fake; the §9 corpus and workout comparison before any default switch or removal.

### Amendment (2026-11-20): authorization and spotdown quality-gate thresholds as built (extends §9)

The phases were authorized on 2026-11-20 (status.md). spotdown's `quality_gates` file is **version 4**,
its analyzer document **version 6.3**, its core analyzer **version 6**. The thresholds below are
**regression checks on the committed corpus, not held-out evidence**: every confidence cap stays
(a heuristic, 0.4) until held-out evidence exists (magpern/spotdown#20).

| Claim | Gate | Cap |
|---|---|---|
| Labels | `label_accuracy` >= 0.70; `label_overconfidence` <= 0.10 | 0.4, heuristic |
| Phrases | `phrase_f` >= 0.70 (`phrase_tol_beats` 2); `phrase_overconfidence` <= 0.03; a 3/4 meter claim yields no phrases | 0.4, heuristic |
| Downbeats and meter | `downbeat_f_4_4` >= 0.85; `downbeat_f_3_4` >= 0.75; `meter_accuracy_min` >= 0.70; `downbeat_overconfidence` <= 0.03; no meter claim without accent evidence | 0.4, heuristic |

Notes for operators and the next phases:

- **(a) Access log.** spotdown's query-string filter (`app/service/access_log.py`) must be re-checked
  if the deployment uses `--log-config` or another server than the one it was written for.
- **(b) Retention (§7).** Global match records and alignments are bounded
  by `GLOBAL_REFERENCE_TTL_S` (default 3 days). Global analyses, links and `provenance.source.match`
  are still unbounded in spotdown's code; their default lifetime is settled by item 5 of the
  amendment "#1296 accepted" below.
- **(c) Path (b) beat equivalence** has no consumer in policy v1: `spotdown_map_policy` maps only
  `played_recording`.
- **(d) Shadow mode** (`CLASSBEAT_SPOTDOWN_SHADOW`, off by default) and its open points are in
  [developer-commands.md](../developer-commands.md) ("spotdown shadow mode for studio tracks").

### Amendment (2026-11-20, owner): #1296 accepted; YouTube for Spotify classes, analysis through spotdown, retention of the global entries

The owner answered #1296 on 2026-11-20
([comment](https://github.com/magpern/classbeat/issues/1296#issuecomment-7050972098)): "Yes, as
owner, I accept the risk. Turn Youtube on." and, to the default "Spotify classes keep today's MOWL
sections": "No, analysis goes through spotdown." Roadmap Amendment 32 records it. This amendment
changes no code, setting, compose file or deployment.

1. **ADR 0010 and ADR 0021 §2 are amended as §12 states** (items 1 and 2), effective 2026-11-20:
   YouTube-derived analysis through spotdown may be used for Spotify-catalogue classes. The owner
   accepts the terms-of-service risk: downloading from YouTube is against YouTube's terms, and using
   the result for Spotify-catalogue classes touches Spotify's Developer Policy (§III 14). Returning only
   metadata and being noncommercial do not remove it. Such a track is `approximate` at best (§3). Both
   ADRs carry a dated amendment that points here.
2. **YouTube sourcing may be enabled.** spotdown's `ALLOW_YTDLP_SOURCING` stays off by default in code.
   The owner deploys spotdown with it switched on; that is the owner's deployment step, never an
   agent's.
   **Owner clarification (2026-11-20): classbeat does not police how the analysis service sources
   audio.** classbeat treats the analysis service as an **owner-chosen external service** (spotdown is
   the configured one) that returns the schema v1 JSON document (§5). Where that service gets its audio,
   whether that sourcing is lawful, and its sourcing settings (`ALLOW_YTDLP_SOURCING` and the like) are
   the owner's deployment concern, not classbeat code's; classbeat has no setting, check or gate for
   them. The safeguards about **classbeat's own behaviour** stay unchanged: the timing classes and their
   evidence (§3), no `exact` for an approximate or matched source, the scopes and lookups that never
   cross users (§7), no audio kept by classbeat, the egress rule (§10), and no real call by an agent or
   CI.
3. **Spotify classes get their analysis through spotdown.** This replaces the default "Spotify classes
   keep today's MOWL sections" (#1296, "Default until answered"). The MOWL sections stay as the
   fallback, per track or for the whole class, when spotdown is unreachable, a track is `unusable`
   (§3, §8), or spotdown is switched off in classbeat or returns no usable analysis (for example
   because its YouTube source is off). SD-P6 (#1303) may build the
   spotdown-first path.
4. **Not covered by this decision:** any live call by an agent or CI (agents and CI never call
   YouTube; tests fake yt-dlp); deploying; and the default-switch evidence gates of §9: #1298 still
   gates making spotdown-first the shipped default for users, unless the owner says otherwise there.
   Licensed sources (SwedeBeat, user audio) still win when they exist (§10).
5. **Retention of the global entries (settles §7's open item; the Architect's safest reversible
   default, the owner can override it).**
   - Public-source (YouTube) analyses, their source-reference links and their match records (the
     source-match cache and `provenance.source.match`) are **global**. They hold no user data: no
     `owner_id`, no per-user link, no `cache_scope` and no reference tied to an owner.
   - **Lifetime:** each is kept until its `analyzer_version` (for a match record, its
     `matcher_version`) changes or it has gone **90 days unused** (not returned by a lookup or a match),
     then purged. The 3-day bound on global match records and alignments (`GLOBAL_REFERENCE_TTL_S`,
     the amendment above, note (b)) stays.
   - classbeat's **per-owner record** of which analysis a class used lives only in its class
     provenance (§6), under ADR 0007 §11's per-owner retention. Deleting a user never touches the
     global cache, and `DELETE /v2/scopes/{scope}` stays scoped (§7).
   - **Takedown:** the operator can purge a source reference, which removes its link, its analysis and
     the match records that chose it, on a rights holder's or provider's request.
   - This replaces, for these global entries only, ADR 0010's "deleted once no class of any user
     refers to it" and ADR 0007 §11's reference-driven purge: a bounded lifetime instead of a
     reference count, because the entries are not tied to any class. Private-source analyses keep
     their scope and their purge (§7). The 90-day purge and the takedown are a spotdown change that the
     Architect tracks as a spotdown issue. Until it ships, these entries are unbounded, which the
     owner's acceptance of #1296 covers (§12 item 2).
6. **§12's hold is lifted.** SD-P6 (#1303) is unblocked (this amendment, which settles §7, has merged).
   Its other dependencies in §14 stand.

### Amendment (2026-11-20, owner): review and developer process for this programme (extends §11)

- **Docs-only PRs** of the SD programme may be reviewed by **Sonnet** instead of Opus (AGENTS.md
  review gate, item 2, already allows Sonnet for non-security PRs). Code PRs keep the Opus review of §11.
- **A Developer who gets a second `FAIL` on the same PR** is replaced for the next fix round by an
  **Opus Developer**: the same agent resumed on Opus, or a fresh Opus Developer given the PR's context.
- **Before handing back a fix,** the Developer runs every reviewer finding through a class-wide
  self-check: grep for the same defect pattern elsewhere, make each call inside an error-swallowing `except` raise and check the outcome, show that
  each gate can fail, and verify every claim against the code.

### Amendment (2026-11-20): the match stage of the Spotify flow, as built (SD-P6 stage A, #1303; amends §8 for that stage)

Recorded with the code (#1325). The owner's acceptance of #1296 (the amendment "#1296 accepted"
above) is what allows SD-P6 to be built; the stage is off by default and allowlisted per owner
(`CLASSBEAT_SPOTDOWN_SPOTIFY`, `CLASSBEAT_SPOTDOWN_SPOTIFY_OWNERS`), with no value that turns it on
for everyone.

1. **This supersedes §8's `rate_limited` row ("fenced requeue after `retry_after_s`") for the
   Spotify match stage** (Architect's decision, 2026-11-20, on the review of #1325). A `rate_limited`
   track, or a `429` for the whole request, is waited for **in the run**: the clamped `retry_after_s`
   is charged to the job's wait budget **before** the wait, the wait is made only when it fits what is
   left, the run checkpoints at least every 10 s while it waits (a cancel, a shutdown or a lost lease
   stops it, the wait already counted), and at most 6 requests are made per batch. What the budget
   cannot wait for keeps MOWL's sections. *Why:* the match is one cheap metadata request per batch, a
   requeue would rerun the model stage before it (`pick_pool`), and the bound is the same 600 s per
   class; it follows the precedent of shadow mode's in-run polls (§9) and SD-P5. It still "does not
   count as an attempt". Plan §6 carries a dated note.
2. **The wait budget counts waits, not wall time**: the 600 s are the seconds the job has waited for
   spotdown (retry pauses and `rate_limited` waits) over all its runs (`spotdown_song_budgets`), so the
   model stages between the match and the later analysis stage do not spend it. The analysis and
   refinement stages share this budget.
3. **Idempotency keys** are derived from the job, the replacement round, the batch, the pass and a hash
   of the tracks asked: a new pass (the retry, or after a wait) is a new request, because spotdown
   replays a repeated key's first answer for 24 hours. A later run of the job that asks the identical
   batch therefore gets the earlier run's answers again, including a replayed `rate_limited` or
   `provider_error`, and may spend budget waiting on them; the outcome is then MOWL's sections for
   those tracks (bounded; a re-run `pick_pool` rarely yields the identical batch).
4. **Per-track fallback at the match** (within §8's rows): an `uncertain` match, a `provider_error`
   still there after its one retry and a `rate_limited` that the budget cannot wait for keep their song
   with MOWL's sections (never used for timing); `not_found` and `source_unavailable` are missing
   (spares, then at most 3 replacement rounds; after them MOWL's sections). A provider outage, spotdown
   unreachable or an error in the stage give today's path for the whole class and every later run of
   the job.


## Status

Accepted. §12 took effect on 2026-11-20, when the owner accepted #1296.
