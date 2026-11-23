# ADR 0040: spotdown as classbeat's music-analysis service (#1300)

Status: Accepted. This ADR itself changes no code, setting, compose file or runtime behaviour. (Since #1356 and #1357 the local studio-track and mix analyses are gone: `design/mixanalysis.py` and `design/mixchild.py` are deleted, migrations 0075 and 0077 deleted the `local` rows, and the render child keeps the confinement of `design/sandbox.py`.)
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
*Superseded 2026-11-21 (owner, #1299): spotdown is the only analysis path, with no fallback; see the amendment "spotdown is the only analysis path" at the end.*

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
*Superseded 2026-11-21 (owner, #1299) for step 5's "fall back for that track": an unsuitable track is replaced or accepted with a warning, never timed another way; the amendment "spotdown is the only analysis path" at the end.*

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
*Superseded 2026-11-21 (owner, #1299): the MOWL sections are no longer a step of the fall-through, and an `unusable` track is missing (replaced), never timed on MOWL's sections; the amendment "spotdown is the only analysis path" at the end (items 3 and 5).*

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
  *(Amended 2026-11-21, owner answer to #1297, #1338: a SwedeBeat **studio track** is analysed in one
  scope shared by every owner; mixes and user audio keep the owner's HMAC scope. That shared scope
  is a scoped entry spotdown never purges (not on a version change, not after 90 days unused), so
  it is kept until an operator deletes it, and rotating the `cache_scope` key orphans it for good.
  See the amendment "studio-track analyses are shared" at the end, item 5, and
  magpern/spotdown#40.)*
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

*The fallbacks of this table and of these bounds are superseded 2026-11-21 (owner, #1299): spotdown is the only analysis path; see the amendment "spotdown is the only analysis path" at the end.*

A failure in one track changes only that track. Before design that is a selection change; after
design it goes through `revise` for that part.

**Amendment (2026-11-20, SD-P5, #1302; Architect decision on the review of PR #1320).** For the
studio tracks classbeat uploads, `rate_limited` waits its `retry_after_s` **in the run** (with
checkpoints, not counted as an attempt, with the same `Idempotency-Key`s, so the same upload is
never sent as a new one) **within the class's wait budget**, instead of a fenced requeue. The wait
budget (600 s) and "spotdown unreachable" hold **per class**: per design job, over all its runs
(`spotdown_studio_budgets`, migration 0064), so an outage found by one run makes the whole class
local and a class of many tracks never waits more than one budget. While spotdown is consulted the
job holds its mix-analysis slot and the track's capped download stays on disk, up to that budget.
The budget is **wall-clock time since the job's first spotdown call** (`started_at`), not time spent
waiting for spotdown: the local analyses of other tracks and the time the job waits requeued (for an
analysis slot) count against it too. That is the safe side of "wait about 10 min": in a long class
the later tracks can go local while spotdown is fast, so the rollout's fallback-rate statistics
(§9) count the tracks that went local because the budget ran out apart from those spotdown failed.
*Superseded 2026-11-21 (owner, #1299): spotdown is the only analysis path; the budget is per attempt and an outage fails the job `analysis_unavailable`; see the amendment "spotdown is the only analysis path" at the end.*

Two bounds that follow from keeping both analysers' rows (#1302 review): `MAPS_PER_USER` (200) in
`swedebeat_track_maps` counts the `local` and `spotdown` rows together, so a user who switches
analyser reaches the trim sooner, and a class whose recorded row was trimmed answers
`music_changed` (Change songs analyses again). The provenance kept with a class in
`class_revisions.music` (`analyses`) is at most one record per track, at most 40 (`MAX_TRACKS`,
checked by the model), each no larger than the 64 KiB the cache table allows its `provenance`.
A class reads back exactly the row it recorded: its analyser, its bytes and the hash of its map
**as stored** (jsonb's form, so `-0.0` and `1e+16` hash as the stored `0.0` and integer).
*Note 2026-11-23 (#1299, item 6 of the amendment "spotdown is the only analysis path"): migration
0081 drops the single-valued `analyser` column of `swedebeat_track_maps` and `swedebeat_mix_maps`
(the keys are `(owner_id, reference, analyser_version)` and `(owner_id, mix_id)` again, and
`provenance` is required); a class reads back its row by its bytes and its map's hash, and its
record (`analyses`) names no analyser. A record stored earlier with `analyser: "spotdown"` still
reads (the key is dropped on read); any other value is refused.*

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

*Superseded 2026-11-21 (owner, #1299): spotdown is the only analysis path, with no rollout, shadow mode or default switch, and these gates are engineering checks, not shipping gates; see the amendment "spotdown is the only analysis path" at the end.*

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
*#1299 was signed off for every source on 2026-11-21 and #1298 gates nothing (owner); see the amendment "spotdown is the only analysis path" at the end.*

## Alternatives

- **Keep all analysis in classbeat** (extend `track_analysis` and `mixanalysis`): rejected. It keeps
  heavy, hostile-input decoding next to the worker's KEK and tokens and gives Spotify classes nothing
  beyond MOWL's sections. The local paths stay, as fallbacks (§9).
  *(Superseded 2026-11-21: the local paths are retired; the amendment "spotdown is the only analysis path".)*
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
  *(Superseded 2026-11-21: the owner signed off for every source; the amendment "spotdown is the only analysis path".)*
- The tests the plan requires when built (plan, "Verification"): in spotdown, unit tests per layer,
  no audio left after a job, 401 without the key, `/files` 404 in service mode, `url` source SSRF
  tests, results valid against schema v1, the metrics runner on the committed fixtures in CI; in
  classbeat, table-driven policy tests, the egress scan, fake-spotdown job tests for every row of §8
  (including an outage and a fallback), the validator refusing precise cues on approximate tracks, a
  YouTube track never `exact` (a confident match, an equal ISRC and agreeing anchors), a
  fingerprint-only match never reusing an analysis, neither lookup crossing scopes, provenance on
  every stored analysis, partial replacement leaving the other tracks unchanged, and e2e against the
  fake; the §9 corpus and workout comparison before any default switch or removal.
  *(Superseded 2026-11-21: no default switch and no evidence gate on removal; the amendment "spotdown is the only analysis path".)*

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
  *(Deleted 2026-11-22, #1412: see the amendment "spotdown is the only analysis path".)*

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
   *Superseded 2026-11-21 (owner, #1299): spotdown is the only analysis path, with no fallback; see the amendment "spotdown is the only analysis path" at the end.*
4. **Not covered by this decision:** any live call by an agent or CI (agents and CI never call
   YouTube; tests fake yt-dlp); deploying; and the default-switch evidence gates of §9: #1298 still
   gates making spotdown-first the shipped default for users, unless the owner says otherwise there.
   *Superseded 2026-11-21 (owner, #1299): #1298 gates nothing and there is no default switch; see the amendment "spotdown is the only analysis path" at the end.*
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
   *Superseded 2026-11-21 (owner, #1299): spotdown is the only analysis path, with no fallback; see the amendment "spotdown is the only analysis path" at the end.*

### Amendment (2026-11-20, SD-P7a, #1304): the SwedeBeat mix through spotdown, as built (extends §6 to §9)

**Off by default and blocked on #1298 (Architect, 2026-11-21).** The mix switch stays `off`, and
no value turns it on for everyone, until the §9 mix gates pass on the owner's real mixes (#1298): the
song reading below is known to over-segment a real mix and to give it one BPM while spotdown emits
no tempo change points, and the harness's recall-only edge gate cannot see that.
*Superseded 2026-11-21 (owner, #1299): #1298 gates nothing and there is no default switch; see the amendment "spotdown is the only analysis path" at the end.*

- **Source and switch.** The mix keeps today's MOWL temporary-link fetch (`design.mixfetch`, 250 MB;
  the link is never stored, logged or sent to spotdown). spotdown is the source of the mix's map only
  with `CLASSBEAT_SPOTDOWN_MIX=allowlist` and the job's owner in `CLASSBEAT_SPOTDOWN_MIX_OWNERS`
  (worker only, its own switch, default `off`; no value means everyone: that waits for the mix gates
  on the owner's corpus, #1298). Off, the code path and every stored byte are today's.
- **Lookups** (§7): the owner's `swedebeat_mix_maps` row, then `lookup_content` by sha256 in the
  owner's `cache_scope`, then the streamed upload and the analysis. Account deletion deletes the scope
  through the existing outbox (one scope per owner covers studio tracks and mixes).
- **Mapping.** `timing_class_policy` path (a) (`swedebeat_mix`, the uploaded bytes are the played mix:
  `exact`), then `mix_map_policy` (version 1): spotdown has no notion of a song, so a song starts and
  ends at the content edges and at the **trusted** structure boundaries, and its BPM is the tempo at
  its midpoint from `tempo.bpm` and the **trusted** change points: the reading spotdown's harness uses
  for the §9 mix gates, restricted to trusted features. Kept as is (Architect, 2026-11-21): a structure
  boundary inside a song becomes a song edge, so a real mix can come out over-segmented, and while
  spotdown emits no change points every song has the mix's one BPM. A span without a trusted beat joins its
  neighbour; beat 1 is the first trusted downbeat, else the first beat with a zero margin (shown as an
  estimate). A drop or breakdown outside every song is left out. The provenance record adds `mix_map_policy` to its policy versions.
- **Rows per analyser.** `swedebeat_mix_maps` is keyed `(owner_id, mix_id, analyser)` (migration
  0065) with the `spotdown` row's provenance; a revision designed on spotdown's map records
  `analyser` and the provenance (kept per class, §6), and a revise or refit reads the row whose map has
  its base's fingerprint first, whatever the switch, so a class never changes analyser silently.
  The web app's admission counts only `local` rows as a cache hit for the daily limit (stricter).
  *Note 2026-11-23 (#1299): migration 0077 left only `spotdown` rows and migration 0081 drops the
  column: the key is `(owner_id, mix_id)`, a revision records only the provenance (a stored
  `analyser: "spotdown"` is dropped on read), and the admission counts a current row as a hit.*
- **Bounds** (§8). The per-class budget and outage flag are the design job's
  `spotdown_studio_budgets` row: the mix **reuses that table** (Architect, 2026-11-21) because a design
  job designs on either a mix or studio tracks, never both, so one class has one budget; a mix also waits
  for spotdown at most 180 s (today's local mix-analysis limit), then falls back. Every failure, and
  an error of any type in the spotdown code, falls back to the local `mixanalysis` and `mixchild`
  path. The local child's 180 s and 1.5 GiB limits are unchanged; spotdown's own are its sandbox's.
  ADR 0026 stage 1's decode time is the analysis that made the map; the spotdown consultation is
  logged apart (`spotdown_ms`).

*Superseded 2026-11-21 (owner, #1299): spotdown is the only analysis path, with no switch and no local fallback; the 180 s cap ends in `analysis_unavailable`; see the amendment "spotdown is the only analysis path" at the end.*

### Amendment (2026-11-20): the analysis and refinement stage of the Spotify flow, as built (SD-P6 stage B, #1303; amends §8 for that stage)

Recorded with the code. Behind the same allowlist as the match stage (it runs only when the match ran
for the class and did not fall back); off by default.

1. **Only the chosen songs are analysed**, after `choose_songs` and before the list is stored: each new
   song whose match is `timing`, by its match's YouTube sources (the best, then at most 2 alternates),
   with **no `cache_scope`** (a `youtube` analysis is global; contract v2 makes it optional for a
   youtube-only request) and a `spotify` reference whose id is an opaque digest of the Spotify id (no
   Spotify id leaves). A cache hit is ready at once. Every result is **at best `approximate`**
   (§3); an `unusable` one is discarded and the song keeps MOWL's sections.
2. **This supersedes §8's `rate_limited` row ("fenced requeue after `retry_after_s`") for the
   Spotify analysis stage**, as the match-stage amendment above (item 1) does for the match stage
   (Architect's decision, 2026-11-21, on the review of #1329). A `rate_limited` start or job is waited
   for **in the run**: charged to the job's budget before the wait, made only when it fits what is left,
   with checkpoints at least every 10 s; it does not count as an attempt. *Why:* a requeue would rerun
   the model stages before it (`pick_pool`, `choose_songs`), and the bound is the same 600 s per class.
   Plan §6 carries a dated note.
   **The other reactions (within §8's rows):** `provider_error` once again, then MOWL's sections for that song, and a provider outage of a
   round (≥ 50 % and ≥ 3 songs) gives today's path for the whole class (no swaps); `processing_failed`
   is not retried on the same audio except once after a `timeout`, then the next alternate, then MOWL's
   sections; `source_unavailable` and `not_found` the next alternate, then MOWL's sections;
   `alignment_failed` and `uncertain` MOWL's sections at once. At most 6 requests per song. For a
   Spotify class MOWL's sections always exist for a chosen song, so §3's fall-through ends there.
3. **Refinement:** `track_fit_policy` checks each `approximate` song against its slot (the BPM and
   length it was chosen with, and a low-energy start for a warm-up); at most 2 rounds and a third of
   the list per round, each unsuitable song replaced alone by a ranked spare that keeps the list's
   total within 10 % (no model call), else accepted with a warning. The poll pauses, retry pauses and
   waits all count against the one 600 s budget the match stage opened.
4. **Kept:** the `spotdown_analysis` record in the list's provenance (§6's per-track items: the
   analysis id, the analyser, the audio identity, the source kind, the timing class with its reason and
   evidence, the policy versions, the fit, and the refinement swaps); never the video id, a scope, a
   `client_ref`, a URL or a job id. **Fallback** to the list as chosen and no record, for the whole
   class and every later run of the job: spotdown unreachable, an outage, or an error of any type in
   the stage.
   *Superseded 2026-11-21 (owner, #1299): spotdown is the only analysis path, and item 2's and this item's fallbacks are replaced by its per-track rule (item 5) and failure categories (item 4); see the amendment "spotdown is the only analysis path" at the end.*
5. **Not yet:** the design map's per-track timing class (`track_timing_calc.stamp`, SD-P6 stage C)
   and the use of spotdown's sections in the design come from this record in a later stage.

### Amendment (2026-11-21): spotdown's analysis in the Spotify design, as built (SD-P6 stage D, #1303; amends §8 for that stage)

Recorded with the code. Behind the same allowlist as the match and analysis stages
(`CLASSBEAT_SPOTDOWN_SPOTIFY`, `CLASSBEAT_SPOTDOWN_SPOTIFY_OWNERS`): off by default, no value turns it
on for everyone. **Making it the shipped default still waits for the Spotify gates of plan §7 (#1298).**
*Superseded 2026-11-21 (owner, #1299): #1298 gates nothing and there is no default switch; see the amendment "spotdown is the only analysis path" at the end.*

1. **Where:** in phase 2 of a song pick (`songs.phase2.final_music`, for the pick, `revise_songs` and
   the composer), after the final playlist's MOWL analysis was checked against the approved ids and
   before the design stage (`songs.phase2_spotdown`). The match (stage A) and the analysis with its
   refinement (stage B) are **not run again** there: they ran in phase 1, before the list was stored
   and approved, and the approved list is fixed (a new match or refinement could only end
   `playlist_mismatch`). Phase 2 reads what stage B recorded (`spotdown_analysis`,
   `timing_marks`), and for each marked song reads the analysis again by its id
   (`GET /v2/analyses/{id}`, global: no `cache_scope`).
2. **What the design gets:** a marked song's document is used only when it is the recorded analysis
   (the same id and audio sha256, a `youtube` source: an allowlist) **aligned to this track** (a
   `spotify` reference with the track's opaque id and its length within 1 s)
   (`song_sections_calc.verified`). Its sections (`spotdown_map_policy.reference_sections`, on the
   Spotify clock) replace that track's MOWL sections, rebuilt by the library's own
   `mowl.spotify.build_map` with the track's MOWL length and BPM: the BPM stays Spotify's own, of the
   recording that is played, and spotdown adds section-level energy (its drops and breakdowns are the
   loudness steps between its sections, as for MOWL's). Every other track, section and event stays as
   it was. The track is then stamped (`track_timing_calc.stamp`, class and `provenance.source.kind`),
   so §3's design validator (`timing_gate`), the model's `timing` key and note (`timing_input`) apply
   to it. A `youtube` source is never `exact` (the cap of `track_timing_calc`); a class that is not
   `approximate` (`unusable`, or an unknown one, which reads `unusable`) is not applied.
3. **The label:** phase 1 sets `Song.timing = "approximate"` for each song stage B marks, so the list
   page shows "timing approximate" (en, sv, da) before the Approve.
4. **Per-track fallback (MOWL's sections, no stamp):** a document not verified, not mapped or not
   `approximate`, and a `rate_limited` read. *This supersedes §8's `rate_limited` row for this
   stage:* phase 2 never waits (the read is one cheap `GET` of a ready analysis; a wait would hold the
   approved class). **Whole-class fallback to today's music, byte for byte** (the MOWL analysis
   unchanged, so everything the design model is sent is what it is sent with spotdown off):
   switched off or not allowlisted, a list with no record, spotdown unreachable on any read (every
   answer but `200` and `429`: a `5xx` outage and an analysis that is gone too), or an error of any
   type in the stage (swallowed; only its type is logged). Only `RunStopped`, `LeaseLost` and
   `DatabaseRoleError` pass, and the step is then closed.
   *Superseded 2026-11-21 (owner, #1299): spotdown is the only analysis path, with no fallback; see the amendment "spotdown is the only analysis path" at the end.*
5. **Not yet:** the design draft records which tracks were on spotdown's sections only through its
   `song_list_id` (the list's `spotdown_analysis` record); a refit or a revise of the design reads
   MOWL's analysis again and so designs on today's sections (no stamp), the path before SD-P6.
   Carrying the stamp there is a later stage of #1303. *(Done: the stage E amendment below.)*

### Amendment (2026-11-21): spotdown's timing through a revise, a refit and a change of songs, as built (SD-P6 stage E, #1303; closes the gap of stage D item 5)

Recorded with the code. Behind the same allowlist as stages A, B and D: off by default, no value
turns it on for everyone; **the shipped default still waits for the Spotify gates of plan §7
(#1298).**
*Superseded 2026-11-21 (owner, #1299): #1298 gates nothing and there is no default switch; see the amendment "spotdown is the only analysis path" at the end.*

1. **What a draft records.** A draft whose design map had at least one track on spotdown's sections
   (stamped `approximate` by stage D) adds `music.spotdown = {song_list_id, tracks}`
   (`design.revision_spotdown_model`): the 0-based positions of those tracks, sorted, and the
   `song_list` revision whose `spotdown_analysis` record gave them. Every other draft stores exactly
   as before (spotdown off, every fallback, a mix, a soundless class, a draft on MOWL's sections only).
   A stamped map without a list cannot happen and fails the job `internal`. Never a title, an
   analysis id, a source or a URL. A copy of a class (#550) drops it with `song_list_id`.
2. **The design: re-read through the stage-D path, not a stored map** (Architect's reversible choice).
   A `revise_draft` or `refit_draft` of such a draft reads the base's `music.spotdown`
   (`design.spotdown_base`, through the job's own class, only while the switch and allowlist are on),
   loads that list (`song_list.load`, its hash checked again), requires the music MOWL analysed now to
   be exactly the list's tracks in order, and runs stage D's `phase2_spotdown.spotdown_music` for
   **those positions only** (`only`): each analysis read again by its id and verified against the
   track, as in phase 2 (`songs.spotdown_carry`), before the beat-one corrections are attached. A
   track the base had on MOWL's sections stays on them, so a revise never changes the timing class of
   a track the base did not have. The new revision records the positions applied again (so a chain of
   chat revisions keeps them: a `revise_draft` revision still names no `song_list_id`, as before).
   *Why not carry the stamped map:* the design map is not stored (only its fingerprint), storing it
   would add up to tens of KiB per revision, and a stored copy could not be verified again; the
   re-read is one cheap `GET` per track and the same allowlisted verification as stage D.
3. **A change of songs** (`revise_songs`, plan §6 "partial changes": a failure after design goes
   through `revise` for that track only). Phase 1 matches and analyses only the new songs (stages A
   and B; a kept song is never in the pool). The new list's record then gets, for each kept song still
   in its own slot, the parent list's entry for that slot as it was, with `kept: true`
   (`spotdown.song_carry_calc.with_kept`): only a slot the parent marked (`timing_marks`) with exactly
   one entry, never one the new record names, and only when the new list has a record (stage A and B
   ran and did not fall back). So the kept songs keep their "timing approximate" label and phase 2
   reads them again and verifies them (stage D) like the new song.
4. **Fallbacks, all to today's path.** Per track (MOWL's sections, no stamp): a read `rate_limited`,
   a document not verified, not mapped or not `approximate`. For the whole revise or refit, the music
   step exactly as MOWL gave it (byte for byte): switched off or not allowlisted (no read at all), a
   base without the field (every draft stored before stage E, so a stage-D draft stored before this
   amendment is revised on MOWL's sections), a field that does not parse (a revise only: a refit,
   like Save and the beat-one page, validates the base's whole `music` strictly first, as it always
   has, so a malformed field, which only a hand edit of the database can make, fails it closed
   `internal`), the list gone or not
   loading, other music than the list's, spotdown unreachable on any read, or an error of any type in
   `spotdown_base`, `spotdown_carry` or `spotdown_music` (swallowed; only its type is logged; only
   `RunStopped`, `LeaseLost` and `DatabaseRoleError` pass). In a change of songs an error carrying the
   kept entries leaves them out (those songs keep MOWL's sections). The refit's `music_changed` check
   is unchanged (the fingerprint is MOWL's track list, the same either way), and a correction of a
   Spotify track is keyed by its track number, so it applies to the same track on either sections.
   *Superseded 2026-11-21 (owner, #1299): spotdown is the only analysis path, with no fallback; see the amendment "spotdown is the only analysis path" at the end.*
5. **Not carried:** the composer (ADR 0038) never runs stages A and B, so its lists have no record.
   A revise whose base was designed before this amendment (no field) designs on MOWL's sections.
6. **Rollback:** `SpotifyRevisionMusic` forbids unknown keys (`extra="forbid"`), so a build from
   before stage E refuses a draft that carries `music.spotdown` wherever it validates the music (Save,
   a refit, the beat-one page). Only the allowlisted owners' drafts can carry it. Before rolling the
   worker and web back past stage E, switch `CLASSBEAT_SPOTDOWN_SPOTIFY` off and accept that those
   drafts cannot be saved until the build is rolled forward again (or strip the field by hand).

**Audit of #1303's acceptance (plan §6, row by row; every test on the fake spotdown, no network):**

| Row or criterion | Test that proves it |
|---|---|
| `not_found` | match: `test_spotdown_song_match_job.py::test_missing_candidates_are_left_out_while_the_spares_fill_the_list`, `::test_a_short_pool_gets_a_replacement_round_with_the_swap_recorded`, `::test_at_most_three_rounds_then_mowls_sections_for_the_missing`; analysis: `test_spotdown_song_analysis_job.py::test_a_lost_source_moves_to_the_alternate[not_found]` |
| `uncertain` | `test_spotdown_song_match_job.py::test_uncertain_and_a_lone_provider_error_keep_their_song_with_mowls_sections`; `test_spotdown_song_analysis_job.py::test_uncertain_and_a_lasting_provider_error_keep_mowls_sections[uncertain]` |
| `source_unavailable` | `test_spotdown_song_analysis_job.py::test_a_lost_source_moves_to_the_alternate[source_unavailable]`, `::test_every_source_lost_keeps_mowls_sections_for_that_song_only`; match: `::test_missing_candidates_are_left_out_while_the_spares_fill_the_list` |
| `provider_error` (retry) and the outage | match: `::test_a_provider_error_that_clears_on_its_retry_is_matched`, `::test_unreachable_or_an_outage_is_todays_list_byte_for_byte[outage]`; analysis: `::test_a_provider_error_that_clears_on_its_retry_is_used`, `::test_a_provider_outage_keeps_the_list_and_swaps_nothing`, `::test_three_provider_errors_of_eight_are_not_an_outage` |
| `rate_limited` (in-run wait, superseding the requeue: stage A item 1, stage B item 2, stage D item 4) and the wait budget | match: `::test_rate_limited_waits_inside_the_jobs_budget_over_its_runs`, `::test_a_wait_that_does_not_fit_the_budget_is_not_made`, `::test_a_429_for_the_whole_request_waits_and_asks_again`; analysis: `::test_rate_limited_waits_inside_the_budget_and_asks_again`, `::test_the_budget_spent_keeps_mowls_sections`; phase 2: `test_spotdown_song_sections_job.py::test_a_song_whose_analysis_is_rate_limited_keeps_mowls_sections_alone`; revise: `test_spotdown_revise_carry_job.py::test_a_failed_read_of_one_track_changes_that_track_only` |
| `processing_failed` | `test_spotdown_song_analysis_job.py::test_a_lost_source_moves_to_the_alternate[processing_failed:decode_error]`, `::test_one_retry_after_a_timeout_on_the_same_source` |
| `alignment_failed` | `test_spotdown_song_analysis_job.py::test_alignment_failed_keeps_mowls_sections_without_an_alternate`, `::test_an_unusable_timeline_is_discarded_with_its_evidence` |
| spotdown unreachable | match: `::test_unreachable_or_an_outage_is_todays_list_byte_for_byte`; analysis: `::test_spotdown_unreachable_is_the_list_as_chosen_byte_for_byte`; phase 2: `test_spotdown_song_sections_job.py::test_a_fallback_at_phase_two_is_exactly_today`; revise: `test_spotdown_revise_carry_job.py::test_a_fallback_in_a_revise_is_exactly_todays_music` |
| Off is identical to today | `test_spotdown_song_match_job.py::test_off_sends_nothing_and_writes_nothing` (no YouTube among its cases), `test_spotdown_song_analysis_job.py::test_off_analyses_nothing`, `test_spotdown_song_sections_job.py::test_a_fallback_at_phase_two_is_exactly_today[off]`, `test_spotdown_revise_carry_job.py::test_a_fallback_in_a_revise_is_exactly_todays_music[off]`, `::test_a_change_with_spotdown_off_carries_nothing` |
| The whole pool is never analysed | `test_spotdown_song_analysis_job.py::test_only_the_chosen_songs_are_analysed_and_each_is_approximate`, `::test_a_replace_analyses_only_its_new_song`; `test_spotdown_revise_carry_job.py::test_a_change_of_one_song_analyses_it_alone_and_keeps_the_others_timing` |
| No beat-precise instruction on an `approximate` track | `test_agent_timing_gate.py`, `test_evals_spotify_approximate.py::test_the_validator_refuses_a_precise_cue_on_the_approximate_map` (and the revise and refit design on the stamped map: `test_spotdown_revise_carry_job.py`) |
| A failure in one track leaves the others unchanged | `test_spotdown_song_analysis_job.py::test_every_source_lost_keeps_mowls_sections_for_that_song_only`, `test_spotdown_song_sections_hook.py::test_only_the_marked_songs_change`, `test_spotdown_revise_carry_job.py::test_a_failed_read_of_one_track_changes_that_track_only`, `::test_a_track_the_base_had_on_mowls_sections_stays_on_them` |
| Partial changes: after design, through `revise` for that track only | `test_spotdown_revise_carry_job.py::test_a_change_of_one_song_analyses_it_alone_and_keeps_the_others_timing`, `::test_a_revise_and_a_refit_of_it_keep_the_tracks_on_spotdowns_sections`, `::test_a_refit_keeps_them_and_applies_the_correction` |
| The Spotify gates of §7 before the default switch | **owner-gated: #1298** (no agent turns the switch on). *Superseded 2026-11-21: #1298 gates nothing, no switch (the amendment "spotdown is the only analysis path").* |

**Not built: an e2e against the fake.** The e2e stack (`compose.e2e.yaml`, `e2e/`) has fakes for
MOWL, Spotify, OIDC, Paddle and the gateway but none for spotdown; one would need a new fake service,
its egress origin and key file in the stack, and the allowlist switched on for an e2e user. The job
tests above run the whole worker path (the jobs API, the queue, the fenced transactions, Postgres)
against the in-process fake instead. The e2e is the tracked follow-up #1337, to be done before the
default switch.
*(2026-11-21: there is no default switch; #1337 is done before the code PRs of the amendment "spotdown is the only analysis path", item 8.)*

### Amendment (2026-11-21, owner answer to #1297; #1338): studio-track analyses are shared (amends §7)

**Decision (owner, 2026-11-21, #1297).** An analysis of a SwedeBeat **studio track** serves every
user: the file is the same catalog file for everyone (the same sha256), it is looked up only once
classbeat already holds the bytes, and a hit only makes the request faster (nothing tells the user
it was analysed before). **Mixes are not shared:** they keep the owner's scope. **User uploads are
not planned** (ADR 0041); if they are ever built they keep the owner's scope and need their own
decision.

**As built (classbeat only; spotdown unchanged).**
1. **Not "no `cache_scope`".** spotdown's contract v2 requires a `cache_scope` for every upload, for
   the sha256 lookup and for the analysis of an upload (`422 scope_required`); only `youtube`
   sources are global there. The owner's "global, like a YouTube analysis" is therefore one
   **shared scope**: `scope_calc.shared_scope(key, "studio")`, `s1-` plus the unpadded base64url
   HMAC-SHA256 of the `cache_scope` key (§7) over its own label and the kind's name. It names no
   owner, and its prefix and label keep it apart from every owner's `o1-` scope. spotdown needs no
   change, and a later spotdown "global upload" kind could replace it without a classbeat schema
   change (only the scope string changes; the shared entries would then be analysed once more).
2. **An allowlist** (`spotdown.scope_policy.scope_for`, pure): `studio` gets the shared scope;
   `mix` and `upload` get the owner's `cache_scope`; any other kind raises `ScopeRefused`, so a new
   kind is never shared by mistake. Both studio paths use it (the live source,
   `design.track_spotdown`; shadow mode, `design.track_shadow`, is deleted, #1355), and the mix path
   (`design.mix_spotdown`) asks it for `mix`. The request builder still refuses an upload, a sha256
   lookup or an upload's analysis without a scope (`request_model`, `client`), so nothing private
   or shared ever goes scope-less.
3. **What crosses owners:** only the studio track's analysis in spotdown (its document, keyed by the
   file's sha256) and the uploads and jobs of that scope (an upload lasts at most 1 h). Each job's
   `client_ref` is a random id and its `Idempotency-Key`s come from the job, so no owner id,
   reference, title or token is in the shared scope. classbeat's own `track_cache` stays per owner
   (ADR 0002): a second owner still downloads the track and stores its own row; only the
   spotdown analysis is reused (no upload, no new analysis).
4. **Account deletion:** `DELETE /v2/scopes/{scope}` still deletes the owner's scope (mixes and any
   future upload). The shared studio scope is never deleted for one account, by design: it holds
   a catalog file's analysis and no user data.
5. **Retention: the shared scope is kept until an operator deletes it.** To spotdown it is a
   scoped entry like any owner's, and spotdown's retention only ever selects global rows (`main`
   at 94b1ee7: `retention_store` filters `cache_scope IS NULL` and `scope_key = ''`; contract v2,
   "Retention of global analyses": "the purge never touches a scoped one"). So **none** of these
   reaches it: the purge on an analyser version change, the 90-days-unused purge of the "#1296
   accepted" amendment (item 5), the takedown (it accepts only a global `youtube:` reference), or
   an account deletion. Its analyses, its job and idempotency records and any upload row the
   upload TTL sweep misses stay. This is a third retention class, beside owner scopes (they end
   with the account) and global entries (90 days unused or a version change).
   - **Consequence:** an analysis made by an older `analyzer_version` is never hit again (a hit
     needs an equal sha256 **and** `analyzer_version`), so a stale entry never reaches a class. It
     only stays in spotdown's database. The scope holds catalog-file analyses, random `client_ref`s
     and hashed `Idempotency-Key`s, no user data, so the lasting entries are a storage and
     hygiene cost, not a privacy one.
   - **Operator deletion:** `DELETE /v2/scopes/<value>` with the service key removes everything
     of the scope (the next studio track then uploads and analyses again). The operator computes
     the value in the worker container, which holds `app_secret_key`:
     `python -c "from classbeat.config import Settings; from classbeat.spotdown.scope_key import scope_key; from classbeat.spotdown.scope_calc import shared_scope; print(shared_scope(scope_key(Settings()), 'studio'))"`.
     The value is an opaque identifier, not a key. It is still not pasted into an issue or a log:
     with the service key it names the shared scope.
   - **Rotation:** rotating `app_secret_key` (the `cache_scope` key, §7) orphans the shared scope
     **for good**, like every owner's old scope. Nothing in classbeat records the old value and
     spotdown never purges it, so an operator computes and deletes it with the old key **before**
     the rotation.
   - **Follow-up:** a lifetime for scoped entries that are never deleted, or an operator
     procedure for them, is magpern/spotdown#40 (owner-gated, like every spotdown phase).

Tests: `test_spotdown_scope_policy.py` (the table over kinds, an unknown kind refused),
`test_spotdown_scope_calc.py` (the shared scope's form), `test_spotdown_client.py` (no scope-less
upload or lookup), `test_spotdown_studio_job.py` (a second owner
reuses the first's analysis without an upload; account deletion leaves the shared scope) and
`test_spotdown_mix_job.py` (mixes stay per owner, unchanged).

### Amendment (2026-11-21, owner): the explicit flag, report only (#1340; extends section 6)

Spotify's track `explicit` flag is kept from the search hit (classbeat reads the `search` answer through the pinned library's own reduction and keeps the one extra field, so no library bump) through the pick (`MatchedPool`) and sent to spotdown as the strict boolean `explicit` on a `/v2/match` item and on `POST /v2/analyses`, **only when known** (spotdown contract 6.4, `content.explicit`). The flag is recorded per track in the song list's provenance (`explicit`, record v1, beside `spotdown_analysis`; spotdown's own `content.explicit` is kept beside Spotify's) and a song shows a small "Explicit" tag in the class view when either source says yes. **Nothing is blocked, filtered, reordered or down-ranked, no model sees the flag, unknown shows nothing, and an error in the report never fails a pick or a job** (only RunStopped, LeaseLost and DatabaseRoleError pass). A kept song of a "Change songs" list carries its parent's evidence for its slot. Not covered, a follow-up: the composer, studio and from-playlist lists have no flag and show no tag. The tag is the Material Symbols `explicit` glyph (Apache-2.0) as an inline SVG; lucide-react has no equivalent boxed "E".

### Amendment (2026-11-21, owner sign-off on #1299; #1351): spotdown is the only analysis path (supersedes the fallbacks of §1, §8, §9 and the stage amendments)

The owner answered #1299 on 2026-11-21
([comment](https://github.com/magpern/classbeat/issues/1299#issuecomment-7066271875)): "yes, for
every source, now", and "there is no old or new way. There is one way. App has never run, so
anything old.. well just gone with it." The Architect's removal plan is on #1306. This amendment is
the spec for the code PRs listed there (item 8); they implement it and do not change it without the
Architect recording why. It changes no code itself. Each decision is stated once, here; the earlier
sections and amendments carry a one-line pointer and are otherwise kept as history.

1. **One analysis path.** spotdown is the only music-analysis path for every source: SwedeBeat
   studio tracks, SwedeBeat mixes and Spotify classes. There is no fallback, no setting that selects
   between spotdown and a local or MOWL-based analysis, no waiting period and no evidence gate.
   spotdown is now a hard dependency of every class with music (this replaces §1's and §8's "spotdown
   is never a hard dependency"); a class without music never calls it and is unaffected. Tuning
   happens after deployment on a test server, from the owner's feedback.
2. **Settings removed, none replaced:** `CLASSBEAT_SPOTDOWN_STUDIO`, `CLASSBEAT_SPOTDOWN_STUDIO_OWNERS`,
   `CLASSBEAT_SPOTDOWN_MIX`, `CLASSBEAT_SPOTDOWN_MIX_OWNERS`, `CLASSBEAT_SPOTDOWN_SPOTIFY`,
   `CLASSBEAT_SPOTDOWN_SPOTIFY_OWNERS`, `CLASSBEAT_SPOTDOWN_SHADOW` and `CLASSBEAT_SPOTDOWN_SOURCES`.
   classbeat always uses the `upload` source for SwedeBeat audio and `youtube` for Spotify picks; the
   deploy-time switch for YouTube sourcing is spotdown's own `ALLOW_YTDLP_SOURCING`, the owner's
   (the amendment "#1296 accepted", item 2). **Kept:** `CLASSBEAT_SPOTDOWN_URL`, the
   `spotdown_service_key` secret, `app_secret_key` (the `cache_scope` key, §7), and
   `CLASSBEAT_SPOTDOWN_MIX_CONCURRENCY` with the job's `spotdown_slot` (ADR 0005 §2, #574). The slot
   now bounds concurrent fetch-and-relay jobs and their disk, not a local decoder. *(Renamed by
   migration 0080, #1465: `analysis_slot` became `spotdown_slot`, queue v6; the `requeued` event value
   `analysis_slot` keeps its name.)*
3. **The MOWL boundary.**
   - **Kept, as data MOWL gives us (not an analysis path we own):** MOWL's map of a Spotify class's
     playlist as the **frame** of the played recording (track order, MOWL's length, Spotify's BPM),
     into which spotdown's sections are rebuilt by the library's `mowl.spotify.build_map` (stage D,
     item 2; that function stays); and MOWL's sections as **anchors** for `spotdown.anchor_calc`.
     An anchor can only move a track to `unusable` (a `disagree` makes the song missing, item 5); it
     never makes one `exact` (§3).
   - **Retired:** MOWL's sections as the design's timing of a track ("the MOWL-sections fallback for
     Spotify classes", #1299's list). No design path times a track on MOWL's sections.
   - **Retired:** the analysis of `design/mixanalysis.py`, `design/mixchild.py`, the local half of
     `design/track_analysis.py`, the `local` branches of `track_cache`, `mix_cache`, `mixadmit` and
     `spotdown.studio_policy`, shadow mode (`track_shadow`, `track_shadow_rows`, the `shadow_*`
     modules, the `spotdown_shadow_tracks` table), and the `local` analyser rows. ADR 0036 A7 step
     3's "track with no stored timing decision" (a local analysis) therefore no longer arises for a
     new design; the rule stays as written.
   - **Kept, not analysis:** `studio/render.py` and `studio/renderchild.py` (rendering a mix needs
     numpy, soundfile and soxr); `design/sandbox.py`, `design/rss.py` and the child limits they use,
     (already modules of their own; `studio/render.py` has its own runner, so nothing moved when #1357 deleted `mixanalysis`); the MOWL temporary-link fetch
     (`design.mixfetch`) and the 250 MB and 60 MB caps; the queue SQL.
4. **Outage and failure behaviour: no silent degraded analysis.**
   - **`analysis_unavailable`**, a new job category, **retryable**: in `jobs.errors.RETRYABLE`, with
     ADR 0005 §4's backoff and the job's attempts. The user sees "Music analysis is not available
     right now" with the automatic-retry and try-again copy (en, sv, da). It is the outcome, for the
     whole job, of:
     - spotdown not installed in the worker (no URL or no key) or unreachable: an `Unreachable`
       (`spotdown.failure_policy`) whose reason is about the service or its deployment, not about this
       music: `not_configured`, `source_off` (until `CLASSBEAT_SPOTDOWN_SOURCES` is removed, item 2),
       `source_disabled` (spotdown's YouTube sourcing is off, `ALLOW_YTDLP_SOURCING`: retried, since
       the owner may switch it on), `blocked`, `connection`, `unauthorized` (a wrong service key, an
       operator error) and `server_error`;
     - a provider outage (§8's threshold: ≥ 50 % of a batch and ≥ 3 tracks), a `provider_error`
       still there after its one retry on a studio track or a mix, a `rate_limited` wait that does
       not fit the budget, the wait budget spent, or the mix's 180 s wait cap;
     - on a Spotify class, a `rate_limited` read in phase 2 or in a revise carry, or one that is
       unreachable for one of the reasons above (stage D item 4, stage E item 4).
   - **A fresh budget per counted retry.** The wait budget (600 s) and the outage flag are reset
     when a run ends in a retryable category (the job goes back to `queued` with backoff, ADR 0005
     §2) and when Approve starts a new phase; the next counted claim then starts with a full budget
     and a cleared flag. A run that is requeued without counting an attempt (a shutdown hand-back,
     `operation_busy`, an analysis-slot requeue) keeps the budget it had. So does a lease-lost
     requeue, which counts an attempt but ends in no retryable category (recorded in #1352, review of
     #1360: the budget and the outage flag are kept, and at worst the next attempt ends
     `analysis_unavailable` and is retried with a fresh budget). The reset is never keyed on
     the `attempt` number alone: it counts per phase and Approve sets it back to 0, so phase 2's
     attempt 1 must not inherit phase 1's. Today both span every run of the job
     (`spotdown_studio_budgets`, `spotdown_song_budgets`), which would make a retry fail at once.
   - **`analysis_failed`** (existing, not retried) is the outcome when spotdown answered but the
     music cannot be analysed: a studio track or a mix refused (`processing_failed` after its retry,
     unsupported, too large, a refused or unmappable document), which includes the `Unreachable`
     reasons about this request or its answer, `too_large` (a 413 or an answer over its cap),
     `rejected` (any other 4xx, such as a 404 for an analysis that is gone) and `bad_answer`; on a
     Spotify class, too few songs with a usable spotdown timing after the spares and the 3
     replacement rounds (item 5), or a
     recorded analysis that is gone or no longer verifies at phase 2 (the user changes songs).
   - **`conflict`** (a 409: an `Idempotency-Key` used for another body) is classbeat's own bug: it is
     `internal`. Every `Unreachable` reason therefore maps to exactly one category.
   - **Any other exception** in the spotdown code is no longer swallowed into a fallback: it is
     `internal`, as everywhere. `RunStopped`, `LeaseLost` and `DatabaseRoleError` pass as before.
   - Every category is logged by name only (never a URL, a scope, a sha256 or a title) and counted in
     the admin failure view (`admin/jobs.py`, which already groups by category).
5. **The Spotify per-track rule.** A song is usable only with a `matched` spotdown result whose
   analysis gives an `approximate` timing (stage B). `uncertain`, `not_found`, `source_unavailable`,
   a single track's `provider_error` after its retry, an anchor `disagree` and an `unusable` timing
   all make the song **missing**: spares first, then at most 3 replacement rounds (the existing
   `song_swap_calc` machinery), then `analysis_failed` (item 4). "MOWL leaves too few, so the missing
   ones are offered too" is removed. A stage whose output changes for the same input bumps its record
   or stage version.
6. **Versions.** The provenance's `POLICY_VERSIONS` (`spotdown_map_policy`, `timing_class_policy`,
   `track_fit_policy`), extended for a mix by `mix_map_model.MIX_POLICY_VERSIONS` (`mix_map_policy`)
   and for a studio track by `studio_map_model.STUDIO_POLICY_VERSIONS` (`cut_candidates_calc`),
   cover a map's content. Removing a fallback does not change a
   spotdown map, so the retirement does not bump them. The rows of the `local` analyser are deleted
   by migration, and the `analyser` checks narrowed to `spotdown`, in the deletion PRs.
   *As built (#1299): migrations 0075 and 0077 did that; migration 0081 then drops the
   single-valued `analyser` column and its checks, rebuilds the unique keys without it and makes
   `provenance` required. The code keeps no analyser type or branch.*
7. **Evidence.** The quality gates of §9 and plan §7 are engineering checks on the fixtures, not
   gates on shipping: none blocks a default (there is none left to switch) or a removal. The two hard
   rules of §9 stay rules of the code: no `exact` for a matched or YouTube source (§3), and no audio
   kept after a job. **Accepted loss:** spotdown's baseline-comparison mode ("≥ the baseline", the
   per-source table of §9) loses its classbeat baseline once the local path is deleted. CC-licensed
   fixtures with published annotations are a low-priority engineering check (the recommendation on
   #1306), not owner work; nothing waits on them.
8. **The PR plan** is on #1306, one issue per PR, in order: #1351 (this amendment), #1337 (a fake
   spotdown in the e2e stack), #1352 (`analysis_unavailable`), #1353 (studio tracks and mixes),
   #1354 (Spotify classes), #1355 (shadow mode and `CLASSBEAT_SPOTDOWN_SOURCES`), #1356 (the local
   studio-track analysis and its `local` rows), #1357 (`mixanalysis` and `mixchild`), #1358
   (librosa and numba, after a mowl-workouts change).

**What it supersedes** (each carries a pointer here): §1's "stay as fallbacks"; §2 step 5's "fall back" and §3's fall-through to MOWL's sections; §8's fallback
reactions, its "spotdown unreachable" row and the SD-P5 note's local fallback; §9's rollout per
source and its removal conditions; §14's #1298 and #1299 blockers; the "#1296 accepted" item 3; the
stage A item 4, SD-P7a, stage B items 2 and 4, stage D item 4 and stage E item 4 fallbacks; every
remaining "#1298 gates the default" (the "#1296 accepted" item 4, the SD-P7a preamble, the stage D and
E preambles, the stage E audit row and its e2e note); and the Alternatives and Consequences lines on the
local paths and the default switch. #1298 gates nothing (owner, 2026-11-21).

### Amendment (2026-11-22, #1407): the last MOWL-sections path, a design on the user's MOWL playlist (implements item 3 of "spotdown is the only analysis path" for `design_draft`)

Recorded with the code. Before it, a `design_draft` on a `mowl_spotify_playlist` (a running class,
the `draft` plan with no Spotify connection, an interpretation hand-off without song picking)
still timed its tracks on MOWL's sections, and #1406 had narrowed item 3's "a base without
`music.spotdown` is `music_changed`" to bases on a song list. Both gaps are closed here.

1. **The design.** `design.kind_music_step.music_step` always times a playlist on spotdown: after
   MOWL's analysis (the frame) and before the beat-one corrections, `songs.draft_spotdown` runs
   stage A and stage B on **every track of the map, in its order** (`songs.draft_list_policy`, then
   `songs.playlist_spotdown` as for an own playlist: no spares, no rounds) and stage D
   (`phase2_spotdown.listed_music`) on the record. A track spotdown cannot match, analyse, align
   (no Spotify track id) or time is `analysis_failed` (item 5: the user's playlist has no spare);
   spotdown unreachable, an outage, the budget spent or a `rate_limited` read is
   `analysis_unavailable` (item 4, with `outage_policy.log_message`); a map longer than a revision
   records (`MAX_TRACKS`, 200) is `invalid_input`; an unsuitable song is kept with a warning. A
   track MOWL has no BPM for yet keeps the library's 120 BPM guess (as `mowl.spotify.build_map`
   does) and is still listed in `unknown_bpm`; its sections are spotdown's. No path returns MOWL's
   sections to a design.
2. **Where the record lives.** The playlist has no `song_list` revision, so the draft keeps the
   stage-A and stage-B record itself: `music.spotdown = {tracks, record}` (exactly one of
   `song_list_id` and `record`; `record` is `{spotdown_match, spotdown_analysis}` as a list's
   provenance holds it, at most 384 KiB as canonical JSON: the worst case of 200 tracks at realistic
   field widths is about 335 KiB, derived in `revision_spotdown_model`; a record over it, which only
   adversarial text can make, is refused `invalid_input` right after stage B, before stage D and
   before any draft revision is stored; only stage A's wait budget, `spotdown_song_budgets`, is
   kept, as for any failure after stage A, #1438). This amends stage E item 1's "never an
   analysis id" for this variant only, as a mix draft's `music.provenance` already names its
   analysis (#1304): analysis ids, versions and audio identities, never a title, a Spotify id, a
   source reference, a scope or a URL. A class copy (#550) keeps a `spotdown` that has its own
   record (it names no other row of the source class) and still drops one that names a song list.
3. **Its revise and refit.** The base's `music.spotdown` is carried (`songs.spotdown_carry`): when
   the music MOWL analyses now has the base's fingerprint and the base had every track on
   spotdown's sections, every recorded analysis is read again and verified exactly as stage D does,
   and the new revision keeps the same record. When the user changed the playlist since (another
   fingerprint), a **revise** runs the design's stages A, B and D on the new music
   (`music_step(..., fresh_on_change=True)`), records the new record and tells the model "the
   music changed" as before; a **refit** (beat-one corrections for the base's music) ends
   `music_changed` before anything is sent to spotdown. A base on a song list keeps stage E's rule:
   other music than the list's is `music_changed`.
4. **The literal rule.** `design.spotdown_base.base_sections` makes **every** Spotify base without
   `music.spotdown` `music_changed`, whatever it was designed on; only a mix base reads nothing.
5. **A running class (or any class) whose stored base has no `music.spotdown`** was designed on
   MOWL's sections, before this amendment. Decision (the owner's "app has never run, anything old
   is gone"): there is no migration and no fallback. Its revise and refit end `music_changed` (not
   retried, no revision, no credit for the revise or refit); the user designs the class again (a
   new `design_draft` on the same playlist, which runs stages A, B and D), and that design's
   revisions carry the record from then on. Save, the beat-one page and a copy of such a draft are
   unchanged; only the jobs that would design or fit on its timing refuse it.

## Status

Accepted. §12 took effect on 2026-11-20, when the owner accepted #1296.
