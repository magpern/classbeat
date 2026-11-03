# ADR 0018: MOWL running classes next to cycling (M8)

Status: Accepted (owner, 2026-11-02, in chat: "M8 is approved"; issue #373; the owner's input of 2026-10-31 is its premise). Acceptance covers the design and the issue split below. It authorizes no live MOWL call. The owner authorized one read-only discovery capture (D1) afterwards; it ran on 2026-11-02 (see Amendment 1 at the end). Any further live check needs its own authorization.

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

`list_workouts` sends **`BikeTypeID: 1`**. The library has no `sport` parameter and no running
id, and no running branch in source or METADATA. Everything below is inferred from `mowl/api.py`
and `mowl/workout.py` in the vendored wheel.

| Area | Cycling behaviour today | Running implication |
|---|---|---|
| **My Workouts list** | `POST v1/programs` with `ProgramCategoryID=17`, **`BikeTypeID: 1`**, paging | Listing almost certainly needs another `BikeTypeID` (value **unknown**) to see running programs |
| **Program header** | `PUT v1/programs`: `ActivityTypeID: 0`, `TSS` from `workout.tss(spec)`, `PlaylistID` | Running may use a different **`ActivityTypeID`**. Published running load name **rTSS** (one hour at 100% rFTP is 100). The API field may reuse the name **TSS** and stays unknown |
| **Segments** | Same endpoints; segment `ActivityTypeID: 0` | Unknown whether running reuses segment shape |
| **Intervals** | `interval_body`: Coggan **`ScaleCoggan` 1–7**, **`RPMFrom`/`RPMTo`**, **`PositionTypeID`** (seated/standing), **`CycleID`**, games, FTP tests, **`Gradient: 0`** (hardcoded on the cycling payload) | RUN Player shows treadmill gradient. **`Gradient`** stays the cycling-payload hypothesis until D1 fixtures name the field, and is not required spec data before that. Standing, cadence and games may be absent or different |
| **Spec / validate** | Zones 1–7, rpm `[0–200]`, standing, games, FTP tests, cooldown rules | Running spec likely **drops or replaces** bike-only fields; validate must be sport-specific |
| **Zones** | `GET v1/intensityzonesets/1` documented as zone set 1 (HR, FTP, MAP, FTHR ranges) | Running may use another zone set id (**unknown**) |
| **TSS** | Client-side NP model with **`ZONE_IF`** tuned on **12 real cycling classes** | Published running load name **rTSS** (one hour at 100% rFTP is 100). The API field may reuse the name **TSS** and stays unknown; `GET v1/calculations/program/{id}/TSS` is not yet captured for run |

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
| **Tests / fakes** | `tests/mowlfake.py` is sign-in, `list_workouts` and Swedebeat. Program bodies are `e2e/fake-mowl/server.py` and `tests/savetools.py` (`FakeMowlPrograms`) |

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
5. How published **rTSS** (one hour at 100% rFTP is 100) is computed and returned for running
   programs. The API field may reuse the name **TSS** and stays unknown.
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
| Draft schema | Current `WorkoutDraft` | Running draft: at minimum **zone** and **sec**, plus a gradient target once D1 names its field. **`Gradient`** stays the cycling-payload hypothesis until then |
| Validation | `mowl.workout.validate` | `mowl.workout.validate_running` (library) |
| Model tools | Same tool names; dispatcher validates against sport-specific schemas |
| TSS / load display | `workout.tss` | Library function aligned with published **rTSS** (one hour at 100% rFTP is 100). The API field may reuse the name `TSS` and stays unknown |
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
- **`workout` module**: sport-aware **`validate`**, **`tss`**, **`interval_body`**, **`save`**, **`summarize`**. **`Gradient`** stays the cycling-payload hypothesis until D1 fixtures name the field; it is not required spec data before that. Cycling path behaviour stays unchanged for existing callers.
- **Document** captured ids and field dictionaries in the library README (no secrets).
- **Tests** in the library repo with recorded JSON fixtures (no network in CI).

The import ban (AGENTS.md) is the CLI and MCP entry points. The tree also imports `mowl.workout`,
`sync`, `music`, `quality`, `spotify` and related modules. Section 2 calls `workout.save` and
`validate_running`.

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
  and the gradient field D1 names; one e2e spec: running design → save → list in running mode (no live
  MOWL).

**Security / review class**

- **R1** and any backend slice that adds a **designer system prompt**, **brief validation**, or
  **sport-specific save/list** stays **security-critical** (AGENTS.md): a new prompt and brief
  validation are the **model boundary** (ADR 0017 P3/P4); changing what save and list send to MOWL
  is an **external write** even when the operation kinds stay **`mowl_save_workout`** /
  **`mowl_update_workout`** with the same protocol (ADR 0005, ADR 0007). Reusing that protocol is
  correct; it does **not** downgrade the review class.
- **Cursor** (Amendment 11, [cursor.md](../cursor.md)): **Grok 4.7 High** implements and a fresh
  **Grok 4.7 High** reviews those issues (`cursor-security-developer` /
  `cursor-security-reviewer`); **Composer** and routine **Grok 4.7** review cover **U1**, **E1**,
  and **F1** unless they widen the categories above. Amendment 11 does not assign Sonnet.
- **Claude Code and Codex** keep **Opus** Developer and Reviewer for the same AGENTS.md categories.
- **L1** (`feat(mowl): running save and list`, the sport-aware interval body, save, and list) is
  an **external write**. It stays **security-critical** even when the operation names stay
  **`mowl_save_workout`** and **`mowl_update_workout`**. **Cursor:** **Grok 4.7 High** implements
  L1 and a fresh **Grok 4.7 High** reviews it (`cursor-security-developer` /
  `cursor-security-reviewer`). **Composer** does not. **Claude Code and Codex** keep **Opus**.
- **D1** (fixtures only) stays routine.

### 5. Feature flag

**Owner question:** Gate running behind an admin or environment flag until the owner enables it for
testers?
**Reversible default:** yes—add effective **`running_enabled`** by copying **`swedebeat_enabled`**
(ADR 0015 §6): stored `off` or `on`, stricter wins, with the environment as the kill switch.
Off means **`feature_off`**: the new-class UI shows cycling only, and the API rejects
`sport=running`. `song_picking` (`off` / `granted` / `on`) is a separate flag.

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
| D1 | `docs(mowl): capture running API fixtures` | Library repo: redacted JSON, id table, discovery runbook. Routine (fixtures only) | Reviewer can implement L1 from fixtures alone | Owner go-ahead |
| L1 | `feat(mowl): running save and list` | Library: sport param, validate/save/list, tests. **Security-critical** external write (sport-aware interval body, save and list), even when the operation names stay `mowl_save_workout` and `mowl_update_workout`. Cursor: Grok 4.7 High implements and a fresh Grok 4.7 High reviews (`cursor-security-developer` / `cursor-security-reviewer`); Composer does not. Claude Code and Codex keep Opus | CLI default still cycling; running path green in library CI | D1 |
| R1 | `feat(design): sport on brief and running draft` | Backend: brief, schemas, prompt, jobs, save/list sport | Unit + integration tests with fakes; cycling unchanged | L1, this ADR Accepted |
| U1 | `feat(web): running new-class and profile chart` | Frontend sport switch, chart gradient/spm | Vitest + axe; bundle budget | R1 |
| E1 | `test(evals): running smoke cases` | Manifest + fake scripts | CI replay green | R1 |
| F1 | `test(e2e): running design and save` | fake-mowl running templates | One Playwright spec green | R1, U1 |

Issue numbers are assigned when the Architect creates them; **#373** closes when this ADR is
**Accepted** and merged, not when R1–F1 ship.

## Amendment 1 (2026-11-02, Architect): discovery findings (D1)

The owner-authorized read-only discovery ran on 2026-11-02; the findings are in
`docs/providers/mowl-running-discovery.md` (PR #468). They replace these statements above:

- Running is **not** a `BikeTypeID` filter. Every program has `BikeTypeID: 1`; running programs have
  **`ActivityTypeID: 1`** (`GET v1/ActivityTypes`), and camps are `v1/ProgramCategories` entries with
  their own `ActivityTypeID`. Listing a user's own running programs is **unverified**.
- Running intervals use the cycling key set: `ScaleCoggan` 1 to 5 with the cycling FTP bands, `Gradient`
  0.0 or 1.0 per interval, `Pace*` null, RPM 0, seated, no games, no FTP test. `TSS` carries the load.
- **Still unverified, so still not to be guessed:** what a save sends (`ActivityTypeID` on the program and on
  each segment; official programs have segments with `0`), how a user-made program stores `Gradient`, and
  the listing request. Only a running program the owner saves in the app, followed by a second read-only
  probe, closes these (D1 stays open for it).
- **L1 splits.** L1a: library read side for running (validate, summarize, TSS, interval body from the
  observed fields), no write. L1b: save and list, after D1 closes. R1's design half (brief, schema,
  prompt, `running_enabled`) may start after L1a; its save half waits for L1b.

## Amendment 2 (2026-11-03, Architect): live save test

The owner authorized a one-program live save test on 2026-11-03; the results are in
`docs/providers/mowl-running-discovery.md` ("Live save test"). They close the unknowns Amendment 1 left open:

- A running save is the cycling save with `ActivityTypeID: 1` on the program, segments left at 0, and
  running interval bodies (float `Gradient` 0.0 or 1.0). No new endpoint.
- Listing needs no change: a running program appears in the existing My Workouts request, and its sport is
  its own `ActivityTypeID`.
- The server keeps the `TSS` the client sends; the library's running TSS stays uncalibrated and is shown as
  approximate.
- **Still unverified and refused:** updating a running program, a new playlist/mix on a running program
  (song picking and Swedebeat for running stay out of M8's first cut), and segment `ActivityTypeID` 1.
- L1b (#469) is therefore unblocked for create-only. R1's save half uses it; its update half waits for a
  later, separately authorized probe.

## Amendment 3 (2026-11-03, Architect): update test, R1 split, flag behaviour

- **Update is verified.** The owner authorized a second live test on 2026-11-03 ("if you need it you have my
  go; don't alter any existing, create a new, then alter it"). On a NEW private running program: a re-save
  with `PUT v1/programs/{id}` (same id, `ActivityTypeID` 1 kept), new segment copies (`ActivityTypeID` 0) and
  `PUT v1/programs/{id}/segments` replaced the segments; changed zones and `Gradient` read back; the client
  TSS was stored; and `PlaylistID` set to an already-analysed playlist id was accepted and kept. Amendment 2's
  "updating a running program is unverified and refused" no longer holds; library 0.9.0 implements it (L1c,
  mowl-workouts#22) with a read-first target check and a sport-mismatch guard (L1d, mowl-workouts#23; the
  classbeat side is `program_other_sport`, #487).
- **Still unverified and refused:** a NEW mix or playlist attach for a running class (it creates objects in
  MOWL and running audio sync is untested) and segment `ActivityTypeID` 1. Running v1 designs on an existing
  MOWL playlist only.
- **R1 is split:** R1a (#463, merged as #491: `sport` on the brief, the flag, running draft schema and prompt) and R1b (#490:
  save, update and reconcile). A running class cannot be saved until R1b; with `running_enabled` off, existing
  running classes cannot be saved or updated either (§5).
- **The flag stays off for real users until E1** (#465) puts running evals behind the model-enable gate:
  running jobs use the `design` route and no recorded eval result covers their prompt versions.

## Amendment 4 (2026-11-03, Architect): no soundless running in v1

ADR 0007's amendment of 2026-11-03 adds a "No music" source for **cycling** classes. A running brief with
it is refused `invalid_input` at input, and a running revision with `none` music is `invalid_input` at Save
and Update: running v1 still designs on an existing MOWL playlist only (§2). Soundless running would need
its own design and revise prompts and is a later change.
