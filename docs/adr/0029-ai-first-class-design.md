# ADR 0029: AI-first class design: free-text start, four MOWL games, explicit vs assumed

Status: Accepted (owner decisions 2026-11-10, roadmap Amendment 18). The plan and the PR split
are in [docs/plans/ai-first-design.md](../plans/ai-first-design.md).

Amends: [ADR 0023](0023-multilingual.md) §7 (the interpreter reads sv and da requests).
Refines: [ADR 0007](0007-classes-revisions-and-saving.md) (chat and revisions),
[ADR 0011](0011-tiers-quotas-and-admin.md) (games become a brief field; the gate is unchanged),
[ADR 0012](0012-eval-suite-and-model-providers.md) (a new gated stage),
[ADR 0013](0013-ui-design-night-studio.md) (game illustrations),
[ADR 0028](0028-ui-refresh.md) (New class becomes a prompt; the form moves to Advanced).

## Context

The owner's proposal (`classbeat-redesign/CLASSBEAT_REDESIGN_PROPOSAL.md` in the
`magpern/classbeat-screenshots` repository)
replaces the form-style New class page with one free-text request. A two-panel workspace follows
it: chat on the left, a live preview on the right. Nothing is saved to MOWL before the user
explicitly approves it.

The back half of that flow already exists: the revision chat (`class_messages`, `revise_draft`),
immutable revisions with history and compare, the validated `WorkoutDraft` tool output, and Save
or Update with a `content_hash` approval. The gap is the start: every class still begins from a
structured `Brief`.

The owner also asked for full MOWL game support. The code knew five games, with no type shown
anywhere, and none of the official game constraints.

## Decision

### 1. Owner decisions (2026-11-10)

1. **Defaults first, then refine.** v1 asks no clarifying questions. Assumptions are shown, and
   the user corrects them in the chat.
2. **The structured form stays as "Advanced"** at `/classes/new/advanced`. Free text is the default
   at `/classes/new`.
3. **Playlist and mix names may go to the interpreter.** It gets names only, never ids, and only
   an index comes back. The music picker always shows the choice and can override it.
4. **Games.** Exactly four cycling games. An explicit request keeps its type, placement and
   length. The AI picks a type when the request names none. The real type shows in the preview
   and the history. The article's constraints apply. Running gets no games.
5. **Ghost Target is removed from everything we support:** what we offer, AI generation,
   validation and saving, and `mowl-workouts` too. Historical records may stay readable through a
   separate read-only compatibility schema. An existing Ghost Target interval never silently
   becomes another game.
6. **The MOWL article as the owner transcribed it is the authority** (section 2). We invent no
   rule it doesn't state.
7. **A default never silently overrides an explicit instruction.**
8. **No licence check.** MOWL manages licences. classbeat states the requirement and never claims
   to have checked it.
9. **No separate game engine.** Games reuse the schema, the validators, the revision pipeline and
   the MOWL save path.

### 2. MOWL game facts

Source: MOWL Help Center, "How to add a GAME", by Brian Overkaer,
https://help.mowl.com/hc/en-gb/articles/17909652657564-How-to-add-a-GAME. The owner transcribed it
on 2026-11-10; help.mowl.com refuses automated fetches.

| # | The article | classbeat |
|---|---|---|
| G1 | The Session Designer offers exactly four games. **Racetrack**: teams complete the most laps before time runs out. **Power Pull**: two teams compete on power-to-weight ratio (W/kg), like a tug of war. **Fast Lane**: teams compete for the highest average speed. **Overflow**: teams earn progress by staying in their target intensity zones | The catalogue holds exactly these four |
| G2 | Games are a commercial feature for instructors at a studio with a commercial MOWL licence. They are added in the desktop Session Designer, never during a session, and the instructor can switch them off before class | One notice line wherever games are offered, and on a class that has a game. No check and no claim of one. Our own plan gate `games` (ADR 0011) is unchanged |
| G3 | A game lasts **at least 10 s** | `workout.validate` (mowl-workouts v0.11.0) |
| G4 | The interval **right after a game** lasts **at least 10 s** | `workout.validate` (v0.11.0), across segment boundaries. A game that is the last interval has no next interval and passes. The cooldown rule (last segment, ≥ 60 s) makes that rare |
| G5 | The article sets no maximum length, no maximum count, no required recovery and no minimum spacing | We add none of these |
| G6 | Games work with power and with heart rate. Longer games are recommended for heart rate, which lags behind effort | Every cycling interval body already carries its zone's PulseZone, FTP, MAP and FTHR ranges from zone set 1 (`interval_body`). The heart-rate advice is prompt guidance, not a rule |
| G7 | With too few riders, MOWL replaces a game with a zone 3 interval (a target game with a zone target is the exception) | MOWL runtime behaviour. Not implemented |
| G8 | Most games reward higher team effort | Prompt guidance on placement and overall load. Not a validator |
| G9 | There is no countdown before a game; the workout overview shows which game comes next | A cue may announce the coming game. Nothing claims a countdown |
| G10 | Teams are assigned at random, 2 to 4 by rider count (up to 8: 2; 9 to 11: 3; 12 or more: 4). Games work with THE PLAYER, TEAM PLAYER, VISUALS and PERFORMANCE PLAYER | Not implemented |
| G11 | Editor operations: add a game; replace an interval with a game (keeping its length); change the type; remove; change the length; for a target game, change the intensity | Each one is a revision-chat operation with its own eval case |

**Back-to-back games.** The article doesn't forbid two games in a row. The library's rule ("games
and FTP tests must be separated by a regular interval", `workout.py:151-154`) has its own
evidence. `mowl-workouts` commit 7378d91 (2026-10-27) recorded a live test in `docs/mowl-api.md`:
"The server saves two FTP tests in one workout, or two games back to back, but the app then
refuses to open the workout for editing." That is a fact about the MOWL app, not an assumption,
so **the rule stays** and the policy test reads "refused". This is the evidence branch of the
plan, so no owner question is needed.

**What the code did before this ADR.**
- Five games: `Game` Literal (`agent/schemas.py:64`) and the library's
  `GAMES = {racetrack 1, power_pull 4, fast_lane 5, overflow 6, ghost_target 7}`.
- Written as `IntervalGame.IntervalGameID` and read back in `mowl_state.py:195`.
- Games were interchangeable apart from the id: the target is the interval's zone.
- No 10 s rules.
- The UI showed no game type.
- Running had no games anywhere (ADR 0018).
- A brief's `races` (15 to 35 s bursts in zone 6 or 7) are not Racetrack games.

### 3. Games in code

**mowl-workouts v0.11.0 (MW1)**
- `ghost_target` is removed from `GAMES` and from the MCP prompt.
- Reading back an id that isn't in `GAMES` gives `IntervalGameName` or `"unknown:<id>"`, never
  another game.
- `validate` gains G3 and G4.
- The back-to-back rule stays, with its evidence linked.
- The changelog notes that removing a `GAMES` value breaks CLI and MCP users.
- classbeat stays on 0.10.0 until MA1a-1.

**Catalogue: `agent/games_catalog.py`** (pure; not under `design/`: the prompts in `agent/` use it
at import time and `agent/` cannot import `design/`, a circular import)
- The four types, each with an i18n key, its MOWL id (looked up from the library's `GAMES`) and
  the G1 sentence.
- classbeat's own advice, labelled as advice and not MOWL fact:
  - Racetrack: a sustained competitive push.
  - Power Pull: fair across body sizes; suits climbs.
  - Fast Lane: flat, fast cadence.
  - Overflow: steady control; suits mixed groups.
- The G2, G6 and G9 facts.

**Policy: `design/games_policy.py`** (pure) covers only:
- `game_retired` (a stored game outside the catalogue);
- cycling only;
- the stored-draft check.

The 10 s rules live in the library alone, which covers both the tool loop
(`preview_draft`/`submit_draft`) and saving. A classbeat test checks that the library refuses 9 s.

**Strict and stored schemas**
- `Game` is the four. The model-facing tools, `to_spec` and saving stay strict;
  `saving/approval.py:164` stays strict.
- A new `agent/stored_draft.py` (split out of `schemas.py`) holds `StoredWorkoutDraft`,
  `stored_draft` and `draft_model`. In it `StoredGame` is the four plus `ghost_target`. It is
  only for reading historical records.
- These read sites switch to it: `classes/history.py:197`, `classes/routes.py:234`,
  `design/diff.py:27`, `jobs/routes.py:373`, `design/revise.py:216`, `design/refit.py:207` and
  `songs/revise.py:303`. History, compare and diff stay safe because `to_spec`, `tss` and
  `total_seconds` don't look up `GAMES`.
- **`game_retired` refuses six actions on a legacy draft:**
  - Make current;
  - Save;
  - Update;
  - Refit;
  - Change songs;
  - Duplicate, which goes through the strict `spec_of` and would otherwise answer 500.

  Each refusal happens before the save path: `mowl_state.py:96`, `interval_body` and `validate`
  look up `GAMES` and would raise. `to_spec` does not look up `GAMES`.
- **Revising from a legacy base** turns that interval into a regular interval with the same
  length and zone, and records a Not as asked item `game_retired` before the model sees the
  draft. The model never picks a replacement game.
- One compatibility test per path.

**Prompts** (`design.py`, `revise.py`, `soundless.py`)
- The catalogue, rendered from the pure module, replaces the generic games line.
- A race is not a game.
- Choose a type only when none is given, and never invent one.
- Think about the load around a game and the requested effort.
- With heart rate, longer games read better.
- A cue may announce a game; there is no countdown.
- Leave a margin over 10 s, because fitting to the music can shorten an interval.
- Running prompts stay free of game words.

**Change summary** (`design/diff.py`): segment index plus type, for example "segment 3: Power Pull
→ Overflow", so it fits the 1024 bytes of `change`. Built as `game_changes: [{segment, before,
after}]` (at most 8); `segment` is counted from 1, as the page counts segments.

**Display**
- `DraftInterval.game` on the frontend.
- A marker on `ProfileChart` (a lucide icon with an accessible name).
- The type name and a small illustration on segment cards and in `DraftSummaryView`.
- Game wording in compare.
- "Ghost Target (no longer offered)" for legacy reads.
- The G2 notice line.

**Illustrations.** These are original AI-generated classbeat illustrations, **made with MOWL game
screenshots as visual references** (owner, 2026-11-10). The source is
`classbeat-screenshots/classbeat-redesign/Games/`, one light and one dark version per game.
- This ADR does not claim they were made without reference to MOWL's screens.
- The owner decided to use them under ADR 0013's rule that nothing is copied exactly and that
  there is no MOWL logo, owl or wordmark (ADR 0013, lines 29 and 126).
- They are served from our origin as `game-<type>-<theme>-<width>` renditions through four new
  `SlotId`s (`components/imagery/imageSlots.ts`).
- The source file `racetrach-light.avif` is renamed on import.

### 4. Explicit instructions vs. assumptions

**The interpreter's output.** Its `submit_brief` tool returns an `InterpretedRequest`. Each field
is `{value, origin: explicit | inferred, quote?}`; a field the model leaves out becomes `default`.
The fields are:
- sport, length, effort and class type;
- the counts of climbs, sprints and races, and the FTP test;
- the music choice, as an index;
- `games: [{type: one of the four | "any", placement?: {position: start | early | middle | late | before_cooldown} | {relation: after | replaces | in, anchor: climb | sprint | race | interval | warmup | cooldown, ordinal: 1–20}, duration_sec?}]`.
  The placement is closed at interpret time: there is no draft yet, so "after climb 2" becomes
  `{relation: after, anchor: climb, ordinal: 2}`, and no model text goes into the Brief.
  `check_explicit` maps it onto the draft's segments by kind and order.

**No model text goes back to the model.** This keeps the guard in `agent/revise.py:11-14`.
- The Brief holds only closed enums and numbers. A placement hint becomes a segment index or a
  position before it is stored.
- `quote` and hints are kept only if they are verbatim substrings of `brief.text`, the user's
  own request.
- Playlist and mix names go through `clean_text`: at most 50 names of 60 characters each, in a
  numbered data block, and only an index comes back. The index is range-checked against the list
  that was sent; an out-of-range index is treated as no match. `title` is never model input.

**`design/request_policy.py`** (pure)
1. An explicit value is never replaced by a default.
2. An explicit value that breaks a hard limit is never changed quietly. The hard limits are the
   plan length, the games gate, a game on a running class, a game under 10 s, and a sport that
   isn't offered. The policy uses the nearest allowed value or leaves the item out, and records a
   **Not as asked** item with a reason code. A game is never swapped for another.
3. Inferred and default values are recorded as **Assumed**.
4. Music order for cycling: the override, then a name match, then pick (when Spotify is ready),
   then the first MOWL playlist, then soundless. For running (ADR 0018): MOWL playlists only,
   otherwise Not as asked `needs_mowl_playlist`.
5. The allowed sports go to the interpreter.

**`Brief`** (moved into `agent/brief.py`)
- Gains `games` (at most 6) and `explicit` (at most 20 paths).
- Both are left out of the serialization when empty, so existing briefs stay byte-identical.
- `games` is cycling-only. ADR 0011 said "games are not a field of the brief"; now they are. The
  plan gate is unchanged: `request_policy` drops a game on a plan without games (Not as asked
  `plan_no_games`), and the admit gate check (`quota/gates.py` `check_brief`) also refuses
  `Brief.games` with `feature: games`, so a hand-crafted request can't get past it.
- The worst case fits the 4096-byte cap, checked at admit.

**`check_explicit(brief, draft)`** (pure)
- Checks length, each explicit game's type, placement and duration, and the counts, each within a
  set tolerance.
- A miss is a tool error inside the existing correction budget. If the draft still misses, a Not
  as asked item is recorded.
- It runs in `design_draft`, so values from the Advanced form count as explicit, and in revise,
  where the explicit list is sent as constraints and the new request's explicit items layer on
  top.
- Explicit values win over ADR 0017 soft rules.

**Storage**
- Items are `{path, origin, reason_code?, value, quote?}`.
- They are stored in `provenance.interpretation` on design and revise revisions, bounded in
  `agent/records.py`.
- Decision (MA1b-2, #880), the `stated_unchecked` question: no new origin value. The interpret
  stage passes `explicit` or `inferred` through as the model stated it. `check_explicit` checks
  counts and games only, so a stated `intensity` or `class_type` is never reported as missed;
  whether the page shows it under **As you asked** is decided in MA1b-3 with the page's catalogue.
- With no revision (a refused hand-off) they go in a new `classes.interpretation jsonb` column
  with an `octet_length` check.
- The frontend renders three parts from catalogue keys: **As you asked**, **Not as asked** and
  **Assumed**. The assistant message body stays the model's short note.

### 5. Two jobs and a web-side hand-off

**`interpret_request`**
- Input: `{request 1–2000, music_override?, sport_override?, mowl_connection_id,
  spotify_connection_id?, title?}`.
- **Admit:** creates the class and stores the request as message seq 1. It takes **no class
  credit** but counts toward the cost cap and the per-user job limit.
- **Worker:** the `interpret` stage, then `request_policy`. It stores the resolved
  `{brief, music}` and the items in `classes.interpretation` under its fence, then completes.
- **It does not enqueue.** The worker cannot import `jobs.service` (`test_session_guard.py`).
  While it is running, a design job would also be refused by the one-open-job-per-class index.

**`POST /api/classes/{id}/design-from-interpretation`** (the `classes/handoff.py:328-353` pattern)
- Reads `classes.interpretation` under the owner lock.
- Enqueues the **unchanged** `design_draft` or `design_with_songs` through `JobEnqueue`, with their
  own admit checks and the class credit.
- The idempotency key is derived from the interpret job id, so the route is idempotent.
- A refusal (`consent_missing`, `pool_busy`, quota and the like) is stored on the class and shown.
  No credit is used.
- The browser calls the route when the interpret job completes. After a closed tab, the class
  page finds an interpretation with no design job and no revision, and calls it again.
- **Two concurrent calls admit exactly one design job, use one credit, and both callers get the
  same job id and state** (owner, 2026-11-10). If the hand-off is refused, both callers get the
  same stored refusal code and no job or credit exists. This is tested with two sessions on
  Postgres.

**Other rules**
- A failed interpret uses no credit.
- Every hard-coded list of job kinds gets the new kind, or a test showing it doesn't need it
  (`classes/service.py`, `design/sport.py`, `agent/records.py`).
- The first design revision's `request_message_id` is not seq 1, because the request already
  reaches the model in `brief.text`.
- `chat_full` counts seq 1.

### 6. Eval gating

- An `interpret` key goes in the design group of `GROUP_STAGES`, with suite cases:
  - explicit values kept;
  - game type and placement, and "any";
  - a playlist name written like an instruction (injection);
  - a running request asking for a game;
  - Swedish and Danish requests.
- `suite_version` is bumped. The cycling prompt pins change on purpose.
- **Stale routes are served, with a warning (ADR 0012 §5, `models/routes.py:376-394`).** Until the
  owner's live suite run, a route whose prompt versions were not recorded is still used for
  cycling and is flagged stale. Only `newer_failure` makes a route unusable. Running and soundless
  classes lose coverage and are refused, as ADR 0012 already describes. MA does not change that
  rule. An unevaluated `interpret` route is therefore safe by construction rather than by eval:
  its output passes closed validation (enums, numbers, a range-checked index, and quotes accepted
  only as substrings of the request; §4), so injected text cannot reach the Brief or a later model
  call. The eval run is about quality. It is listed under "Waiting for the owner" and does not
  block implementation.

### 7. ADR 0023 §7 amendment

Generated classes stay English. The interpreter accepts requests in English, Swedish and Danish,
and the phrases that Ideas adds to the prompt use the UI language.

### 8. Frontend

**Start screen, `/classes/new`**
- The prompt box and **Design my class**.
- Starter ideas.
- Chips for connected services only.
- **Music ▾**: a searchable `MusicSourcePicker` whose rows are labelled by source ("Spotify
  playlist · in MOWL", "Swedebeat mix", "classbeat picks songs (Spotify)", "No music"). The
  destination is shown separately, and the picker claims no audio features we don't have.
- **Ideas for your class**: Climbs, Sprints, Intervals and Games, collapsed by default.
  - Games shows four cards with the illustration, the name, the G1 sentence and the G2 line.
  - A tap adds a phrase to the prompt. There are no counters.
  - Games appear only if the plan includes them, and are marked "cycling".
- An Advanced link to today's form.
- If MOWL isn't connected, the `no_mowl` state.

**Workspace, `/classes/<id>`**
- Left: the chat, starting with the request and the three-part interpretation.
- Right:
  - the designing state;
  - a hand-off refusal, if there is one;
  - inline song approval;
  - the preview with game markers;
  - history;
  - **Approve & save to MOWL** (the existing save flow and its confirmation).
- A status pill.
- On a phone: Chat | Class tabs and a bottom save bar.

**Other changes**
- Home presets link to Advanced.
- The shell marks Advanced as "New".
- "Try again" keeps the request.

### 9. Split on touch

The plan's section E names, for every file over 300 lines that MA touches, the part each PR moves
out (CLAUDE.md rule 2). Where a PR only swaps one import line and the net change is 0, rule 2 is
met without a split. The PR names each such file. `egress.py` is not touched.

## Alternatives

- **One `design_from_request` job that chains into the design.** Rejected. Music and sport admit
  checks need what interpret decides; a retry would interpret again; and the open-job index and
  the worker import guard forbid enqueueing from the worker.
- **Clarifying questions with a waiting state.** Deferred by the owner (decision 1).
- **Keep Ghost Target, but don't offer it.** Rejected by the owner (decision 5).
- **Drop the back-to-back rule because the article doesn't state it.** Rejected: the 2026-10-27
  live evidence shows the MOWL app can't open such a workout.
- **A minimum game length of 60 s** (the app's default length). Rejected: the article's minimum is
  10 s, and 60 s is only a default.
- **Check the MOWL licence through the API.** Rejected by the owner (decision 8).

## Consequences

- New Class is no longer a form. Every e2e spec that uses `/classes/new` is moved in MA2.
- Two new job steps (interpret, hand-off) add one short model call per new class. Its cost is
  counted, and it uses no credit when it fails.
- Every prompt change makes the eval routes stale until the owner's next live suite run.
- `mowl-workouts` v0.11.0 removes a `GAMES` value. CLI and MCP specs that use `ghost_target` stop
  validating.
- Historical Ghost Target revisions stay readable but can't be saved, duplicated or refitted.
