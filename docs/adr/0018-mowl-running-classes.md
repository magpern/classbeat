# ADR 0018: MOWL running classes next to cycling (M8)

Status: Proposed (issue #373; the owner's input of 2026-10-31 is its premise).

Related: [ADR 0006](0006-agent-runs.md) (agent loop, `WorkoutDraft`, prompts, evals),
[ADR 0007](0007-classes-revisions-and-saving.md) (classes, save and update to MOWL),
[ADR 0008](0008-song-picking.md) (song picking and music sync),
[ADR 0012](0012-eval-suite-and-model-providers.md) (suite, manifest, enable gate),
[ADR 0013](0013-ui-design-night-studio.md) (Profile chart and class page),
[ADR 0017](0017-class-pattern-soft-rules.md) (profiles; running slot §7, #401 P9),
GitHub milestone **M8 Running classes**.

## Context

MOWL (Intelligent Cycling) ships two class modes in one app: **Ride** (indoor cycling) and **Run**
(treadmill / group running). In the app, a top-level switch shows only workouts for the active
mode. classbeat today implements the cycling path only: the brief, validation, designer prompts,
quality and soft rules, the class profile chart, save and update, evals and e2e fakes all assume
cadence, seated/standing, games, FTP tests and Coggan zones 1–7 on a bike.

Issue #373 asks for **research first**, then this ADR, before any build. No live MOWL call was
made for this document. Findings below come from the vendored `mowl-workouts` wheel (v0.7.0,
captured against MOWL app **8.8.8**), the classbeat tree on `main`, and MOWL's public help and
release notes.

## Research findings

### Public MOWL product facts (not API contracts)

Sources: MOWL help centre (RUN Player article, updated 2026-04-06), App Store release notes
(8.8.1, 2026-04-08: "Added a new sport type: Running"), and the MOWL RUN press material.

- Users switch **RIDE** and **RUN** from the app menu; each mode filters what they see.
- The **RUN Player** shows, per interval: time left (colour = intensity), a **training target**
  (RPE, %HRmax or **%rFTP**), and **treadmill gradient**.
- The overview still uses **colour blocks** by interval; symbols mark how intervals should be
  performed (details in separate help articles not fully reproduced here).
- Running workouts exist in the catalogue ("10+ official running workouts" at RUN launch); the
  Session Designer and save path are assumed analogous to cycling until API evidence says otherwise.

These facts justify a **second sport** in classbeat but do **not** specify JSON field names,
numeric ids, or validation rules.

### What `mowl-workouts` v0.7.0 implements (cycling only)

The library has **no** `sport`, `discipline`, `BikeType`, or running branch in source or METADATA.
Everything below is inferred from `mowl/api.py` and `mowl/workout.py` in the vendored wheel.

| Area | Cycling behaviour today | Running implication |
|---|---|---|
| **My Workouts list** | `POST v1/programs` with `ProgramCategoryID=17`, **`BikeTypeID: 1`**, paging | Listing almost certainly needs another `BikeTypeID` (value **unknown**) to see running programs |
| **Program header** | `PUT v1/programs`: `ActivityTypeID: 0`, `TSS` from `workout.tss(spec)`, `PlaylistID` | Running may use a different **`ActivityTypeID`**; TSS may follow **rFTP** semantics (%rFTP in help) |
| **Segments** | Same endpoints; segment `ActivityTypeID: 0` | Unknown whether running reuses segment shape |
| **Intervals** | `interval_body`: Coggan **`ScaleCoggan` 1–7**, **`RPMFrom`/`RPMTo`**, **`PositionTypeID`** (seated/standing), **`CycleID`**, games, FTP tests, **`Gradient: 0`** (hardcoded) | RUN Player needs **gradient**; standing/cadence/games may be absent or different; **`Gradient` must become data**, not a constant |
| **Spec / validate** | Zones 1–7, rpm `[0–200]`, standing, games, FTP tests, cooldown rules | Running spec likely **drops or replaces** bike-only fields; validate must be sport-specific |
| **Zones** | `GET v1/intensityzonesets/1` documented as zone set 1 (HR, FTP, MAP, FTHR ranges) | Running may use another zone set id (**unknown**) |
| **TSS** | Client-side NP model with **`ZONE_IF`** tuned on **12 real cycling classes** | Running load metric may differ; server exposes `GET v1/calculations/program/{id}/TSS` (not yet captured for run) |

**Save flow** is unchanged in shape (ADR 0007): program → segments → intervals → segment list.
classbeat and the e2e fake MOWL implement that path with **`ActivityTypeID: 0`** and no
`BikeTypeID` on create—only the listing call pins **`BikeTypeID: 1`**.

### What classbeat implements today (cycling-only)

| Layer | Cycling assumption |
|---|---|
| **`Brief` / new-class form** | Length, intensity, climbs, sprints, races, FTP test, style, text, optional `class_type` (ADR 0017); **no sport** |
| **`WorkoutDraft` / intervals** | Zones 1–7, rpm or `"beat"`, standing, games, ftp_test; `workout.validate()` from the library |
| **Designer** | `SYSTEM_PROMPT` in `agent/design.py`: indoor cycling, standing, rpm, races, games |
| **Music sync** | `sync.fit_to_music` on a Spotify or MOWL map; cadence guidance in prompt (~60–80 rpm standing, beat riding) |
| **Soft rules** | Profiles under `design/profiles/cycling/` only; `Sport = Literal["cycling"]` (ADR 0017) |
| **UI** | `ProfileChart`: zones 1–7, cadence text in **rpm**, flame/bolt markers; landing copy says indoor cycling |
| **Save / update** | `mowl.workout.save` / reconcilers; listing via `list_workouts` → **cycling list only** |
| **Evals** | Smoke and suite cases are cycling designs; song-pick smoke uses cycling cadence/BPM habits |
| **Tests / fakes** | `tests/mowlfake.py`, `e2e/fake-mowl/server.py`, `tests/savetools.py`: cycling program bodies |

A running class saved with today's stack would be stored in classbeat as a cycling-shaped draft and
written to MOWL with **`Gradient: 0`**, **`BikeTypeID: 1`** on list, and bike interval fields—i.e.
**not** what the RUN Player expects.

### Unknown until authorized discovery

The following **must not be guessed** in implementation PRs. They are filled by a **read-only**
discovery pass (owner go-ahead per AGENTS.md; log under "Live activation log" in
`docs/status.md`, maintained by the Architect—not part of #373):

1. Numeric **`BikeTypeID`** for running and whether My Workouts is filtered only by that field.
2. **`ActivityTypeID`** on programs and segments for running creates/updates.
3. Interval JSON for running: which of RPM, position, cycle, games, FTP test appear; how
   **gradient** and pace/speed targets are encoded (field names and ranges).
4. Whether **`ScaleCoggan` 1–7** and zone set **1** apply to running on the server, or running uses
   a parallel scale.
5. How **TSS** (or an equivalent) is computed and returned for running programs.
6. Whether **Spotify playlists** attach to running programs the same way (`PlaylistID`, analysis,
   BPM sync).
7. Example **read** payloads: `GET v1/programs/{id}` for one running template and one user-created
   running program; `POST v1/programs` list pages for `BikeTypeID` candidates.

**Reversible discovery default:** capture traffic from the MOWL desktop app (8.8.x) while switching
to RUN, listing My Workouts, opening one official running workout, and exporting screen JSON—or
provide one **exported running program** file from the owner—before any classbeat or library code
changes. No writes during discovery.

## Decision

This ADR **does not** implement running classes. It records the intended architecture once
discovery closes the unknowns above, and splits work across **mowl-workouts** and classbeat.

### 1. Sport is a first-class, immutable choice per class

Each class has **`sport`**: `"cycling"` | `"running"`.

- **Storage (proposed):** add optional `"sport"` to the validated **`Brief`** JSON on `classes`
  (`brief` is set on the first design job and not rewritten). Serialize `"cycling"` explicitly for
  new classes; **omit** the key on legacy rows and treat absence as **`cycling`** everywhere
  (validation, UI, save, export). Do not allow changing sport after the first revision exists.
- **API:** new-class and job input carry `sport`; responses that expose the brief expose it too.
- **Quotas and plans:** unchanged at first; one class is one class regardless of sport (owner may
  revisit later).

**Owner question:** Should running and cycling share the same monthly class quota without distinction?
**Reversible default:** yes, shared quota.

### 2. Two parallel spec pipelines through one job architecture

Keep **`design_draft`**, **`revise_draft`**, **`save_workout`**, and **`update_workout`**; branch
behaviour inside by `sport` rather than new job kinds.

| Concern | Cycling (keep) | Running (add) |
|---|---|---|
| Draft schema | Current `WorkoutDraft` | Running draft: at minimum **zone**, **sec**, **gradient**; optional fields per discovery |
| Validation | `mowl.workout.validate` | `mowl.workout.validate_running` (library) |
| Model tools | Same tool names; dispatcher validates against sport-specific schemas |
| TSS / load display | `workout.tss` | Library function aligned with MOWL server for running (may reuse field name `TSS`) |
| Music fit | `sync.fit_to_music` | Same if playlists attach; prompt uses **spm** not rpm (typical band ~150–190 spm vs cycling ~60–100 rpm) |
| Save | `workout.save(...)` | `workout.save(..., sport="running")` or parallel entry point calling running interval mapping |
| Reconciler listing | `list_workouts(..., bike_type=...)` | Must list with running **`BikeTypeID`** so saves are found |

**Owner question:** For v1 running, is **design on an existing MOWL playlist** enough, with **song
picking deferred** (ADR 0008 stays cycling-only until a follow-up)?
**Reversible default:** yes—running v1 = connect MOWL, pick playlist, design, save; no Spotify pick
pipeline.

### 3. `mowl-workouts` release (backwards compatible)

Ship in **`magpern/mowl-workouts`** first, then vend a new wheel in classbeat (same process as
0.6.0 / 0.7.0):

- **`api.list_workouts`**: parameterise **`BikeTypeID`** (default `1` for CLI/MCP/plugin compatibility).
- **`workout` module**: sport-aware **`validate`**, **`tss`**, **`interval_body`**, **`save`**, **`summarize`**; running sets **`Gradient`** from spec; cycling path behaviour unchanged for existing callers.
- **Document** captured ids and field dictionaries in the library README (no secrets).
- **Tests** in the library repo with recorded JSON fixtures (no network in CI).

classbeat continues to import only **`api`**, **`service`**, **`library`**, **`spotify_web`**, **`swedebeat`**
modules—never CLI/MCP entry points (AGENTS.md).

### 4. classbeat application changes (by layer)

**Backend**

- Brief and job input: `sport` enum; running brief **drops or hides** bike-only knobs (climbs,
  sprints, races, games, FTP test) unless discovery shows parity.
- Agent: separate **`SYSTEM_PROMPT`** (and later prompt_version) for running; running eval cases
  in smoke/suite when the enable gate is extended (ADR 0012).
- Design rules: ADR 0017 already reserves `profiles/running/`; no rules until #401 data (unchanged).
- Saving: pass sport into library save/list; reconcilers compare running programs on the correct
  listing; **`saved_workouts`** unchanged schema (program id is opaque)—optional future column only
  if cross-sport id collision were proven (not assumed).
- Account export/delete: include `sport` in exported brief; no new tables required for v1.

**Frontend**

- New class: **Cycling / Running** control (step 1 or top of brief); copy and EffortCards differ
  for running; keep Night Studio tokens (ADR 0013).
- Class page: **`ProfileChart`** shows **gradient** and running targets when `sport=running`; cadence
  label **spm** where rpm is shown today; hide standing/game markers when absent from draft.
- Landing and meta copy: product still "classbeat"; describe both sports where marketing strings
  mention cycling only today.

**Fakes and e2e**

- Extend **`FakeMowl`** / **`FakeMowlPrograms`** with **`BikeTypeID`**, running program templates,
  and gradient on intervals; one e2e spec: running design → save → list in running mode (no live
  MOWL).

**Security / review class**

- Treat as **routine** unless discovery exposes new external hosts or auth (unlikely). Save and
  listing remain **`mowl_save_workout` / `mowl_update_workout`** with the same operation protocol
  (ADR 0005, ADR 0007). Opus if interval or brief validation widens the trust boundary in an
  unexpected way; otherwise Sonnet Developer per Amendment 11.

### 5. Feature flag

**Owner question:** Gate running behind an admin or environment flag until the owner enables it for
testers?
**Reversible default:** yes—add effective **`running_enabled`** (environment + optional admin flag,
same stricter-wins pattern as `song_picking` / `swedebeat_enabled`): off → new-class UI shows
cycling only; API rejects `sport=running` with **`feature_off`**.

### 6. Roadmap and milestones

Milestone **M8** (#373 tracker) proceeds **after** discovery and acceptance of this ADR:

1. **D1** – Discovery artefact in the library repo (fixtures + doc), owner-signed capture date.
2. **L1** – `mowl-workouts` running API support + release tag; classbeat bumps vendored wheel.
3. **R1** – classbeat backend: sport on brief, validation branch, running designer prompt, save/list.
4. **U1** – Frontend sport switch, running chart, copy.
5. **E1** – Eval smoke cases + suite manifest bump for running (offline first; live baseline owner-run).
6. **P9** – Running pattern profiles (#401), blocked on aggregate data (ADR 0017 §7).

A **roadmap amendment** (Architect PR) should add M8 rows and "done when" language; it is **not**
part of issue #373.

### 7. Live activation and permissions

Read-only discovery and the first live save test require **owner authorization** per AGENTS.md and
ADR 0010. Agents do not run them in CI. Beta circle rules for model input (Spotify/MOWL metadata to
the model) apply to running designs the same as cycling once enabled.

## Alternatives

| Alternative | Why not |
|---|---|
| **Fork classbeat into a running-only app** | Duplicates connections, billing, jobs, admin; MOWL already unifies Ride/Run. |
| **Save running workouts as cycling programs with a tag** | RUN Player and My Workouts filtering would be wrong; reconcilers would mis-list. |
| **Implement from marketing copy without API capture** | High risk of silent save failures or wrong gradients/intensity. |
| **Single shared interval schema with nullable bike fields only** | Harder to validate and to teach the model; two explicit schemas are clearer. |

## Consequences

- **Positive:** Aligns classbeat with MOWL's product direction; reuses jobs, save protocol, and UI
  shell; library changes benefit CLI/MCP consumers too.
- **Cost:** Large cross-cutting effort; **two** designer prompts and eval tracks; fakes must gain
  sport dimension; every cycling guard test must prove running does not regress cycling defaults.
- **Dependencies:** Blocked on **D1** discovery before L1/R1; P9 still blocked on owner aggregates
  (#401).
- **Risks:** Wrong `BikeTypeID` breaks listing reconciliation; inventing gradient or TSS rules
  without server alignment frustrates instructors; song-pick cadence conflated with running spm if
  shipped too early.

## Proposed issues (for the Architect; not opened by #373)

| Id | Title (Conventional Commits subject) | Scope | Done when | Depends on |
|---|---|---|---|---|
| D1 | `docs(mowl): capture running API fixtures` | Library repo: redacted JSON, id table, discovery runbook | Reviewer can implement L1 from fixtures alone | Owner go-ahead |
| L1 | `feat(mowl): running save and list` | Library: sport param, validate/save/list, tests | CLI default still cycling; running path green in library CI | D1 |
| R1 | `feat(design): sport on brief and running draft` | Backend: brief, schemas, prompt, jobs, save/list sport | Unit + integration tests with fakes; cycling unchanged | L1, this ADR Accepted |
| U1 | `feat(web): running new-class and profile chart` | Frontend sport switch, chart gradient/spm | Vitest + axe; bundle budget | R1 |
| E1 | `test(evals): running smoke cases` | Manifest + fake scripts | CI replay green | R1 |
| F1 | `test(e2e): running design and save` | fake-mowl running templates | One Playwright spec green | R1, U1 |

Issue numbers are assigned when the Architect creates them; **#373** closes when this ADR is
**Accepted** and merged, not when R1–F1 ship.
