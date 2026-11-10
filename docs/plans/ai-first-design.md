# Plan: milestone MA "AI-first class design" — revision 7 (frozen)

Design record: [ADR 0029](../adr/0029-ai-first-class-design.md); roadmap Amendment 18. Where this
plan and the ADR differ, the ADR wins; fix the plan in the PR that changes it.

Revision 7 was approved and frozen by the owner on 2026-11-10 after three Architect review rounds
(an independent Opus agent; AMEND on revisions 2, 4 and 5, all findings folded in) and the owner's
own review of revision 6 (three corrections: illustration provenance, a concurrent hand-off test,
the Ghost Target policy).

## Context

**What the proposal asks for.** The proposal is
`classbeat-screenshots/classbeat-redesign/CLASSBEAT_REDESIGN_PROPOSAL.md` (on `main`). It replaces
the form-style New class page with one free-text request. The instructor then refines the class in
a two-panel workspace: a chat on the left and a live preview on the right. Nothing is saved to MOWL
until the instructor explicitly approves it.

**Where it fits.** MX (the UI refresh, ADR 0028) is closed, and no milestone covers this work. So
it becomes a **new milestone, MA**, with a plan document, ADR 0029 and roadmap Amendment 18.

**How this plan got here.**
- Revision 1 set the approach, and the owner approved it.
- Revision 2 added MOWL games and the split between explicit instructions and assumptions.
- Revision 3 added the owner's transcription of MOWL's article "How to add a GAME"
  (https://help.mowl.com/hc/en-gb/articles/17909652657564-How-to-add-a-GAME, by Brian Overkaer,
  transcribed by the owner on 2026-11-10).
- Revisions 4 and 5 fold in two Architect reviews: an independent Opus agent, resumed for the
  delta round.

### Owner decisions (2026-11-10)
1. **Defaults first, then refine.** No clarifying questions in v1. Assumptions are shown, and the
   chat corrects them.
2. **The structured form stays as "Advanced"** at `/classes/new/advanced`. Free text is the default
   at `/classes/new`.
3. **Playlist and mix names may go to the interpreter.** Names only, never ids, and the picker can
   always override the choice.
4. **Games:**
   - exactly four cycling games;
   - an explicit request keeps its type, placement and length;
   - the AI picks a type when the request names none;
   - the preview and history show the real type;
   - the article's constraints apply;
   - no running games.
5. **Ghost Target is removed from everything we support:** what classbeat offers, AI generation,
   validation and saving, and from `mowl-workouts` too. Old records may still be read through a
   separate compatibility schema (`StoredWorkoutDraft`), and only for reading. An existing Ghost
   Target interval never silently becomes another game.
6. **The article, as the owner transcribed it, is the authority.** We invent no rule the article
   doesn't state.
7. **A default never silently overrides an explicit instruction.**
8. **No licence check.** MOWL manages licences; classbeat states the requirement and never claims
   to have checked it.
9. **No separate game engine.** We reuse the schema, validators, revisions and the MOWL save path.

## MOWL game facts (from the article) and what classbeat does with them

| Article | classbeat |
|---|---|
| Four games. Racetrack: teams complete the most laps before time runs out. Power Pull: two teams compete on W/kg, like a tug of war. Fast Lane: highest team average speed. Overflow: teams progress by staying in their target zones | The catalogue holds exactly these four |
| Games are a commercial feature that needs a studio with a commercial MOWL licence. They are added in the desktop Session Designer, can't be added mid-class, and can be switched off before class | One notice line wherever games are offered, and on a class that has a game. No check, and no claim of one. Our own plan gate `games` is unchanged |
| A game lasts at least **10 s**. The interval **right after a game** lasts at least **10 s** | Enforced by the library's `validate` (MW1). That one source covers both the tool loop (`agent/design.py:161,173`, `soundless.py:126`) and saving |
| No maximum length or count, no required recovery or spacing; back-to-back games are not forbidden | We add none of these. For the existing ban, see "Back-to-back" below |
| Games work with both power and heart rate; with heart rate, longer games are better | Every cycling interval body already writes the PulseZone, FTP and FTHR ranges of zone set 1 for its zone (`workout.py:224-243`). The heart-rate advice is prompt guidance only |
| With too few riders, MOWL runs the game as a zone 3 interval (target games with a zone target are an exception) | Recorded in the ADR, not implemented |
| Most games reward higher team effort | Prompt guidance about placement and overall load |
| No countdown before a game; the overview shows which game is next | A cue may announce the coming game. Never claim a countdown |
| Teams are random, 2 to 4 of them by rider count; the supported players are listed | Recorded in the ADR, not implemented |
| The editor's operations: add, replace (keeps the length), change type, remove, change length; for target games, set the intensity (the zone) | Revision-chat operations, one eval case each |

### What the code does today (verified)
- **Five games.** `Game` Literal (`agent/schemas.py:63`); the library's
  `GAMES = {racetrack 1, power_pull 4, fast_lane 5, overflow 6, ghost_target 7}`
  (`mowl/workout.py:49`).
- **Tests and evals pin the set.** `test_agent_tools.py:99` and `test_evals_suite_design.py:87`
  pin it, and the eval case `games_fast_lane_ghost_50` uses Ghost Target.
- **Serialization.** A game is written as `IntervalGame.IntervalGameID` and read back in
  `mowl_state.py:195`. No path imports a workout from MOWL.
- **Games are interchangeable apart from their id.** The target is the interval's zone; the plan
  gate is a single boolean; the UI shows no game type.
- **Validation.** `sec ≥ 1`; game ∈ `GAMES`; an interval can't be both a game and an FTP test;
  "games and FTP tests must be separated by a regular interval" (`workout.py:151-154`, attributed
  to "the app's own rules"). No 10 s rules.
- **Running.** No games anywhere: strict `RunningDraft`, the running interval body, the prompts,
  and ADR 0018's probe of 24 programs. MOWL's announced running games are out of scope.
- **A race is not a Racetrack game.** A race is a 15 to 35 s burst in zone 6 or 7.

### Back-to-back (resolved in MA0)
`mowl-workouts` commit 7378d91 (2026-10-27) recorded from a live test in its `docs/mowl-api.md`:
"The server saves two FTP tests in one workout, or two games back to back, but the app then refuses
to open the workout for editing." That is evidence, so the rule stays (ADR 0029 §2), the
`test_games_policy.py` row reads "refused", and MW1 links the evidence. No owner question.

## Design (ADR 0029)

### A. Games

**MW1 (`magpern/mowl-workouts`, following its own `CLAUDE.md`):**
- remove `ghost_target` from `GAMES` and from the MCP prompt;
- reading back an unknown id gives `IntervalGameName` or `"unknown:<id>"`, never another game;
- `validate` gains "a game lasts ≥ 10 s" and "the interval after a game lasts ≥ 10 s", across
  segment boundaries. A game as the **very last interval** has no next interval, so it passes. The
  cooldown rule makes that rare, and the ADR states the case;
- the back-to-back rule kept, with its evidence linked;
- a changelog entry noting that removing a `GAMES` value breaks CLI and MCP users;
- release v0.11.0. classbeat stays on 0.10.0 until MA1a-1.

**Catalogue: `design/games_catalog.py`** (pure)
- The four types, each with:
  - an i18n key;
  - its MOWL id, taken from the library's `GAMES`;
  - the article's one-line description;
  - classbeat's advice, labelled as advice. Racetrack: a sustained push. Power Pull: fair across
    body sizes, suits climbs. Fast Lane: flat, fast cadence. Overflow: steady control, suits mixed
    groups.
- The article's facts: licence, heart-rate advice, no countdown.

**Policy: `design/games_policy.py`** (pure) handles only:
- `game_retired` (a stored game outside the catalogue);
- cycling only;
- the check on a stored draft.

The 10 s rules live only in the library. A test makes sure the library refuses a 9 s game and a
9 s interval after a game.

**Strict vs stored schema (MA1a-1)**
- `Game` = the four. The model-facing tools, `to_spec` and saving stay strict:
  `saving/approval.py:164` stays strict.
- New `agent/stored_draft.py`, split out of `schemas.py`: `StoredWorkoutDraft`, `stored_draft` and
  `draft_model`, with `StoredGame` = the four plus `ghost_target`.
- **Read sites switched to the stored schema:**
  - `classes/history.py:197` and `classes/routes.py:234` (history, compare, revision API);
  - `design/diff.py:27`;
  - `jobs/routes.py:373`;
  - `design/revise.py:216` (the base, before the legacy rewrite);
  - `design/refit.py:207`;
  - `songs/revise.py:303`.
- **`game_retired` refuses six actions on a legacy draft:** Make current
  (`classes/service.py:358`), Save (`saving/kind.py:161`), Update (`saving/update.py:165`),
  Refit, Change songs and **Duplicate**. Duplicate validates through the strict `spec_of`
  (`classes/duplicate.py:77,345`) and would otherwise answer 500. The refusal happens before
  `to_spec`, which would raise in `mowl_state.py:96` and in `validate`.
- **Revise from a legacy base:** the parse goes through the stored schema; the interval becomes a
  regular interval with the same length and zone; a `game_retired` Not as asked item is recorded.
  The model never picks a replacement game.
- One compatibility test per path. History, compare and diff read legacy drafts safely, because
  `to_spec`, `tss` and `total_seconds` don't look up `GAMES`.
- **The wheel bump, the `uv.lock` hash and the read side land in this one PR.**

**Prompts (MA1a-2: `design.py`, `revise.py`, `soundless.py`)**
- The catalogue, rendered from the pure module.
- A race is not a game.
- Choose a type only when the request names none, and never invent one.
- Think about the load around a game and the requested effort.
- Use longer games with heart rate.
- A cue may announce a game; there is no countdown.
- **Leave a margin over 10 s, because fitting to the music can shorten an interval.**
- The running prompts stay free of game words.

**Change summary** (`design/diff.py`): segment index plus type, for example "segment 3: Power Pull
→ Overflow", within the 1024 bytes of `change`.

**Evals (MA1a-2)**
- The Ghost Target case becomes Fast Lane only.
- Generic design requests: "competitive team challenge", "a game that rewards steady pacing",
  "finish with a short team race". Any one valid type passes; an invented name fails.
- Revision cases for the article's five operations, each checking that only the targeted
  interval or intervals changed.
- A case where music fitting comes near the 10 s minimum.
- The coverage test becomes "every catalogue game is covered".
- `suite_version` bumped.

**Display (MA1c)**
- `DraftInterval.game` on the frontend.
- A marker on `ProfileChart` (a lucide icon with an accessible name).
- The type name and a small illustration on segment cards and in `DraftSummaryView`.
- Game wording in `describeComparison`.
- A legacy label, "Ghost Target (no longer offered)".
- The licence line.

**Illustrations**
- The eight AVIF files go into `frontend/public/img/` as
  `game-<type>-<theme>-<width>.avif|webp|jpg` renditions. This also fixes the `racetrach-light`
  filename.
- Four new `SlotId`s in `components/imagery/imageSlots.ts`, drawn by `SlotPicture`; alt text from
  the catalogue.
- **Provenance (owner, 2026-11-10).** These are original AI-generated classbeat illustrations,
  made **using MOWL game screenshots as visual references**. ADR 0029 says exactly that and makes
  no claim that they were made without reference to MOWL's screens. It also records the owner's
  decision to use them under ADR 0013's rule that "nothing is copied exactly" and that there is
  no MOWL logo, owl or wordmark (ADR 0013, lines 29 and 126).

### B. Explicit instructions vs. assumptions (MA1b-1 pure, MA1b-2 interpret)

**What the interpreter returns.** The tool `submit_brief` returns an `InterpretedRequest`. Each
field is `{value, origin: explicit|inferred, quote?}`. Anything the model leaves out becomes
`default`. The fields are:
- sport, length, effort and class type;
- the counts of climbs, sprints and races, and the FTP test;
- the music choice, given as a list index;
- `games: [{type: one of the four | "any", placement?: {position: start|early|middle|late|before_cooldown | after:<hint> | replaces:<hint>}, duration_sec?}]`.

**No model text goes back to the model.**
- The Brief holds only closed enums and numbers. A placement hint is turned into a segment
  index or position before it is stored.
- `quote` and the hints are kept only if they are **verbatim substrings of `brief.text`**, which
  is the user's request.
- Playlist and mix names: `clean_text`, at most 50 names of 60 characters each, in a numbered data
  block, and only an index comes back.
- An adversarial eval case uses a playlist named like an instruction.

**`design/request_policy.py`** (pure)
1. Explicit values are never replaced by defaults.
2. An explicit value that breaks a hard limit is never changed quietly. The hard limits are plan
   length, the games gate, a game on a running class, a game under 10 s, and a sport that isn't
   offered. The policy uses the nearest allowed value or leaves the item out, and records Not as
   asked with a reason code. A game is never swapped for another.
3. Inferred and default values are recorded as Assumed.
4. Music order for cycling: the override, then a name match, then pick (if Spotify is ready), then
   the first MOWL playlist, then soundless. For running (ADR 0018): MOWL playlists only, otherwise
   Not as asked `needs_mowl_playlist`.
5. The allowed sports (`runningEnabled` and the plan) go to the interpreter.

**`Brief` (moved into `agent/brief.py` in MA1b-1)**
- Gains `games` (at most 6) and `explicit` (at most 20 paths).
- When empty, these are left out of the serialized Brief, so existing briefs stay byte-identical.
- `games` is cycling-only (`CYCLING_ONLY_BRIEF`).
- The worst case fits the 4096-byte cap, checked at admit.

**`check_explicit(brief, draft)`** (pure)
- Checks:
  - length, within a tolerance;
  - each explicit game's type, placement and duration, within a tolerance;
  - the counts.
- A miss is a tool error inside the existing correction budget. If it still misses, a Not as
  asked item is recorded.
- MA1b-1 wires it into `design_draft`, so Advanced-form values count as explicit, and into
  revise, where the explicit list is sent as constraints to keep and the new request's explicit
  items are layered on top.
- The prompt says that explicit values win over ADR 0017's soft rules.

**Where the interpretation lives**
- Items are `{path, origin, reason_code?, value, quote?}`.
- **On revisions:** the items go in `provenance.interpretation` of design and revise revisions,
  bounded and checked in `agent/records.py`. That is also where `design_draft` and revise put
  their own Not as asked items.
- **With no revision** (the hand-off is refused): a new `classes.interpretation jsonb` column with
  an `octet_length` check, added by a migration in MA1b-3.
- The frontend renders three parts, **As you asked**, **Not as asked** and **Assumed**, from
  catalogue keys. The assistant message body stays the model's short note, at most 300
  characters.

### C. Two jobs: `interpret_request`, then a server hand-off (MA1b-3)

**`interpret_request`**
- Input: `{request 1–2000, music_override?, sport_override?, mowl_connection_id, spotify_connection_id?, title?}`.
- **Admit:**
  - creates the class;
  - stores message seq 1;
  - takes **no class credit**;
  - counts toward the cost cap and the per-user job limit.
- **Worker:** the `interpret` stage, then `request_policy`. It stores the resolved
  `{brief, music}` and the items in `classes.interpretation` under its fence, then completes. **It
  does not enqueue.** The worker can't import `jobs.service` (`test_session_guard.py`). Also, a
  design job created while the interpret job is still running would hit the one-open-job-per-class
  index (`jobs/service.py:127-129`).
- **Web-side hand-off (the `classes/handoff.py:328-353` pattern):**
  `POST /api/classes/{id}/design-from-interpretation`.
  - It reads `classes.interpretation` under the owner lock.
  - It enqueues the **unchanged** `design_draft` or `design_with_songs` through `JobEnqueue`, with
    their own admit checks and the class credit. The idempotency key is derived from the interpret
    job id, so the route is idempotent.
  - A refusal (`consent_missing`, `pool_busy`, quota and the like) is stored on the class and
    shown. No credit is used.
  - **The browser calls it when the interpret job completes.**
  - **After a closed tab,** `/classes/<id>` finds an interpretation with no design job and no
    revision, and calls the same route.
  - Route tests cover idempotency, the owner lock, a refusal and the call after a reopen.
  - **Concurrency test (owner, 2026-11-10):** two requests to `design-from-interpretation` at
    the same time. Exactly one design job is admitted and exactly one class credit is used, and
    both callers get the same job id and state. The owner lock and the derived idempotency key
    together guarantee this. The test runs against the Postgres container with two sessions.
- **A failed interpret** uses no credit.
- **Hard-coded kind lists:** each one gets the new kind, or a test showing it doesn't need it:
  - `classes/service.py:56,132`;
  - `design/sport.py:135`;
  - `agent/records.py:211,296`.
- **Chat:**
  - The first design revision's `request_message_id` is not seq 1, because the request already
    reaches the model in `brief.text`.
  - `chat_full` counts seq 1.

### C2. Eval gating (ADR 0012 §5)
- An `interpret` key in the design group of `GROUP_STAGES`, with suite cases:
  - explicit values kept;
  - game type, placement and "any";
  - injection;
  - a running request with a game;
  - Swedish and Danish requests.
- `suite_version` bumped. The cycling prompt pins (`test_agent_running.py:68-71`) change on purpose.
- **Waiting for the owner:** a live suite run before release. Until then, stale routes are refused,
  as ADR 0012 intends.
- **ADR 0023 §7 amendment:** classes stay English. The interpreter accepts sv and da, and phrases
  added from Ideas use the UI language.

### D. Frontend

**Start screen `/classes/new` (MA2)**
- The prompt box and **Design my class**.
- Starter ideas.
- Chips for connected services.
- **Music ▾** (`MusicSourcePicker`).
- **Ideas for your class:** a compact row of Climbs, Sprints, Intervals and Games, collapsed by
  default.
  - Games opens four cards: illustration, name, the article's line and the licence line.
  - Tapping a card **adds a phrase to the prompt**. No counters and no required steps.
  - Shown only if the plan includes games, and marked "cycling".
- An Advanced link.
- If MOWL is not connected, the existing `no_mowl` state.
- Home presets are changed to link to `/classes/new/advanced`.
- `AppShell.tsx:47` marks Advanced as "New".
- "Try again" in `JobEnded` and `SongEnded` keeps the request.
- The six e2e specs that use `/classes/new` are updated here.

**`MusicSourcePicker`**
- A searchable list with source-labelled rows: "Spotify playlist · in MOWL", "Swedebeat mix",
  "classbeat picks songs (Spotify)", "No music".
- The destination is shown separately.
- It claims no audio features we don't have.

**Workspace `/classes/<id>` (MA3, MA4)**
- **Left:** `ChatPanel`, starting with the request and the three-part interpretation.
- **Right, in order:**
  - the designing state;
  - a hand-off refusal, with its existing copy, if there was one;
  - inline song approval (pick);
  - `RevisionView` with game markers;
  - history;
  - **Approve & save to MOWL** (the existing `useSaveFlow` and its confirmation).
- A status pill.
- On a phone: Chat | Class tabs and a bottom save bar.
- Planned songs per section where the source supports it (MA4).

**Strings:** English keys go on the untranslated list; MA6 translates them.

### E. Split on touch (CLAUDE.md rule 2: files over 300 lines; each PR moves out its part first)

| File (lines) | Moved out | PR |
|---|---|---|
| `agent/schemas.py` (457) | `agent/stored_draft.py`; later `Brief` → `agent/brief.py` | MA1a-1, MA1b-1 |
| `design/revise.py` (408) | legacy rewrite and explicit constraints → `design/revise_base.py` | MA1a-1 |
| `songs/revise.py` (447), `classes/routes.py` (606), `jobs/routes.py` (439), `classes/history.py` (379) | the stored-draft read helpers each one uses → `classes/stored_reads.py`, or MA0 records, by file name, that a one-line import swap with a net change of 0 needs no split | MA1a-1 |
| `classes/service.py` (433), `saving/kind.py` (388), `saving/update.py` (411), `classes/duplicate.py` (476) | the `game_retired` check goes into one shared helper in `design/games_policy.py`; each call site is a net-zero swap, or moves out one cohesive part, named in MA0 | MA1a-1 |
| `design/kind.py` (377) | the music admit → `design/kind_music.py` (moved earlier, because MA1b-1 wires `check_explicit` into it) | MA1b-1 |
| `agent/model_input.py` (398) | → `agent/interpret_input.py` | MA1b-2 |
| `agent/records.py` (349) | the interpretation bounds → `agent/interpretation_record.py` | MA1b-1 |
| `jobs/user.py` (359), `classes/service.py` (433) | class creation SQL → `jobs/class_rows.py`; kind lists → `classes/kinds.py` | MA1b-3 |
| `NewClassPage.tsx` (414), `ChatPanel.tsx` (412) | `AdvancedForm.tsx`; `ReviseRun` | MA2, MA3 |
| `api/classes.ts` (550), `api/jobs.ts` (332) | `api/revisionTypes.ts`, `api/draftTypes.ts` | MA1c |

`egress.py` is not touched.

### Out of scope
Clarifying questions; direct segment editing; running games; importing MOWL workouts; any licence
check; MOWL runtime behaviour for teams and the zone 3 fallback; audio features.

## Issues and PRs (one issue per PR, AGENTS.md gates)

| # | PR | Depends on | Developer / Reviewer |
|---|---|---|---|
| MA0 | Plan doc, ADR 0029 (article quoted; A to E; back-to-back evidence; illustration provenance as the owner stated it; the Ghost Target policy; the concurrent hand-off test), Amendment 18, status, milestone and issues | none | Architect / **Opus** |
| MW1 | mowl-workouts v0.11.0 (Ghost removed, 10 s rules, unknown-id read, back-to-back outcome, changelog) | MA0 | Sonnet / Sonnet |
| MA1a-1 | Wheel bump, strict `Game` with the four, `stored_draft.py`, read sites, `game_retired` on 6 actions, legacy paths and compatibility tests | MW1 | **Opus / Opus** (adds a refusal to Save and Update: approvals and external writes) |
| MA1a-2 | `games_catalog`, `games_policy`, prompts, diff wording, evals and `suite_version` | MA1a-1 | Sonnet / **Opus** (prompts, eval gate) |
| MA1c | Game display, illustrations and slots, frontend type split | MA0 | Sonnet / Sonnet |
| MA1b-1 | `brief.py`, `Brief.games`/`explicit`, `request_policy`, `check_explicit`, `provenance.interpretation`, wired into `design_draft` and revise | MA1a-2 | Sonnet / **Opus** |
| MA1b-2 | `interpret` stage, `interpret_input.py`, model-input allowlist, `GROUP_STAGES`, eval cases, fake-model fixture | MA1b-1 | Sonnet / **Opus** |
| MA1b-3 | `interpret_request` job, `classes.interpretation` migration, the web-side `design-from-interpretation` route, credit rules, kind lists, splits | MA1b-2 | **Opus / Opus** |
| MA2 | Start screen, picker, Ideas, Advanced route, links, 6 e2e specs | MA1b-3, MA1c | Sonnet / Sonnet |
| MA3 | Workspace, three-part interpretation, phone tabs | MA2 | Sonnet / Sonnet |
| MA4 | Inline song approval, planned songs per section, Change music | MA3 | Sonnet / Sonnet |
| MA5 | `e2e/tests/ai-first.spec.ts` done-when suite and screenshot refresh | MA4 | Sonnet / Sonnet |
| MA6 | Swedish and Danish catch-up translation | MA5 | Sonnet / Opus |

There are two lanes after MA0: the backend (MW1 → MA1a-1 → MA1a-2 → MA1b-1 → MA1b-2 → MA1b-3) and
MA1c. MA2 joins them. Waiting for the owner, not blocking implementation: the live eval suite run
before release (ADR 0012 §5).

## Done when
1. One message produces a complete class.
2. A revision changes one thing and keeps the rest, as the diff shows.
3. The first screen has no playlist grid and labels every music source.
4. The preview updates when a revision arrives.
5. Nothing is written to MOWL without the save confirmation.
6. The Advanced form works, and its values count as explicit.
7. The phone layout passes.
8. **Games:**
   - The catalogue has exactly four. Ghost Target is refused by the tool schema, generation and
     save.
   - A legacy Ghost Target revision can be read and compared. It is refused at Make current,
     Save, Update, Refit, Change songs and Duplicate with `game_retired`, and revises to a regular
     interval with a Not as asked note.
   - Every row of ADR 0029's constraint table has a test, including 9 s and 10 s games, the
     following interval, crossing a segment, the last interval and the back-to-back outcome.
   - The fake MOWL receives `IntervalGameID` 1, 4, 5 or 6, and the game interval's PulseZone,
     FTP, MAP and FTHR fields equal `ZONES[zone]`.
   - "Power Pull after climb 2, 2 min" produces exactly that. A generic game request produces one
     of the four. Each of the five editor operations changes only its target.
   - The real type shows in the preview and in history.
   - The Ideas cards show light and dark illustrations.
   - Running classes never get a game.
   - Nothing claims a licence check.
9. **Explicit vs. assumed:**
   - No explicit value is replaced by a default, and every one that couldn't be met appears under
     Not as asked with its reason.
   - Every stored quote or hint is a substring of the request.
   - A running request with a game shows Not as asked.
   - A Swedish request is interpreted.
   - A failed interpret or a refused hand-off uses no credit.
   - A missing service ends in a stated reason.
   - The interpret route is eval-gated.
10. e2e and axe pass on the compose stack with fakes.

## Verification
- **mowl-workouts:** its own tests for the 10 s rules, the last interval, the unknown-id read and
  the back-to-back outcome.
- **Backend** (targeted `uv run pytest`, ruff, pyright):
  - `test_games_catalog.py`;
  - `test_games_policy.py` (one row per ADR table row, plus "the library refuses 9 s");
  - `test_stored_draft.py` (each read site; `game_retired` at each of the 6 refusals; the revise
    rewrite);
  - `test_request_policy.py`, `test_check_explicit.py` and `test_interpret.py` (fake provider,
    injection);
  - `interpret_request` admit, worker and credit tests, plus the `design-from-interpretation`
    route: idempotent, owner lock, refusal, call after a reopen, and **two concurrent calls (one
    job, one credit, the same answer to both)** (Postgres container);
  - `test_mowl_state.py` (ids 1, 4, 5 and 6; PulseZone, FTP, MAP and FTHR equal `ZONES[zone]`);
  - `test_diff.py`;
  - the eval structure tests and the `GROUP_STAGES` test.
- **Frontend** (Vitest, lint, typecheck, i18n guards):
  - the Ideas cards (adding a phrase, hidden without the gate or for running, light and dark);
  - the chart marker and its name;
  - compare wording and the legacy label;
  - the three-part interpretation;
  - the picker;
  - the workspace at desktop width and with phone tabs.
- **e2e** (`ai-first.spec.ts`, compose stack, fake model, MOWL and Spotify):
  1. Ask for Power Pull after climb 2; the badge appears.
  2. Revise to Overflow; history names the change.
  3. Save through the confirmation; the fake MOWL stores id 6 and the neighbouring intervals are
     unchanged.
  4. A length conflict on a 60-minute plan shows under Not as asked.
  5. A hand-off refusal is shown.
  6. axe at 1440 and 390 px, in light and dark.
- **Screenshots:** `classbeat-screenshots/tooling/run.sh`.

## Execution
MA0 (this plan, ADR 0029, Amendment 18) merges first with a fresh Opus Reviewer's PASS. Then the
GitHub milestone "MA AI-first class design" and one issue per row above; each PR follows the
AGENTS.md loop and gates. MW1 lands in `magpern/mowl-workouts` under its own `CLAUDE.md`. After
MA6 a fresh Opus integration review checks the done-when list against `main`.
