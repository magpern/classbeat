# ADR 0006: Agent runs

Status: Proposed

## Context

Roadmap §5 fixes the agent contract: a provider-neutral loop over a `ModelProvider` interface,
Anthropic first, stages with tool allowlists, no write tools, strict schemas, hard limits and
provenance per revision. M3a builds it with one stage, `design`, on music the user already has
in MOWL, plus the ~15-case smoke set of §11. [ADR 0005](0005-jobs-and-leases.md) gives the job
around a run: the lease and fence on every write, cancellation checkpoints, error categories,
events and external operations.

What the data rules allow: tokens never go to a model (AGENTS.md); for the beta circle,
Spotify-derived track metadata and MOWL's analysis may be sent to the model, and tokens, emails
and account data never are (roadmap Amendment 1, [ADR 0010](0010-live-activation-permissions.md));
everything Spotify-derived that we keep is deleted with its class or account.

Facts from `mowl-workouts` v0.4.0 this relies on: `workout.validate(spec)` raises `ValueError`
with a readable message for anything the MOWL app would refuse; `workout.tss(spec)` and
`sync.fit_to_music(spec, map)` are pure functions; `library.summary(entry)` gives a compact map
(tracks with title, artist, BPM, start and length; drops and breakdowns) meant for a model;
`library.spotify_map` attaches the playlist in MOWL (a write, `api.attach_spotify`) whenever it
has no cached analysis **and** link, then reads tracks, availability and Spotify's sections
through MOWL; `music.MAP_VERSION` versions the analysis.

Amendment (#105): the design pipeline is built against `mowl-workouts` **v0.5.0**, which adds
to the above: Spotify maps model MOWL's Spotify crossfade (`spotify.CROSSFADE_SEC` = 9 s of
overlap; `spotify.with_crossfade(map, sec)` converts a stored map without a `crossfade` key,
whose tracks are back to back); `library.spotify_analysis` re-reads tracks for up to
`library.BPM_WAIT_SEC` (120 s) while MOWL reports a BPM of 0, and `spotify.unknown_bpm(map)`
names the tracks still lacking one; `quality.report(wanted, fitted, map, timeline)` is a pure
ride-quality report; `workout.save(..., program_id=...)` raises `MowlError` if MOWL answers with
another program id. `music.MAP_VERSION` stays 12 on purpose.

## Decision

### 1. The loop and the `ModelProvider` interface

`classbeat.agent` holds the loop; adapters live in `classbeat.agent.providers`. The loop owns
stages, tools, limits and validation; an adapter only turns one request into one response.

```python
class ModelProvider(Protocol):
    name: str                                   # "anthropic", "fake"
    def start(self, spec: StageSpec) -> Conversation: ...

class Conversation(Protocol):                   # one stage run; append-only history
    async def turn(self, tool_results: Sequence[ToolResult]) -> Turn: ...

@dataclass(frozen=True)
class Turn:
    stop: Literal["tool_use", "end_turn", "max_tokens", "refusal", "other"]
    text: str                                   # visible text, kept for the transcript only
    tool_calls: tuple[ToolCall, ...]            # (call_id, name, input as a parsed dict)
    usage: Usage                                # input, output, cache_read, cache_write tokens
    model: str                                  # the model that actually answered
    request_id: str | None
```

`StageSpec` carries the stage's stable system prompt, its tool definitions (names, descriptions,
JSON schemas from the Pydantic models), the first user message, `model`, `effort` and
`max_tokens`. The adapter keeps the provider's native message list itself and only ever appends
to it: provider blocks the loop doesn't understand (thinking blocks) go back unchanged on the next
turn. `OpenAIProvider` and DeepSeek (M3c) implement the same two methods.

**One run** of a stage: `conv = provider.start(spec)`, then repeat: checkpoint (ADR 0005 §4:
cancel, lease, limits); `turn = await conv.turn(results)`; record usage (fenced, §6); on
`tool_use` run each call through the dispatcher (§3) and collect results; on `end_turn` without
the stage's final tool, send one tool-less user reminder ("finish by calling `submit_draft`"),
which counts as a turn; a second one ends the run `invalid_output`. `max_tokens` and `other` end
it `invalid_output`; `refusal` ends it `model_refused`. Parallel tool calls in one turn are run in
order and answered together in one message.

### 2. `AnthropicProvider`

- **SDK:** the official `anthropic` package (pinned, audited), `AsyncAnthropic(api_key=<secret
  file>, http_client=<the allowlisted httpx.AsyncClient of §9>, max_retries=2,
  timeout=120)`. API keys only; no CLI, shell, file, web or code-execution tools are ever
  declared. The SDK's own retries stay on (what they mean for usage: §6). `max_retries=0`, so
  that only the job retries (ADR 0005 §4), was considered for #121 and not taken: every
  transient 429, 529 or 5xx would then end the stage run, and the job's next attempt would start
  the stage again from its first turn after a backoff, re-sending and re-billing every earlier
  turn and spending one of `max_attempts`, which costs more than the usage gap it closes.
- **SDK version** (decided for #120, 2026-10-29; review by **2027-01-31**): `anthropic==0.125.0`,
  the last 0.x release (2026-08-19) and the last on `httpx`. It gets no further fixes: 1.0.0
  (2026-08-20) and 2.x (2026-10-21) require `httpx2`. We stay on 0.125.0 for now, because
  `classbeat.egress` and the egress guard (static reachability and runtime) are built on httpx,
  and moving means rebuilding the allowlisted transport on httpx2 (host, scheme and port, the
  per-hop redirect check, `trust_env=False`, the `refused` hook so refusals aren't retried,
  `after_request`) and reviewing it again, while keeping httpx2 out of the web process and out of
  Authlib's OAuth client (the `mcp` exclusion: `httpx2` replaced httpx inside Authlib); 0.125.0
  has no known advisory (pip-audit is clean) and has everything the provider uses (streamed
  Messages, strict tools, adaptive thinking, `output_config.effort`). Dependabot ignores
  `anthropic` semver-major updates (1.x, 2.x) until the review. The move to httpx2 gets its own
  issue at the review date, or earlier if pip-audit reports an advisory against 0.125.0, the API
  needs something 0.x lacks, or Authlib (already warning that its httpx integration is
  deprecated for httpx2) needs httpx2 itself.
- **Request:** streamed (`messages.stream(...)` then `get_final_message()`), so long turns don't
  hit HTTP timeouts. `model` default `claude-opus-5-5`, `output_config={"effort": "medium"}` set
  explicitly, `thinking={"type": "adaptive"}`, `max_tokens` 32 000, `tool_choice={"type":
  "auto"}`. No sampling parameters (rejected by current models), no `eager_input_streaming`
  (the loop acts on a tool call only once the whole answer is in). Forced tool choice (`any` or a
  named tool) is rejected by Opus 5.5, so the final tool is required by the prompt and by §1's
  reminder rule instead.
- **Tools:** every tool has `strict: true` and an `input_schema` with `additionalProperties:
  false` and `required`; the loop still validates every input itself (§4).
- **Caching:** `cache_control: {"type": "ephemeral"}` on the system prompt block, on the last
  tool definition and on the last block of the newest message (set on the request copy only, so
  the stored history carries none and the total stays at three of the API's four). That makes the
  brief, the music and earlier turns cache reads from the second turn on. The system prompt and tool list are byte-stable per stage and prompt version
  (no dates, ids or user data in them); the brief and music go in the first user message. A test
  checks that two runs of a stage send identical `system` and `tools`.
- **Fallback:** the server-side fallback (`fallbacks: "default"` with its beta header) stays
  **off** in M3a: a refusal ends the run `model_refused`, and the model that answered is always
  the one configured. A refusal on a class brief is rare, and an automatic switch to another model
  would bypass the per-model gate of §11 (models are enabled only after passing the regression
  suite). M3c revisits it once other models have passed.
- **Errors**, from the SDK's typed exceptions, most specific first, never from message text:
  `RateLimitError`, `InternalServerError` and other 5xx or overloaded statuses, `APITimeoutError`,
  `APIConnectionError` → `model_unavailable` (retryable, ADR 0005 §4);
  `AuthenticationError`, `PermissionDeniedError`, `NotFoundError` → `model_config` (an operator
  problem: key or model id); `BadRequestError` and anything else → `internal`. Logs get the class
  name, status and `request_id`, never the exception text or a request or response body (they hold
  the brief and music data).
- **Usage and cost:** from `usage.input_tokens`, `output_tokens`, `cache_creation_input_tokens`
  and `cache_read_input_tokens`, priced by an editable price table (`classbeat.agent.prices`,
  per model: input, output, cache write, cache read per million tokens; seeded from Anthropic's
  published list when A2 is implemented; admin-editable in M5). Cost is kept in micro-dollars.

### 3. Stages, tools and allowlists

A **stage** is a system prompt, a tool allowlist and one final tool. The dispatcher refuses any
call to a tool outside the stage's list (an error result, counted against the limits). Tools are
of two kinds only: **read** (data the backend loaded for this job) and **compute** (pure
functions). No stage has a write tool; external writes happen only in the backend, around stages
(ADR 0005 §7).

| Stage | Milestone | Tools | Ends with |
|---|---|---|---|
| `design` | M3a | `get_music` (read), `preview_draft` (compute) | `submit_draft` |
| `choose_songs` | M4 | `get_pool` (read), `check_song_list` (compute) | `submit_song_list` |
| `pick_pool` | M4 | `search_tracks` (read), `check_pool` (compute) | `submit_pool` |

M4 specifies its two stages in its own ADR within this contract.

**`design_draft` pipeline** (ADR 0005 §2):
1. **Music** (backend, before any model call). Look up the user's link in `mowl_playlist_links`;
   if there is none, attach the Spotify playlist in MOWL as the external operation
   `mowl_attach_spotify_playlist` (ADR 0005 §7) and store the link. Then build the analysis with
   **read-only** calls through `classbeat.connections`: a new `mowl-workouts` function (v0.4.1)
   `library.spotify_analysis(creds, playlist_id, spotify_id)` (the read half of `spotify_map`,
   which then calls it, so the CLI keeps working). Each library read (tracks, availability, one
   per track's sections) is a cancellation checkpoint (ADR 0005 §4). The analysis lives in memory
   for the run only.
2. **`design` stage** with the brief and the music summary of §7.
3. **Store** (fenced): the final `WorkoutDraft` as a `draft` revision with music and provenance;
   job `completed`.

**The attach operation.** M3a's one external-operation kind, `mowl_attach_spotify_playlist`, wraps
a Spotify playlist in a MOWL playlist (the library's `attach_spotify`). The user's choice of that
playlist in the new-class form is the user write's authorization (roadmap §2's write table: the
form says classbeat will add the playlist to their MOWL music). Its result goes to
`mowl_playlist_links`, so later jobs on the same playlist need no write. Its reconciler has no
read that maps a Spotify id to a MOWL playlist, so it always returns inconclusive: an uncertain
attach waits for the user, and at worst **Do it anyway** adds a second wrapper to their own MOWL
music. Whatever marks an attach `succeeded` (the worker or **Check again**) also writes its
`mowl_playlist_links` row in the same transaction. M3b adds the MOWL saves with real reconcilers.

**MOWL disconnect** deletes the user's `mowl_playlist_links` rows and their
`mowl_attach_spotify_playlist` operations in the disconnect transaction (classbeat's local data
about that MOWL account goes with the connection, as Amendment 1 has Spotify-derived data go
with its class or account), so reconnecting as another MOWL account never reuses a link or an
`external_id` from the old one. The delete is the mechanism; rows are not scoped by connection
id, because a reconnect keeps the row id (ADR 0004 §4). Amendment (#113): **reconnecting MOWL in
place** (the same connection row rewritten, no disconnect) deletes them the same way, as do the
keys wipe and a disconnect: the row records no MOWL account identity, so classbeat cannot tell the
same account from another one. Undecided `uncertain` attach rows go too, so the next attach of
that playlist may add a second wrapper to the user's MOWL music; U1's reconnect and disconnect
dialogs say so.

**Hidden stale `pending` attach rows** (#117 item 7): when a design job's holder is reaped with
its attempts spent while the attach row is still `pending`, the row is not listed (only
`uncertain` rows are) and Check again / Do it anyway answer 409 `not_uncertain`. The designing
page treats an empty list as honest: it explains that nothing is offered here and points at MOWL
disconnect/reconnect to forget classbeat's record. A later design on the same playlist marks the
row `uncertain` through the normal protocol, or the user clears it by reconnecting MOWL.

**Holder of an operation row** (ADR 0005 §7 step 1, amended in #125). The pair `(job_id,
lease_version)` names a holder only while the row is `pending`. A job that marks a stale `pending`
row `uncertain`, or meets an `uncertain` row, sets its `job_id` to itself in the same fenced
transaction, so the job that fails `uncertain_write` lists the decision; `lease_version` keeps the
token of the call. For `uncertain` rows, and rows resolved after another job adopted them, `job_id` therefore no
longer names the job that made the call.

**The tools in `design`:**
- `get_music()`: the music summary again (so the model can re-read it after corrections).
  `model_input` builds it by **projecting** `library.summary` down to §7's fields: its
  `spotify_id`, `playlist_id` and MOWL's `unavailable` texts are dropped (unavailable tracks are
  simply absent). Tracks are referred to by their **index** in that list, never by an id.
- `preview_draft(draft)`: §4's validation, then `sync.fit_to_music(spec, map)`, then returns the
  fitted timeline (segments and intervals with start times and the track under each), the warnings
  `service.prepare` would give for a Spotify source, the total duration and the TSS, and the
  **quality report** `quality.report(wanted, fitted, map, timeline)` (changes on the music, the
  longest run in one zone, and issues: standing over 90 s, standing outside 60-80 rpm, cadence
  changes of more than 15 rpm inside a song, `"beat"` cadences that fell back off the beat), or
  the list of errors to fix. Pure: no network, no credentials, no database. The report's issues
  are warnings, never refusals: they let the model fix the draft before submitting and never block
  `submit_draft`. The library's Spotify warning text that tells the user to set `"crossfade": 0`
  is reworded or dropped before it reaches the model or the UI, because a classbeat user has no
  such setting.
- `submit_draft(draft)`: the same checks; on success the stage ends with that draft (after
  `fit_to_music`), on failure the errors come back and count as a correction round (§5). It also
  returns the quality report, as a warning only.

**Crossfade.** The map's crossfade is the library default, `spotify.CROSSFADE_SEC` = 9 s, for the
10 s Spotify crossfade MOWL's how-to asks riders to set. In the beta it is a **constant default**,
not a setting: a per-user setting (for a rider whose device has crossfade off, which would use
`spotify.with_crossfade(map, 0)` before fitting) is deferred until a tester needs it. A stored
analysis without a `crossfade` key (made under v0.4.1) is converted with
`spotify.with_crossfade(map, spotify.CROSSFADE_SEC)` wherever a stored map is used (`get_music`,
`preview_draft`, `submit_draft`). Analyses with BPM still 0 for some tracks
(`spotify.unknown_bpm(map)` not empty) are not stored as shared: the next job analyses again, and
the user is told which tracks have no BPM yet.

TSS, duration and the fitted timing are always computed by the backend from the draft; a number
the model states is never stored. `workout.validate()` runs again server-side before the revision
is stored, and M3b runs it again before any save.

### 4. Schemas

`classbeat.agent.schemas` has strict Pydantic v2 models (`extra="forbid"`, `strict=True`) whose
JSON schemas are the tool `input_schema`s:

- **`WorkoutDraft`:** `name` (1-100), `description` (≤ 500), `segments` (1-20), each with `name`
  (1-60), `kind` (`warmup`, `main`, `recovery`, `cooldown`) and `intervals` (1-60), each with
  `sec` (int 1-3600), `zone` (1-7), `rpm` (`[from, to]` ints 0-200, or `"beat"`), `standing`
  (bool), optional `game` (the library's `GAMES`), optional `ftp_test` (3, 5, 20), optional
  `cue` (≤ 120). It converts to the library's spec dict; `workout.validate()` stays the authority
  on the MOWL app's own rules (cooldown, games, FTP tests, three hours), and its message is
  returned to the model for correction.
- **`SongList`** (M4): songs (1-40), each `track` (an index into the analysed pool), `role`
  (an enum M4 fixes), with the list's total checked by the backend.
- Every tool input is capped at 32 KB before parsing; a tool result is capped at 32 KB (the music
  summary is truncated by whole tracks with a note if longer). Text fields in model output are
  stripped of control characters before they are stored.

### 5. Limits

Per run, all settings with these defaults; the loop checks them at each checkpoint, before the
call that would exceed them:

| Limit | Default | When hit |
|---|---|---|
| Model turns per stage | 12 | `limit_exceeded` |
| Tool calls per stage | 40 | `limit_exceeded` |
| Correction rounds (`submit_*` refused) | 3 | `invalid_output` |
| Wall time per stage | 300 s | `limit_exceeded` |
| Cost per job, across stages and attempts | $1.50 | `limit_exceeded` |

**The cost check** runs before each turn: spent cost plus the price of the conversation so far as
input plus a per-turn output reserve (`CLASSBEAT_AGENT_TURN_OUTPUT_RESERVE`, default 8 000
tokens, thinking included) must stay under the ceiling. Reserving the full `max_tokens` (32 000,
about $0.64 of output on Opus 5.5) would let the $1.50 ceiling stop a run after one or two
turns; with the reserve a turn can overshoot the ceiling by at most `max_tokens` minus the reserve
(about $0.48), once. **The ceiling wins over the turn limit:** at the defaults a design run fits
about six to eight turns before the cost check stops it, which the fake scripts' usage numbers
exercise. S1's live smoke runs measure the real cost per run, and the defaults (ceiling, reserve,
turns) are set from them before live activation; until then these numbers stand. A run stopped
by a limit stores nothing but its `agent_runs` row and the failure; the user may start again. The
per-user job limit (ADR 0005 §2) bounds concurrency; quotas are M5's.

### 6. Records and provenance

**`agent_runs`** (owned; one row per stage run, written fenced): `id`, `owner_id`, `job_id` →
`jobs` (`ON DELETE CASCADE`), `stage`, `attempt`, `provider`, `model` (configured),
`model_answered`, `effort`, `prompt_version`, `turns`, `tool_calls`, `input_tokens`,
`output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cost_micros bigint`, `outcome`
(check: `submitted`, the ADR 0005 §4 categories, `cancelled`, `lease_lost`; NULL while the run is
in progress, with `(outcome IS NULL) = (finished_at IS NULL)`, amended in J1, #84), `started_at`,
`finished_at NULL`. Counters are updated after every turn, so a crashed run still shows its cost.
A turn that finishes after its worker lost the lease can't be recorded (the fence refuses it), so
the cost across attempts may **undercount by that one turn per lost lease**; the next holder does
not guess it. Likewise a request that fails after the provider started billing it returns no
usage, so nothing is recorded for it (amended in A2, #91), **except** where the adapter marks the
turn billed (`ProviderError.billed`, ADR 0012 section 3, #219): the loop then records it at the
turn's ceiling. That is so for the OpenAI-SDK adapters and for Anthropic once its stream has begun
(an error event, a read timeout, the turn bound, a cut connection). A cancel mid-stream stays
unrecorded. And
the SDK retries silently inside one turn (§2, `max_retries=2`: a connection error or a timeout
before the response headers, and 408, 409, 429 and 5xx answers; never once the stream has
started; the provider's `x-should-retry` response header can override that list for one answer,
forcing or forbidding a retry, which leaves the bound below unchanged); only the attempt that answers reports usage, so an earlier attempt the provider billed
(a timeout or a dropped connection after the request was sent) is lost **even when the turn
succeeds** (amended for #121). Bound: **at most two uncounted attempts per turn, and three for
the turn that ends a failed attempt**, each at most the conversation's input plus `max_tokens`.
Accepted: leases are lost rarely, a retried attempt is billed only if the provider processed it
before the failure, failed turns end their attempt, and the gaps are bounded; real cost
per class is measured against the provider's own figures at live activation (roadmap M3b).
No transcript is stored in M3a: model text, tool arguments and results stay in memory and are
never logged. (A debug transcript store, if ever needed, gets its own decision and falls under
the roadmap §6 purge.)

**`class_revisions.provenance`** (ADR 0005 §1), validated by a Pydantic model: `provider`,
`model`, `model_answered`, `effort`, `prompt_version` (SHA-256 of the stage's system prompt and
tool schemas), `classbeat_version`, `mowl_version`, `map_version` (`music.MAP_VERSION`), the map's `crossfade`
in seconds (`MAP_VERSION` stays 12 across v0.5.0, so `map_version` alone no longer tells a back to
back map from an overlapped one), the `agent_runs` ids and summed usage and cost. **`class_revisions.music`**: `{source:
"mowl_spotify_playlist", link_id, fingerprint, unknown_bpm}`, where the fingerprint is SHA-256 of
the analysed track list (per track in play order its Spotify track id and length; amended in D1,
#92: the library's map keeps no MOWL track ids), so a later revision can tell whether the music
changed, and `unknown_bpm` is `[{index, title}]`, the tracks MOWL had no BPM for yet (0-based, the
same index as in the model's music summary), so the user is told their bars were a guess; the
titles are the user's own track list and go with the class (ADR 0010).
Revisions are deleted with their class or account (Amendment 1).

### 7. What the model sees, and prompt injection

**Model input is built in one place**, `classbeat.agent.model_input`, from allowlisted fields
only:
- the brief: the guided options (length, intensity, climbs, sprints, races, FTP test, style) and
  the user's free text (≤ 2000 characters);
- the music summary: playlist name, per track its index, title, artist, BPM, start and length, and
  the drops and breakdowns with times (Spotify-derived metadata and MOWL's analysis, allowed for
  the beta circle by Amendment 1 and ADR 0010);
- the quality report of `preview_draft` and `submit_draft` (clock times, rpm, counts and issue
  codes only: `quality.report` is pure and carries no ids, titles or credentials);
- the current draft, when revising (M3b).

**Never:** tokens or credentials, the user's email, name or ids, connection data, MOWL or Spotify
ids (playlist, track, program), playlist URLs, MOWL's availability texts, anything from another
user, error texts from providers. A test builds a request with planted values in every one of
those places (the fake MOWL returns them) and asserts none appears in what the provider adapter
would send.

**Stance.** Song titles, artist names, playlist names and the user's free text are untrusted
(T8). Consequences, each tested with an adversarial fake script:
- **Tool output is data.** Tool results are JSON with a fixed shape; the system prompt says that
  text inside results and the brief is content to design with, never instructions. This lowers the
  odds of steering; it is not relied on.
- **Nothing a steered model can do matters beyond a bad draft.** It has no write tool, no network,
  no ids to name (only indexes into data the backend loaded for this user), and its final output
  passes schema and library validation before it is stored; a draft is a proposal the user sees.
- **Tool arguments are validated** against the schema and the stage allowlist; an index outside
  the list, an unknown tool or an oversized input is an error result, not an exception.
- **Limits stop loops** (§5); events and logs never carry model text (ADR 0005 §6).

### 8. The fake provider and the smoke set

**`FakeProvider`** (`classbeat.agent.providers.fake`) replays a **script**: a JSON file of turns,
each either a list of tool calls (with inputs, which may reference the previous tool result by a
small template, for example "the draft from the last error, with `sec` fixed") or `end_turn`, or
`refusal`, or an error to raise (`overloaded`, `timeout`, `auth`), with fixed usage numbers per
turn. It checks each tool result against the script's expectations when given and fails loudly on
a mismatch, so a script is also a test of the tools. Scripts live in
`backend/tests/agent_scripts/` and cover: a good first draft, a draft fixed after `validate()`
errors, an unknown tool, an out-of-range track index, an oversized input, a missing final call, a
refusal, an overload then success, a turn or cost limit, and injection text in a song title. The
worker uses it when `CLASSBEAT_MODEL_PROVIDER=fake` (never in `prod`, §9) with
`CLASSBEAT_FAKE_MODEL_SCRIPT`; the E2E stack runs that way.

**Smoke set** (`classbeat.evals`, cases in `backend/src/classbeat/evals/smoke/`, roadmap §11),
15 cases, each a JSON file (amended in S1, #95: in the package so the image carries them, and JSON
because the project has no YAML dependency): the brief,
a synthetic music fixture (no real Spotify data: invented titles and BPMs in the analysis
format), and checks on the stored draft: `validate()` passes, duration within 5 % of the asked
length and not longer than the music, one cooldown of at least 60 s, the asked sprints, climbs,
race or FTP test present, zone shares within bounds for the asked intensity, TSS in a range,
interval starts on downbeats after fitting, cost and turns under the limits. S1 uses
`quality.report` for the checks it covers instead of re-implementing them (interval starts on
downbeats as `changes_on_music`, standing and cadence limits as its issues), and as an input to
the M3c eval. Cases cover the
guided options, too short music, missing BPM or sections, a long free-text brief, and adversarial tool output. There is no
missing-music case: D1 fails a job without music before the stage runs.
- **In CI**, every case runs against `FakeProvider` with a recorded script, which tests the
  harness, the tools and the checks (no network).
- **Against Claude**, the owner runs `docker compose exec worker python -m classbeat.evals smoke
  --live` (the worker is the only container with the key) with the real provider on synthetic
  music only, outside CI, on every PR that touches prompts, tool schemas or the loop; the results
  table (pass per case, cost, turns, prompt version) goes in the PR. Neither CI nor an agent runs
  it. It refuses (exit 2, no key in any output) unless `--live` is passed, `CI` is unset (even empty), the
  provider is `anthropic`, the `anthropic_api_key` secret is present and the model is priced; it
  then prints its plan, the §5 limits and the **cost ceiling** (every case at the per-job ceiling
  plus one turn's over-run, $29.70 for 15 cases at the defaults) and stops, unless `--max-usd N`
  is also passed. `N` is a budget: each case runs with the money left as its job ceiling (what a
  case spent counts even if it ended in an error) and the run stops when none is left. The total
  can exceed `N` by one turn's over-run (up to $0.48 at the defaults), which the printed ceiling
  includes. `--case NAME` runs a subset, `--out FILE` writes the results as
  JSON (per run: model, effort, prompt version and repeat index; outcome, turns, tool calls,
  corrections, tokens, cost and wall time; TSS with its bounds and `changes_on_music`; every check
  with its detail; the shape of the class, segment names and per interval seconds, zone and track
  index; no cues, song titles, ids or secrets). Each failed check is printed with its detail.
  `--model` picks the model (Opus, Sonnet or Haiku 5.5, the priced ones; the printed plan and
  ceiling use its prices, Haiku's at the upper tier above 100 000 prompt tokens), `--effort`
  overrides `CLASSBEAT_AGENT_EFFORT` and `--repeat N` (1 to 10) runs each case N times, all cases
  once before any twice. The request is the same for the three models (no sampling parameters, no
  forced tool choice, adaptive thinking, the effort as configured; `tests/test_agent_anthropic_models.py`).
  **A model comparison run needs the owner's go-ahead for each run**; `python -m classbeat.evals
  compare RESULT.json ... --out DIR` reads the saved JSON files offline and writes `compare.md` and
  a self-contained, escaped `compare.html` (issue #134). It
  installs the worker's outbound allowlist before building the provider, and lives in
  `classbeat.evals`, which the web process never imports. It needs an Anthropic API key, which
  the owner provides (roadmap "Before M0"); until then live runs wait and the PR says so.
  Synthetic data and our own key touch no user account.

### 9. Worker secrets and outbound calls

- **KEK.** The worker already loads the key ring at start (ADR 0004 §2: `connection_kek` and the
  retired files as compose secrets; the check value must match). A run opens the MOWL connection
  with `classbeat.connections.open_connection(factory, ctx, keyring, "mowl")` when it first needs
  it, after checking the job's `mowl_connection_id` equals `ctx.connection("mowl")` and its status
  is `connected` (otherwise `not_connected`, ADR 0004 §3), and drops the `Credentials` when the
  run ends. They never reach job rows, events, SSE, model input, tool results or logs (ADR 0004
  §3). `key_unavailable` and `decrypt_failed` fail the job (ADR 0005 §4).
- **Model key.** A new secret file `anthropic_api_key`, mounted on **worker only** (web never
  calls a model), read as `SecretStr`, never logged. `CLASSBEAT_MODEL_PROVIDER` is `anthropic`
  (the default) or `fake`; the worker refuses to start with `anthropic` and no key file, and with
  `fake` when `CLASSBEAT_ENVIRONMENT` is `prod`. With `anthropic` it also refuses to start (and
  the provider to build) when `ANTHROPIC_LOG`, `ANTHROPIC_CUSTOM_HEADERS` or `SSLKEYLOGFILE` is
  set (A2; `SSLKEYLOGFILE` added for #121).
- **Outbound allowlist** (roadmap §7, T19). The worker installs, before anything else, a
  process-wide `urllib` opener whose handler refuses any request (including every redirect hop)
  that is not `https` to an allowlisted host on port 443, and builds the model client on an
  `httpx.AsyncClient` (`trust_env=False`) whose transport applies the same check; the opener has
  an empty `ProxyHandler`, so no proxy setting in the environment reroutes a call. Both verify
  TLS with a context built without `ssl.create_default_context()`, so `SSLKEYLOGFILE` never makes
  them write TLS session secrets (#121). M3a's list:
  `api.mowl.com`, `api.anthropic.com`; M4 adds the Spotify hosts. A refused request raises, logs
  the host only and fails the run with `outbound_blocked`. A guard test fails on any other HTTP
  client construction or `urlopen` in `classbeat` outside the allowlist module. The E2E fakes
  answer under these same names inside the compose network (as today's fake MOWL does).

## Alternatives

- **The SDK's tool runner** (`client.beta.messages.tool_runner`): less loop code, but checkpoints,
  fencing, per-call limits and provider neutrality need hooks into every step; a manual loop of a
  few dozen lines keeps them explicit and the same for every adapter.
- **Letting the model call MOWL through tools**: gives it credentials-backed reads it doesn't need
  and makes injection reach the network; the backend loads music before the stage instead.
- **Structured output (`output_config.format`) instead of `submit_draft`**: the model would lose
  the validate-and-correct loop; the final tool returns errors it can fix.
- **Using `library.spotify_map` as is**: it attaches the playlist on every cache miss, a write
  outside `external_operations`. Splitting out the read half keeps writes visible.
- **Persisting transcripts for debugging**: more Spotify-derived and model text to purge and
  protect; the smoke set and fake scripts reproduce problems instead.
- **Server-side model fallback on refusal**: rejected for now (§2).

## Consequences

- M3a ships one stage; M4 adds two within this contract, and M3c adds adapters behind the same
  interface. A new model is enabled for a tier only after the §11 suite passes.
- `mowl-workouts` v0.5.0 is needed before the design pipeline (`library.spotify_analysis`, the
  crossfade and `quality.report`).
- New dependency: `anthropic` (pinned, audited). New secret: `anthropic_api_key`, worker only.
- Threat model T54 to T57 follow from this ADR.

## M3a issue breakdown (ADR 0005 and this ADR)

One issue per PR, milestone "M3a Jobs and design". "Opus" marks a security-critical issue with an
Opus Developer (AGENTS.md); every PR gets a fresh Opus Reviewer.

| # | Issue | Owner | Depends on |
|---|---|---|---|
| E1 | `mowl-workouts` v0.4.1 (adopted at v0.5.0, #105): `library.spotify_analysis` (read-only half of `spotify_map`, which then uses it); release, rebuild the classbeat wheel | Sonnet | none |
| J1 | Jobs schema: migration for `classes`, `class_revisions` (immutable policy), `jobs` (one open job per class), `job_events`, `external_operations`, `mowl_playlist_links`, `agent_runs`; `20-queue.sql` (single transaction, superuser-only, role attributes, revoke-then-grant) with `classbeat_queue` and the five functions (claim skips spent rows, reap fails them); `db-functions` compose service (password through a run-time `PGPASSFILE`), worktree and CI wiring, a CI step regenerating the expected function `md5` against the pinned image; startup and per-connection queue check (`md5(pg_get_functiondef)`, ACL with the owner's entry); guard 1, guard 2 allowing `classbeat_queue` exactly its table privileges and `USAGE` on `public` (grantee check kept), new guard 9; stack check that `postgres_password` is absent from web, worker, migrate and maint; double-claim, poison-row, racing insert on one operation key (`ON CONFLICT DO NOTHING`, no unique-violation error) and cascade (`job_id` set to `NULL` on immutable revisions) tests | **Opus** | none |
| J2 | Jobs core: the `classbeat.jobs` issuer, kind registry, transitions with phases (`attempt` reset on approve), `fenced_session` and `LeaseLost` with the AST allowlist guard (the `classbeat.connections` status-write exemption), worker loop (claim only after the queue check passed, `LISTEN jobs_queued`, heartbeat with `GREATEST`, reap, expire, settle), retry classes and backoff, cancellation checkpoints and cancel-on-requeue, lease extension when writing `pending`, shutdown hand-back (`stop_grace_period: 30s`, no new call after SIGTERM, fenced requeue without an attempt under the 5 s `lock_timeout`; a call still in flight after 25 s left `pending`), `operation_busy` requeue without an attempt, `CreditLedger` seam with `UnmeteredLedger`; stale-worker, crash, lease-expiry, resume after a retried pre-approval phase, poison row not blocking other users, cancellation, shutdown and settle-once tests | **Opus** | J1 |
| J3 | Jobs API: `POST /api/jobs` with idempotency keys, the per-user limit and `class_busy`, `GET /api/jobs/{id}`, cancel, approve (all checks of ADR 0005 §2 including the per-user limit and connection status read through `classbeat.connections`, tested with the test-only kind); 404 tests | **Opus** | J2 |
| J4 | Progress: event payload models (`tool_called.error` an enum code), `pg_notify` on insert, the web `LISTEN` hub with polling fallback, `GET /api/jobs/{id}/events` SSE with `Last-Event-ID`, stream limits; payload-leak and foreign-id tests | Sonnet | J2 |
| X1 | External operations: the protocol of ADR 0005 §7 (per-kind keys and request hashes, `spotify:<source_id>` and `{kind, source, source_id}` for the attach, `SELECT … FOR UPDATE`, `INSERT … ON CONFLICT DO NOTHING` then re-load, conditional `failed → pending`, `operation_busy`), `Reconciler` seam, `mowl_attach_spotify_playlist` with `mowl_playlist_links` (`ON CONFLICT DO NOTHING`, also written by a successful **Check again**), **Check again** / **Do it anyway** endpoints (409 on a `succeeded` row), MOWL disconnect deleting links and attach operations, `queue_stats` CLI; tests with the fake MOWL: uncertain write, stale pending, two jobs claimed at the same moment on one playlist (no row and a `failed` row) giving exactly one attach and no `internal`, and an account switch (disconnect, reconnect as another fake account: no stale link or reused `external_id`) | **Opus** | J2 |
| W1 | Worker outbound allowlist (urllib opener with an empty `ProxyHandler`, httpx transport with `trust_env=False`, guard test), `anthropic_api_key` secret (compose worker only, worktree init, `docker inspect` check), `CLASSBEAT_MODEL_PROVIDER` startup rules | **Opus** | none |
| A1 | Agent loop core: `ModelProvider`/`Conversation`, stage runner with checkpoints and limits (cost reserve of §5, undercount after `LeaseLost` documented), dispatcher with allowlists and caps, `WorkoutDraft`, `model_input` builder projecting `library.summary` (no `spotify_id`, `playlist_id` or `unavailable` texts) with the planted-value test, `agent_runs` writes (fenced), provenance; `FakeProvider` and the adversarial scripts | **Opus** | J2 |
| A2 | `AnthropicProvider`: request shape of §2, caching stability test, error mapping, usage and price table (the cost arithmetic of §5 checked against it); tests with an `httpx.MockTransport` (no network) | **Opus** | A1, W1 |
| D1 | `design_draft` pipeline: the music step (link or attach through X1, then `spotify_analysis` with a checkpoint per read), `get_music`, `preview_draft`, `submit_draft` with `validate()`, `fit_to_music` and TSS, revision storage; `GET /api/music/mowl-playlists` (the user's Spotify playlists as MOWL lists them, names only; opens MOWL credentials in a web route); fake MOWL extended with playlists, tracks and sections | **Opus** | A1, X1, E1 |
| U1 | Frontend: new-class form (brief options, playlist choice with the "classbeat adds it to your MOWL music" line), Designing… page on SSE with resume, a read-only draft summary, failure and uncertain-write messages with their two actions | Sonnet | J3, J4, D1 |
| T1 | E2E with fakes: draft from a MOWL playlist (fake MOWL and `FakeProvider` in the worker), cancel while running, uncertain attach path | Sonnet | U1, W1 |
| S1 | Smoke set: 15 cases, synthetic music fixtures, the checks of §8, the fake-provider CI run and the `--live` command (live runs wait for the owner's key; their cost per run sets §5's defaults) | Sonnet | D1 |

Order: E1, J1 and W1 in parallel; then J2; then J3, J4, X1 and A1 in parallel; A2 and D1; U1 and
S1; T1 last. M3a is done when J2's crash, lease and stale-worker tests and T1 are green.
