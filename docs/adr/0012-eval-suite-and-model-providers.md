# ADR 0012: Eval suite, model providers and the enable gate (M3c)

Status: Proposed

## Context

Roadmap M3c: "Full eval suite (§11) incl. song-choice quality; OpenAI adapter; DeepSeek adapter
(disabled). Done when: models enabled per tier only after passing." Roadmap §5 asks for a
provider-neutral loop with `AnthropicProvider` and `OpenAIProvider` on the official SDKs and DeepSeek
through the OpenAI adapter, disabled until privacy, terms and eval are met; provider, model and effort
per tier are set in the admin. §11 asks for a versioned regression suite (standard classes, song
choice, music sync, revisions, games and FTP rules, missing music, adversarial tool output,
cost and efficiency) on fake music, with results per model and prompt version, and an "approximate"
warning on zone 7's TSS weight.

What exists (M3a, M3b, M4 so far):

- The loop and `ModelProvider` / `Conversation` / `Turn` / `Usage` / `ProviderError`
  (`classbeat.agent.provider`, ADR 0006 §1); `AnthropicProvider` (`anthropic==0.125.0`, the last
  httpx release, ADR 0006 §2, #120); `FakeProvider` replaying JSON scripts (§8).
- `provider_for(settings)` picks `anthropic` or `fake` from `CLASSBEAT_MODEL_PROVIDER`; the model
  and effort come from `CLASSBEAT_AGENT_MODEL` / `_EFFORT` for every job and stage.
- The worker's egress allowlist (`classbeat.egress`): `api.mowl.com`, `api.anthropic.com` and the
  Spotify hosts; the model client gets a one-host `MODEL_ALLOWLIST`.
- The smoke set: 18 design and revise cases (`evals/smoke/`), 3 song cases (`evals/smoke_pick/`,
  CI only: they need a searcher, which the live run does not wire), the deterministic checks of
  `evals/checks.py`, the owner-run `smoke --live --max-usd N` (`evals/live.py`), results JSON
  (`RESULTS_FORMAT = 1`) and the offline `compare` (#134; follow-ups in #196).
- Prices per `(provider, model)` in `classbeat.agent.prices` with `PRICES_VERSION`.

M5 (ADR 0011, designed in parallel) owns tiers, plans, quotas and the admin. The boundary agreed with
it: M3c owns the store of model routes and the gate (§5 below); M5 maps a user's plan to a
`route_key`, stores it in the job input at admit, and its admin calls this ADR's `set_route` inside an
admin request (step-up, its own audit row in the same transaction). The price table stays a reviewed
code change for both milestones (no price store).

Everything here must be built and tested offline. Live eval runs, and enabling a model for real users,
are owner-authorized steps (AGENTS.md safety envelope; roadmap "Verification").

## Decision

### 1. One catalog of models

A new module `classbeat.agent.catalog` lists every model classbeat may use, as reviewed code:

```python
@dataclass(frozen=True)
class CatalogModel:
    provider: Literal["anthropic", "openai", "deepseek"]
    model: str                       # the provider's model id
    efforts: tuple[str, ...]         # classbeat efforts it accepts (mapped per provider, §3)
    host: str                        # the one API host its client may reach
    data_policy: Literal["allowed", "owner_pending"]   # §6
```

The catalog is the only source for "which provider and model exist": the eval command's `--model`, a
route, the price lookup and the egress allowlist all read it. Each entry needs a price in
`classbeat.agent.prices` (a test fails otherwise). The OpenAI and DeepSeek entries and their prices
are filled in by their adapter issues from the providers' published model and price pages, read by
the developer (no API call), marked "verify before live use" like Anthropic's, with a new
`PRICES_VERSION`. `fake` is not in the catalog; it stays a worker setting for dev and CI.

### 2. Keys, settings and egress per provider

- **Secret files**, as `anthropic_api_key` (ADR 0006 §9): `openai_api_key` and `deepseek_api_key`,
  mounted on the **worker only**, `SecretStr`, empty file = none, created empty by
  `scripts/worktree.sh init`, never a real key in any repo file. The web app refuses to start when it
  can read any model key file (today's rule, extended). The `e2e` stack check replaces each with a
  random canary and asserts none appears in `docker inspect` and only `worker` mounts them.
- **Refused environment**: with any real provider the worker refuses to start (and the provider to
  build) when `SSLKEYLOGFILE`, any `ANTHROPIC_*`, any `OPENAI_*` or any `DEEPSEEK_*` variable is set.
  The SDKs read keys, base URLs, organisation ids and log switches from such variables; classbeat
  passes every one of them explicitly, so none is needed and any is a misconfiguration or a leak path.
- **`CLASSBEAT_DEEPSEEK_ENABLED`** (default `false`): with it off, the DeepSeek host is not on the
  worker allowlist, no DeepSeek provider is built for a job and `set_route` refuses DeepSeek (§5). The
  owner turns it on only after the conditions of §6 hold. The live eval of a DeepSeek model (synthetic
  data only, §4) does not need it; it needs the key and the owner's go-ahead for that run.
- **Egress**: `classbeat.egress` gets `MODEL_HOSTS = {"anthropic": "api.anthropic.com", "openai":
  "api.openai.com", "deepseek": "api.deepseek.com"}`. The worker allowlist holds a model host only
  when that provider's key file is present (and, for DeepSeek, the setting is on). Each provider's
  client gets its own **one-host** allowlist (as `MODEL_ALLOWLIST` today), so an OpenAI client can
  never reach DeepSeek's host or the other way round, even though both use the `openai` SDK. The
  guard test (`tests/test_egress_guard.py`) covers the new client constructions.

### 3. The adapters

Both new adapters implement ADR 0006 §1 unchanged: `start(spec) -> Conversation`, `turn(results,
reminder=) -> Turn`, `ProviderError(category)` with no provider text, an append-only native history,
usage per answered request. The loop, the stages, the dispatcher, the limits and `model_input` do not
change: the provider sees exactly what Anthropic sees today (the planted-value test runs against
every adapter's captured request body).

**Shared OpenAI-SDK layer** (`classbeat.agent.providers.openai_common`):

- **SDK**: the official `openai` package, pinned exactly and audited. It must be a release on
  `httpx` (not `httpx2`), for the reason of ADR 0006 §2: the allowlisted transport is built on httpx.
  The developer checks this from the package metadata; if the current release needs `httpx2`, the pin
  is the last httpx release with no pip-audit advisory, and it joins the #120 review (2027-01-31). If no
  such release exists, the adapters wait for the httpx2 move and the issue says so. Dependabot ignores
  its majors until that review.
- **Client**: `AsyncOpenAI(api_key=<secret>, base_url=<the catalog host, https>, http_client=<the
  provider's one-host allowlisted httpx.AsyncClient>, max_retries=2, timeout=120,
  organization=None, project=None)`; each turn, retries included, is bounded by `turn_seconds` as
  Anthropic's is. A request the allowlist refuses is raised unretried as `outbound_blocked`.
- **Errors** by SDK exception class, never by message text, with the same table as ADR 0006 §2:
  `RateLimitError`, `InternalServerError`, other 5xx, `APITimeoutError`, `APIConnectionError` →
  `model_unavailable`; `AuthenticationError`, `PermissionDeniedError`, `NotFoundError` →
  `model_config`; `BadRequestError` and the rest → `internal`. Logs get the class name, status and
  request id only.
- **Usage**: the OpenAI APIs report cached prompt tokens as a part of the prompt tokens and have no
  cache-write charge. Normalised: `input_tokens = prompt - cached`, `cache_read_tokens = cached`,
  `cache_write_tokens = 0`, `output_tokens` = completion tokens including reasoning tokens.
- **Tool schemas**: the providers' strict function mode needs every property listed in `required`
  (optional ones as a union with `null`) and `additionalProperties: false`. A pure transform turns each
  stage's Pydantic schema into that form and drops the added `null`s from the parsed input before the
  loop validates it; a test runs every tool of every stage through it and back. The loop still
  validates every input itself (ADR 0006 §4).
- **Effort**: classbeat's `low | medium | high | xhigh | max` maps per catalog model to the provider's
  own setting (OpenAI `reasoning.effort`, DeepSeek: model choice only, no effort parameter). An effort
  the model does not list in the catalog is refused when the route or the eval run is set up, never
  silently changed.

**`OpenAIProvider`** (`classbeat.agent.providers.openai_responses`): the **Responses API**, because it
carries the model's reasoning between tool turns, which multi-turn tool use needs (Chat Completions
drops it for OpenAI's reasoning models). `store=False` (nothing kept for later retrieval at OpenAI),
`include=["reasoning.encrypted_content"]` so reasoning items go back unchanged on the next turn
(the counterpart of Anthropic's thinking blocks), `tool_choice="auto"`, `parallel_tool_calls` allowed
(the loop answers them in order, ADR 0006 §1), `max_output_tokens` from the spec, no sampling
parameters, no built-in tools (web search, file search, code interpreter, computer use are never
declared), no `previous_response_id` (the history is ours, so `store=False` holds). `prompt_cache_key`
is the stage name and prompt version, never a user or job id. Stops: a final answer with function
calls → `tool_use`; with none → `end_turn`; `incomplete` for `max_output_tokens` → `max_tokens`; a
refusal item or `content_filter` → `refusal`; anything else → `other`.

**`DeepSeekProvider`** (`classbeat.agent.providers.deepseek`): the same SDK and shared layer on
**Chat Completions** (what DeepSeek's API offers), `base_url` the DeepSeek host. Tool calls as
`tools` with `strict` where the model supports it; the reasoning text a reasoning model returns is
**not** sent back (the provider's documentation asks for that) and is kept in memory only like any
model text. Stops from `finish_reason`: `tool_calls` → `tool_use`, `stop` → `end_turn`, `length` →
`max_tokens`, `content_filter` → `refusal`, else `other`. The developer takes the exact request rules
(which models support tool calls and strict mode, the reasoning field's name) from DeepSeek's published
API documentation, writes them into the module docstring with the date read, and the fixtures follow
them; nothing is learned by calling the API.

**Fallback**: ADR 0006 §2's server-side fallback stays off, and there is no cross-provider fallback: a
`model_unavailable` turn is retried by the job as today. Switching to another model would run a
model the user's route did not choose, possibly one that has not passed (§5).

### 4. The regression suite

**Layout.** `classbeat.evals` grows a suite next to the smoke set, in the package (the image carries
it), JSON as today:

```
evals/smoke/            the 18 design and revise cases, unchanged (smoke set = these, run on prompt PRs)
evals/smoke_pick/       the 3 song cases, unchanged
evals/suite/manifest.json   suite_version, the categories, their thresholds, which cases belong to each
evals/suite/<category>/*.json   cases added for the suite
```

The **suite** is every case named in the manifest: the smoke cases (by name) plus the suite's own.
`suite_version` is an integer bumped by any change to a case, a check, a threshold or the manifest; a
test computes a digest of all of them and fails if the digest changed without the version (the
digest is stored in the manifest). Results from another `suite_version` never satisfy the gate.

**Categories** (roadmap §11), each with a **stage group** (`design`: the `design` and `revise` stages;
`songs`: `pick_pool` and `choose_songs`):

| Category | Group | What its cases check (deterministic, on backend-computed data only) |
|---|---|---|
| `standard` | design | the existing checks: structure, `validate()`, duration, cooldown, zones, TSS range, limits, across lengths 10 to 90 min and every intensity |
| `music_sync` | design | interval starts on drops, breakdowns, track changes or bars (`quality.report`'s `changes_on_music` at a per-case minimum); sprints start within a bar of a drop when the case's music has one; no zone change mid-phrase where a boundary exists within 8 s |
| `games_ftp` | design | the brief's games, races and FTP tests present with the library's rules (`workout.validate`), the FTP test's warm-up, no FTP test when none was asked |
| `revisions` | design | the asked change is made (a case-defined predicate: more sprints, easier, shorter, a climb moved), everything the request did not touch kept within bounds (length, cooldown, the other segments' zones), the draft differs from the base |
| `song_choice` | songs | §4.1 below |
| `missing_music` | both | music shorter than the ask, unknown BPM, no sections, a pool with too few usable tracks: the run ends in the expected outcome (a shorter class with the warning, `pool_too_small`) and never stores a guess |
| `adversarial` | both | injected text in titles, artists, albums, playlist names and the brief; oversized and malformed tool output; tool output asking for another tool, an id or an approval: the run ends `submitted` with a valid result or a correctable error, no forbidden text in what is stored, no tool outside the stage's list called successfully |
| `cost` | both | per run: turns, tool calls, corrections and cost within the case's bounds; per category of the run file: the **median cost per class** at most the threshold of the manifest |

**4.1 Song-choice quality.** The song cases run the real `pick_pool` and `choose_songs` stages against
the synthetic catalogue of `evals/pick.py` (an in-memory `Searcher`, invented tracks; no Spotify)
and a synthetic MOWL analysis. Because the searcher is local, **song cases can run live** against a real
model too (unlike today's `smoke --live`): only the model is real. Checks (`evals/song_checks.py`):

- the pool: size `n` to `4n`, every slot's role covered by at least two candidates, no duplicate
  (title, artist), the year range of the brief honoured by the searches;
- the final list: total within ±10 % of `target_sec` (the backend's computation), no track twice,
  first `warmup` or `build`, last `cooldown`, each song's BPM inside its role's band (a table in the
  manifest: for example climbs 60 to 80 or their double, sprints at least 120; a song with no drop is
  not a sprint), energy rising into the main part and falling at the end (by BPM and drop count, the
  data the model saw);
- a replace case keeps every kept song in its slot and role;
- scores, not only pass/fail: the share of songs in their band and the length error are written to
  the results so `compare` can rank models; the case passes on the hard checks.

**4.2 Results format 2.** `results_json` writes one file per run of the suite with a header:
`results_format: 2`, `suite_version`, the manifest digest, `provider`, `model`, `effort`, the
`prompt_version` of each stage, `classbeat_version`, `mowl_version`, `prices_version`, the repeat count,
the start and end time, and the provider kind actually built (`provider.name`). Per run of a case, as
today plus `category` and the song scores. No cues, titles, ids, model text or secrets (unchanged).
`compare` reads formats 1 and 2 (after #196).

**4.3 The verdict** (`classbeat.evals.gate`): a pure function `evaluate(results, manifest) -> Verdict`
with, per stage group, `passed: bool` and per category the runs, passes and the rule that failed.
Rules, all from the manifest, with these defaults:

- every case runs at least **3 times** (`--repeat 3`);
- `adversarial` and the `limits` and `forbidden_text` checks of every category: **every run** passes;
- every other category: at least **90 %** of its runs pass, and every case passes at least once;
- `cost`: the median cost per class of the run at most **$0.75** for `design` and **$1.50** for
  `songs`, and no run over the job ceiling;
- a run that ended in `model_unavailable` or `outbound_blocked` counts as not run, not as failed; a
  file with more than 10 % such runs gets no verdict (`incomplete`), so an outage cannot fail a model
  and too few runs cannot pass it.

The verdict is never read from a file: whoever needs it computes it from the runs.

**4.4 The live command.** `python -m classbeat.evals suite --live --provider P --model M --effort E
--repeat N --max-usd N --out FILE [--record --authorization TEXT]` replaces nothing (`smoke --live`
stays as it is). It generalises `live.py`'s preconditions to any catalog model: `--live`, no `CI`, the
model in the catalog with that effort, the provider's key file present, a clean environment (§2); it
prints the plan and the ceiling and stops without `--max-usd`. It installs the worker allowlist with
only that provider's host before the provider is built, and builds the provider with
`provider_for_choice` (§5), never `fake` (a `fake` choice is refused). Each run needs the owner's
go-ahead for that run, as today; agents and CI never run it.

### 5. The gate: recorded results and routes

**Tables** (one migration, the next free revision when it lands; global, not owned: they hold no user
data, so no `owner_id` and no RLS, like `connection_keks`; the app role's privileges below, checked by
guard 2's privilege test):

`model_eval_results`: **one row per recorded run and stage group** (a suite run covering both groups
records two rows with the same `run_id`): `id`, `run_id`, `recorded_at`, `suite_version`,
`manifest_digest`, `provider`, `model`, `effort`, `stage_group` (`design` | `songs`),
`prompt_versions jsonb` (stage → SHA-256, the group's stages), `classbeat_version`, `results_sha256`
(of the file as written), `verdict jsonb` (§4.3, the group's part, computed when recorded), `passed
bool NOT NULL`, `total_cost_micros`, `authorization text` (1 to 200 characters: the owner's go-ahead,
for example "owner 2026-11-03 chat"), and a unique key `(id, provider, model, effort, stage_group,
passed)` for the foreign key below. **Append-only**: the app role has `SELECT` and `INSERT` only (a
test that `UPDATE` and `DELETE` fail, like `agent_runs`).

`model_routes`: `route_key text` (`^[a-z0-9_]{1,40}$`), `stage_group`, `provider`, `model`, `effort`,
`eval_result_id`, `gate_passed bool NOT NULL DEFAULT true CHECK (gate_passed)`, `set_at`, `set_by
text`; primary key `(route_key, stage_group)`. A **composite foreign key** `(eval_result_id, provider,
model, effort, stage_group, gate_passed)` → `model_eval_results (id, provider, model, effort,
stage_group, passed)`: **the database refuses a route whose result is not a pass for exactly that
provider, model, effort and group.** Deleting a route is how a model is disabled; the app role has
`SELECT, INSERT, UPDATE, DELETE` on routes.

**Who writes a result.** Only the live command with `--record`, in the worker container (the one with
the keys and a database connection), after the last run: it computes the verdict from the runs it just
made, refuses to record when `provider.name` is not the catalog provider of the choice (a `fake`
provider never records), and inserts the row with the file's SHA-256 and the given authorization. There
is **no import of a result file**: a hand-edited or fake-provider file has no way into the table through
the app. (The operator with database access is trusted; the gate protects against mistakes and agents,
not against the owner.)

**Routes.** `classbeat.models` (new package):

```python
async def set_route(db, route_key, stage_group, provider, model, effort, actor) -> ModelRoute
async def clear_route(db, route_key, stage_group, actor) -> None
async def list_routes(db) -> list[RouteView]          # with the result, cost and staleness
async def list_results(db, provider=None, model=None) -> list[ResultView]
async def resolve_route(db, route_key, stage_group) -> ModelChoice
def provider_for_choice(settings, choice) -> ModelProvider
```

Plain async functions on an open session, so M5's admin calls them inside its own transaction and
writes its audit row with them. `set_route` refuses (a typed `RouteRefused` with a reason code, which M5
answers as 409) unless: the model is in the catalog with that effort; for DeepSeek
`CLASSBEAT_DEEPSEEK_ENABLED` is on and the entry's `data_policy` is `allowed` (§6); the provider's key
file is present; and the **newest** recorded result for that provider, model, effort and group with the
current `suite_version` passed **and** its `prompt_versions` equal the running code's
for that group's stages. It then upserts the route with that result's id (the FK checks the rest).
Until M5's admin exists, a CLI does the same: `python -m classbeat models list | results | enable
ROUTE_KEY GROUP PROVIDER MODEL EFFORT | disable ROUTE_KEY GROUP` (worker container; `enable` prints the
result it will rely on and needs `--yes`).

**Resolving.** Every model job (`design_draft`, `revise_draft`, `design_with_songs`, `revise_songs`)
calls `resolve_route(route_key, group)` once per stage group at the start of the stage, with the
`route_key` from its job input (M5 fills it from the user's plan; until then every job has
`"default"`). The answer is the route's choice. **With no route** for that key and group, the answer is
the **bootstrap choice**: provider `anthropic` with `CLASSBEAT_AGENT_MODEL` and `_EFFORT`, as today, so
nothing changes on deploy. The bootstrap can never name another provider (`CLASSBEAT_MODEL_PROVIDER`
stays `anthropic | fake`), so **OpenAI and DeepSeek are reachable only through a route, and a route only
through a recorded pass.** `fake` stays the dev and CI provider and bypasses routes (never in `prod`).

**Staleness.** A route whose result's prompt versions no longer equal the running code's (a deploy
changed a prompt or a tool schema) or whose `suite_version` is older is **stale**: `list_routes` and the
CLI show it, the worker logs a warning once per process (route key and group, no data), and the route
keeps working until the owner re-runs the suite or clears it (owner question 3). A new route for a
stale result is refused.

**Provenance.** `agent_runs` and `class_revisions.provenance` already record provider, model, effort and
prompt version; they gain `route_key` and `eval_result_id` (NULL for the bootstrap choice and `fake`).

**How the gate is tested with fakes** (no network, no key, no live run):

- A **replay transport**: the real `OpenAIProvider` and `DeepSeekProvider` (and `AnthropicProvider`) on
  an `httpx.MockTransport` that answers from recorded fixture responses in
  `backend/tests/provider_fixtures/<provider>/` (hand-written to the documented response shapes:
  tool calls, reasoning items, refusals, `incomplete`, every error status). It asserts each request's
  host, path, headers (a test key only) and body shape, and that no planted value is in it.
- The **whole live command** offline: `main_suite` with the replay provider factory and a no-op
  allowlist installer runs a small manifest, writes a format-2 file and `--record`s it into the test
  database; a fixture set that answers well makes a passing row, one that answers badly a failing row.
- The gate: `enable` succeeds on the passing row; refuses on the failing row, a row of another effort or
  model, an older `suite_version`, other prompt versions, DeepSeek with the setting off or `owner_pending`,
  a missing key file; the database refuses a hand-made route `INSERT` pointing at a failing or
  mismatched result (the FK), and `UPDATE` or `DELETE` on `model_eval_results`.
- Resolving: a design job with a route uses the route's provider (the replay one) and records
  `route_key` and `eval_result_id`; without a route it uses the bootstrap choice; a stale route is
  used and flagged.
- `--record` with a `FakeProvider` refuses; `--record` under `CI` refuses (the precondition).

### 6. What goes to OpenAI and DeepSeek

The model input is the same as for Anthropic (ADR 0006 §7, ADR 0008 §3): the brief, Spotify-derived
track metadata and MOWL's analysis, the current draft and the user's requests; never tokens, emails,
names, ids, URLs or provider error texts. Amendment 1 and ADR 0010 allowed that data to go to "the
model" for the beta circle when the model was Claude. Sending it to another company is a new
disclosure, so:

- **Eval runs** send synthetic data only (invented music, invented briefs): no user data reaches any
  provider through the suite. They need the owner's go-ahead per run and the provider's key (which the
  owner creates; agents never see it).
- **OpenAI routes** for real users: `data_policy: allowed` on the owner's decision (question 1, default
  yes for the beta circle: `store=False`, the API's default of no training on API data). ADR 0010 gets a
  dated line recording it when answered.
- **DeepSeek**: `data_policy: owner_pending` and `CLASSBEAT_DEEPSEEK_ENABLED=false` until the owner has
  decided privacy (where data is processed and kept), the terms, and a passing eval (question 2). Turning
  it on is a code change of the catalog entry (reviewed) plus the setting; ADR 0010 records the decision.

### 7. Zone 7's TSS weight

The library's TSS gives zone 7 an intensity weight that is a guess (no power data defines it). Wherever
classbeat shows or stores a TSS of a class with any zone 7 interval it says so: the revision gets
`tss_approximate: true` (computed by the backend from the fitted draft, never from the model), the class
page shows "approximate: zone 7 has no exact TSS weight" next to the number, `preview_draft` and
`submit_draft` return the flag to the model (so it does not chase the number), and the eval results
carry it per run, where the `tss` check of a case with zone 7 widens its upper bound by the manifest's
`zone7_tss_margin` (default 10 %).

## Alternatives

- **OpenAI on Chat Completions for both providers** (one dialect): less code, but OpenAI's reasoning
  models lose their reasoning between tool turns there, which costs quality and tokens in a multi-turn
  stage. Two thin dialects over one shared layer keep each provider on its best API.
- **A third-party multi-provider library** (LiteLLM and the like): one more dependency between us and
  the provider, its own HTTP stack outside the egress guard, and the roadmap asks for the official SDKs.
- **Raw `httpx` calls without the SDKs**: no `httpx2` question, but more request code to keep right and
  against §5's "official SDKs"; kept as the fallback if no httpx `openai` release can be pinned.
- **Importing result files into the gate** (the owner runs anywhere, then uploads): simpler for the
  owner, but then a hand-edited or fake-provider file passes the gate. Recording in the same process
  that made the runs closes that path.
- **A signed result file** (HMAC with a derived key): also closes it, but needs a key in the worker
  that it does not have today, and the operator could sign anything anyway.
- **Gate only in application code**: a bug or a direct `INSERT` would enable an unpassed model; the
  composite FK makes the database refuse it.
- **Strict staleness** (a prompt change disables every route until re-evaluated): the safest, but every
  prompt PR would take the service back to the bootstrap model until the owner pays for a new run;
  offered as owner question 3.
- **Routes per stage instead of per stage group**: finer (a cheap model for `pick_pool`), but four
  routes per tier and a suite verdict per stage; two groups match how the suite is scored. A later ADR
  can split a group.
- **Cross-provider fallback on outages**: rejected (§3).

## Consequences

- New dependency `openai` (pinned, audited, httpx-based); new secrets `openai_api_key`,
  `deepseek_api_key` (worker only); new hosts `api.openai.com` and `api.deepseek.com`, on the allowlist
  only with their key (and DeepSeek's setting).
- New tables `model_eval_results` (append-only) and `model_routes`; a CLI `models`; the suite command;
  results format 2.
- M5 builds its admin's model page on `set_route`, `clear_route`, `list_routes` and `list_results`, and
  puts `route_key` into the job input at admit; nothing in M3c knows what a tier is.
- Until the owner records a passing run and enables a route, production behaves exactly as today
  (bootstrap Anthropic choice). The roadmap's "done when" is met technically with replayed fixtures;
  enabling real models is the owner's live step (the runbook of issue T4).
- Threat model T79 to T84.

## Owner questions (with the defaults agents build to)

1. **OpenAI and user data.** May OpenAI receive the same data Claude does (briefs, Spotify-derived
   track metadata, MOWL's analysis) for the beta circle? *Default: yes, with `store=False`; recorded in
   ADR 0010 when you answer. Until then OpenAI is evaluated on synthetic data only and no OpenAI route is
   set.*
2. **DeepSeek.** Keep it disabled (no route, no host) until you decide privacy and terms? *Default: yes,
   disabled; its adapter is built and tested on fixtures, and an eval run with synthetic data needs your
   go-ahead.*
3. **Stale routes.** After a deploy that changes a prompt, should enabled routes keep working (flagged)
   until you re-run the suite, or fall back to the bootstrap model at once? *Default: keep working,
   flagged.*
4. **Thresholds.** 3 repeats per case; adversarial and limit checks 100 %; other categories 90 % of runs
   and each case at least once; median cost per class at most $0.75 (design) and $1.50 (song picking).
   *Default: as written; they live in the manifest.*
5. **Does the current default (Opus 5.5) need a full-suite pass** before M5's tiers use it? *Default:
   yes for any route; the bootstrap keeps using it until you record a run, which costs roughly the
   suite's ceiling printed by the command.*
6. **Provider keys.** You create the OpenAI (and later DeepSeek) API keys and place them in the worker's
   secret files yourself, as with Anthropic. *Default: yes; agents never handle them.*

## M3c issue breakdown

One issue per PR, milestone "M3c Eval and providers". "Opus" marks an Opus Developer (provider adapters,
keys and egress, the gate); every PR gets a fresh Reviewer (Opus for the Opus issues and for EV1, Sonnet
for the rest). No issue is startable before this ADR is accepted. Fakes and fixtures only.

In-flight M4 work (#180 to #186, #198) owns `classbeat/jobs` song kinds, `classbeat/agent/{pick_pool,
choose_songs,songs,registry}.py`, the class API's song views and the frontend class pages; #196 owns
`evals/compare.py`. No M3c issue edits those files before they merge; G2 and Z7 are sequenced after them.

| # | Issue | Owner | Depends on |
|---|---|---|---|
| P0 | Catalog, keys, settings, egress: `agent/catalog.py`, `openai_api_key` / `deepseek_api_key` secrets (config, compose worker only, worktree init, stack check canaries), refused `OPENAI_*`/`DEEPSEEK_*`, `CLASSBEAT_DEEPSEEK_ENABLED`, `MODEL_HOSTS` and per-provider one-host allowlists, guard tests | **Opus** | none |
| P1 | `openai` SDK pin and `providers/openai_common.py`: client builder, error map, usage normalisation, strict-schema transform, effort map, replay transport test helper | **Opus** | P0 |
| P2 | `OpenAIProvider` (Responses API) with fixtures and the planted-value test | **Opus** | P1 |
| P3 | `DeepSeekProvider` (Chat Completions, disabled) with fixtures | **Opus** | P1 |
| EV1 | Suite manifest, `suite_version` and digest test, categories, results format 2, `evals/gate.py` verdict, zone 7 margin rule | **Opus** | none |
| EV2 | Design-group cases: `standard`, `music_sync`, `games_ftp`, `revisions` cases and checks, fake scripts | Sonnet | EV1 |
| EV3 | `song_choice` cases and `evals/song_checks.py`, live-capable song runner on the synthetic catalogue | Sonnet | EV1 |
| EV4 | `adversarial`, `missing_music` and `cost` cases for both groups, fake scripts | Sonnet | EV1 |
| G1 | Migration and `classbeat.models`: tables, FK gate, privileges, `set_route` / `clear_route` / `list_*` / `resolve_route`, `provider_for_choice`, `models` CLI | **Opus** | P0, EV1 |
| EV5 | `evals suite --live ... --record`: preconditions per provider, plan and ceiling, recording; offline test through the replay provider | **Opus** | G1, P2 |
| G2 | Jobs resolve routes: `route_key` in job inputs (default `"default"`), the four model kinds build their provider per stage group, provenance `route_key` and `eval_result_id`, stale warning | **Opus** | G1, #182 |
| Z7 | Zone 7 TSS approximate flag: backend flag on revisions and in preview/submit results, class API field, class page note | Sonnet | #183, #184 |
| T4 | Done-when: offline end-to-end gate test per provider (replay → suite → record → enable → a job uses it; fail → refused; DeepSeek refused while disabled), `docs/runbooks/m3c-model-enable.md`, AGENTS.md notes, status | Sonnet (Opus Reviewer) | P3, EV2, EV3, EV4, EV5, G2, Z7 |

Order: P0 and EV1 in parallel; then P1, EV2, EV3, EV4 and G1 in parallel (EV3 calls the song stages
as they are and edits only `evals/`); P2 and P3
in parallel; EV5; G2 (after #182); Z7 (after #183, #184); T4 last. M3c is done technically when T4 is
green: no route can be set without a recorded pass, which the database enforces, tested end to end on
replayed providers. Enabling real models is the owner's live step.
