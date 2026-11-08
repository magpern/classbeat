# ADR 0017: Class patterns as soft rules (#388)

Status: Proposed (issue #388; the owner's request of 2026-11-01 is its premise).

Related: [ADR 0006](0006-agent-runs.md) (the agent loop, `model_input`, provenance, `prompt_version`),
[ADR 0007](0007-classes-revisions-and-saving.md) (classes and revisions),
[ADR 0008](0008-song-picking.md) (the song stages; not changed here),
[ADR 0012](0012-eval-suite-and-model-providers.md) (the suite, the manifest, the gate),
[ADR 0013](0013-ui-design-night-studio.md) (UI), the threat model (no new row, see the Threat model section).

## Context

The designer already knows a few "typical class" habits, but they are prose in the system prompt
(`classbeat.agent.design.SYSTEM_PROMPT`, "a guide, not a rule"), written from the workout library's
own rides. #388 adds measured evidence: an owner-run, read-only analysis of **a professional studio
library**, a reference set of public classes available to the owner's account (209 indoor-cycling programs, aggregate statistics only, raw data kept outside any
repository) says how a professional class is typically built per class type and length. Headlines
that matter to this decision, all medians or shares over many classes:

- the timeline runs 2 to 5 minutes shorter than the class label; TSS is about 1.42 x minutes - 5;
- the warm-up is a zone 1 -> 2 -> 3 ramp, the first zone 4+ comes no earlier than about 6 minutes;
  the cool-down is one zone 1 interval of 2 to 3 minutes;
- plain cycling classes stay in zones 1 to 4; interval classes add zones 5 and 6 (about 11, 16 and
  19 % of the time at 30, 45 and 60 minutes); HIIT is polarised zones 5 to 7 with 10 to 30 s work;
- zone steps are one at a time (98.5 %); standing is 5 to 10 % of the time and almost never in
  zones 1 and 2; cue cards and games are not used;
- evidence is **strong** for cycling and interval classes at 30, 45 and 60 minutes (n 29 to 33, but
  much of it is the same class re-released at three lengths, so the effective n is lower),
  **moderate** for HIIT (15), **weak** for journey, 75 minutes and FTP test. Running (P9, 2026-11-03): see §7.

What exists today (read from `main`):

- **Hard rules** are the workout library's `workout.validate()` (one cool-down segment, last, at least 60 s;
  zones 1 to 7; at most one FTP test of 3, 5 or 20 minutes; at most three hours). `check()` in
  `agent/design.py` runs it before and after fitting; a failure is a `ToolError('invalid_draft')`
  the model must fix, and nothing is stored.
- **Soft signals** already exist in two forms, neither stored: `quality.report` issues (coded in
  `model_input.ISSUE_CODES`: `standing_too_long`, `zone_run_too_long`, `cadence_jump`, ...), and
  `music_warnings` (longer than the music, timing, unknown BPM). Both are returned by
  `preview_draft` and `submit_draft` as `quality` and `warnings`, and a run may end with them
  unfixed. The class page computes its tiles from the revision's segments in the browser.
- **Provenance** (ADR 0006 §6) records the prompt's SHA-256, not any data the prompt was given.
- **Evals** (ADR 0012 §4) are a manifest of categories with deterministic checks, `suite_version`
  and a digest test; the gate needs 90 % of a category's runs and every run of the hard checks.

The risk to avoid is turning a statistic about one library into a law: the instructor's brief is
the product, and a class a professional studio would not build can be exactly what the instructor
asked for.

## Decision

### 1. What a soft rule is

A **soft rule** is a named, versioned expectation about a finished draft that is checked
deterministically by the backend and reported as a **note**, never as a refusal.

| | Hard validation | Soft rule |
|---|---|---|
| Source | `workout.validate()`, the workout library | a **profile** file (§2) |
| On violation | `ToolError('invalid_draft')`, the model must fix, nothing stored | a `RuleHit`; the draft is accepted and stored |
| Who may override | nobody | the **brief** (always), the model (deliberately), the user (dismiss, §4) |
| Counts against a run | yes (`limits`, `standard` checks) | never by itself (§5) |

A `RuleHit` has: `rule` (stable id, for example `warmup_short`), `severity` (`info` or `warn`),
`metric` (what was measured, from the fixed catalogue below), `value` (what the draft has),
`bounds` (the profile's low and high), `profile` (id and version), and `rationale_id`.
**Severity** is `warn` only for rules with `strong` evidence and a bound that is a 5th or 95th
percentile; everything with `moderate` evidence is `info`; **`weak` evidence ships no rule at all**.
There is no `error` severity: a soft rule cannot block.

**The brief always wins.** A rule is evaluated against the draft, and the brief can switch it off:
a rule carries `brief_overrides`, the brief fields that make its expectation wrong (the intensity
`easy` or `hard`, `sprints`, `races`, `ftp_test`, a non-empty `style` or `text`), read from the
**stored brief** only. Two limits are deliberate: a revise request's text is not in `classes.brief`
and is **not** passed to the engine, so "make it harder" in chat does not mark a hit as explained
(the model sees the request and may deviate; the note then stays as `warn` or `info` until the
user dismisses it); and a **derived** `class_type` (§2) never counts as explained by the brief:
only a field the user set themselves does, so `interval` derived from `sprints` does not make its
own deviations "explained". When one applies,
the hit is still computed but its severity is lowered to `info` and marked `explained_by_brief`, so
the UI can say "you asked for this". Length is never a rule: `total_minutes` is in the profile for
the record but no rule is generated from it (the instructor chose the length; the 2 to 5 minute gap
between label and timeline is a fact about the studio library, not a defect).

The model may deviate on purpose: the prompt says so (§3) and the eval suite asserts it (§5). A hit
that remains at submit is stored as part of the revision's computed view, not as a model decision.

**Metric catalogue** (v1, all computable from the fitted spec; each is in the profile's aggregate
tables): `warmup_s_before_first_zone3`, `first_zone4plus_s`, `cooldown_s`, `max_zone`,
`zone5plus_time_share`, `zone4plus_time_share`, `zone4plus_share_in_first_25pct`,
`mean_zone`, `tss_per_minute`, `segments`, `intervals`, `standing_time_share`,
`zone_step_over_one_share` (zone steps larger than one, excluding the cool-down), and the
class-type rules `no_games_or_cue_cards`. A new metric is a code change with a profile schema
bump (§2), reviewed like any rule.

### 2. Where the rules live

**A versioned data file per profile, in the repository**, next to the designer, not in the
database: `backend/src/classbeat/design/profiles/<sport>/<class_type>_<label>.json` (for example
`cycling/interval_45.json`), plus `profiles/schema.json` (JSON Schema, `additionalProperties:
false` everywhere) and `profiles/index.json` (the registry: profile id -> file, `rules_version`).
The image carries them; nothing is fetched at run time. Reasons: the profile is reviewed in a PR
like code, its digest can be recorded in provenance and the eval manifest, a change to it is a
visible diff, and no migration or admin screen is needed.

A profile file contains **numbers and ids only**:

```json
{
  "schema": 1,
  "id": "cycling.interval.45",
  "version": 1,
  "sport": "cycling",
  "class_type": "interval",
  "label_minutes": 45,
  "evidence": "strong",
  "n": 29,
  "source": "professional studio library",
  "rules": [
    {"rule": "warmup_short", "metric": "first_zone4plus_s", "low": 370, "high": null,
     "basis": "p5", "severity": "warn", "rationale_id": "warmup_ramp",
     "brief_overrides": ["intensity:hard", "ftp_test"]}
  ]
}
```

- `rule` and `metric` come from closed enums in the schema; `rationale_id` keys a table of
  classbeat-written sentences in code (`classbeat.design.rules.RATIONALES`), so **no free text
  lives in a profile** and no wording is copied from a source.
- `basis` is `p5`, `p95` (n >= 25) or `range_10` (observed range widened 10 %, n < 25, which
  forces `severity: info`); the schema enforces both pairings. Thresholds are produced from the
  private aggregate file by a script kept with the private notes (§6), never typed by hand.
- `version` is an integer bumped by any change to the file; `index.json`'s `rules_version` is the
  maximum, and a test fails if a file's digest changed without its version (the same pattern as
  the suite's `suite_version`, ADR 0012 §4).

**Selecting a profile.** A pure function `select_profile(brief) -> Profile | None`:

- `sport` is `cycling` or `running` (§7); `select_profile` picks only from the requested sport's own profile files and never falls back to the other sport; the new-class form and the brief carry no sport until R1 (#463) adds it, so `select_profile_for_brief` is cycling-only for now;
- `class_type` is a new optional brief field `class_type` in `{plain, interval, hiit, journey,
  ftp_test}`; when absent it is derived: `ftp_test` set -> `ftp_test`; `sprints` or `races` > 0
  or intensity `hard` -> `interval`; otherwise `plain`. The new-class form may expose it later;
  the derivation alone is enough to ship (§8, P3);
- the **duration** picks the profile whose `label_minutes` is nearest to `brief.length_min`, only
  within 3 minutes (so 30, 45, 55, 60 and the 75 that ships no rules; a provisional 55 never replaces a strong profile in the window, §7b); anything else, and any
  `(class_type, label)` with no file, returns `None`.

`None` means: no rules, no section in the prompt, no hits, no note in the UI. A missing profile
is never an error and never a warning.

**Provenance.** `class_revisions.provenance` gains `rules: {profile, version, digest}` (or
absent), validated by the same Pydantic model as the rest. It records which profile the **model
saw** when it made that revision; it is not used to recompute notes (§4). The rules are not part of
`prompt_version` (§3 keeps them out of the system prompt), so a profile change does not
invalidate the model gate's `prompt_version` match (P4 touches neither the system prompt nor any
tool name, description or schema, so `prompt_version` does not change), but `compare` and the results header record
`rules_version` (§5).

### 3. How the model receives them

Two channels, neither in the system prompt:

1. **A short section in the first message** (`model_input.first_message` and `revise_message`),
   built from the selected profile: a header line, then rule lines of the form
   `<plain-language expectation> (<bound>; usually; the brief wins)`, produced from the profile's
   rules and the `RATIONALES` table (about 150 to 250 tokens), followed by the standing sentence.
   The whole section, header and standing sentence included, is **at most 8 lines**, so it states
   the first 6 rules of the profile in file order (the file order is the priority order); the
   `rules` hits of the tool results cover every rule. When every rule of a class is dismissed no
   section is built and the revision's provenance carries no `rules`. The standing sentence is:
   "These are patterns from professional classes, not requirements. Follow the instructor's brief
   first; deviate on purpose when the brief or the music asks for it, and keep the draft valid."
   Reasons for the user message: the system prompt's SHA (`prompt_version`) stays stable across
   profile edits, Anthropic's prompt cache of the system block keeps working, and the section is
   absent entirely when no profile applies.
2. **`rules` in `preview_draft` and `submit_draft` results**: the list of `RuleHit`s of the
   *fitted* draft, **violations only**, sorted `warn` first, **at most 5**, each
   `{rule, severity, value, bounds, explained_by_brief}` and one short sentence from `RATIONALES`.
   A draft with no hit adds `"rules": []`. The existing `quality` and `warnings` keys are
   unchanged. `submit_draft` never refuses because of `rules`; a run may finish with hits.

The existing "typical class" bullets of `SYSTEM_PROMPT` stay as they are in the first change; once
the profile section exists, a follow-up may shorten them (a prompt change, so it runs the smoke
set and bumps `prompt_version` like any other). Model text is never copied into a hit, and the
profile is trusted repository data: it is not "content to design with" in the injection sense, but
the section is still built only from enumerated fields (no free text from the brief or the music).

Token budget: the profile section is capped by a test (at most 8 lines, at most 1,200 characters),
and the tool result's `rules` is capped at 5 entries; a draft with no hits costs about 5 tokens.

### 4. How the user sees them

The hits are a **pure function** of the revision's segments, the class's stored brief and the
**current** profile, so they are **recomputed on read** by `GET` of a revision. **Nothing is stored
per revision**, and superseded profile versions are not kept in the image. An old revision
therefore always shows notes under today's rules, however old it is. If the revision's provenance
`rules.digest` is recorded and differs from the loaded profile's digest, the block adds one muted
line, "Notes use the current rules", and nothing else: no error, no blocked action. A revision with
no recorded digest sets no line. When no profile applies today (or the class's stored brief is gone)
there are no notes and no line either, because there is nothing to compare. The provenance digest answers "what did the model see?", never "what do
we show?". If no profile applies to the class today, no block is shown.

- **Where.** The class page's revision view (`RevisionView`) gets a quiet "Pattern notes" block
  under the stat tiles, only when there is at least one visible hit. Each note is one line: the
  plain-language sentence, the value in the draft against the usual range, and a "dismiss" action.
  `warn` is styled as a note (not as an error), `info` and `explained_by_brief` are muted; nothing
  disables Save, Approve or any other action, and nothing is announced by `role="alert"`
  (`role="status"` at most, once).
- **Dismiss** is per class and per rule id, and persists across that class's revisions: a new
  column `classes.dismissed_rules jsonb NOT NULL DEFAULT '[]'` (a list of rule ids, at most 32).
  There is no class `PATCH` route today (`classes/routes.py` has GET, POST `/current` and DELETE),
  so the dismiss and undo route is **new**, owner-scoped like every class write; no new table and
  `classes` is already forced-RLS, so no new RLS surface (the app role's column `UPDATE` grant is
  checked in P5). The column is created by P1b, before the designer reads it.
  A dismissed rule is not shown and not sent to the model on a revise stage of that class, so
  "I like it this way" is also a message to the next revision. Undo is offered once ("Show again").
- **Wording.** Each sentence is written in classbeat's words and says what is usual, never that
  the class is wrong: for example "Most classes of this kind ride at least six minutes before the
  first hard effort. This one starts at four." No reference to a studio, a brand or a count of
  classes beyond "most", and no number from the source other than the profile's bounds.
- **Recording.** What was shown or dismissed is not logged per user. The revision keeps `rules`
  in its provenance (§2); the dismissal list is the only per-class state; both are in the account
  export and deletion paths because they are in `classes` and `class_revisions`
  (ADR 0014): the export registry already covers every non-omitted `classes` column, so P1b only
  verifies export, deletion and the retention registry with a test and edits them only if it fails.

### 5. Evals

A new suite category **`class_patterns`** (group `design`), added to the manifest with a
`suite_version` bump. Fixtures only: synthetic specs and invented briefs written by hand, built
from the profile's bounds, never from a class of the source library. All checks are deterministic
on backend-computed data (ADR 0012 §4's rule).

1. **Engine fixtures (unit tests and `class_patterns` cases with scripted `FakeProvider` runs).**
   For every rule of every shipped profile, a pair: a synthetic draft just inside the bounds
   (no hit) and one just outside (exactly that hit, the right severity). Also: no profile ->
   no `rules` key content; `explained_by_brief` set when the listed brief override applies;
   dismissal removes the hit; a weak-evidence profile ships no rule; at most 5 hits are returned.
2. **Follows the pattern.** Brief of each shipped `(class_type, length)` with no unusual request:
   on the replay or live run the submitted draft has **no `warn` hit for the rules of that
   profile** in at least 90 % of runs (the gate's ordinary category rule). This measures whether
   the section helps. The first live run records the baseline; the number is not guessed before.
3. **Deviates on request (the brief wins).** Briefs that ask for what a rule discourages ("hard
   from the first minute", "no warm-up segment beyond two minutes", "ten sprints in a 30 minute
   plain class"): the run ends `submitted` and the **asked feature is present** (a case predicate,
   as `revisions` does); a hit is allowed and must carry `explained_by_brief`. A model that
   "corrects" the draft away from the brief fails this case.
4. **Never blocks.** A scripted draft that violates every rule is accepted by `submit_draft`, is
   stored, and has Save and Approve available (API-level check); `rules` never appears in a
   `ToolError`.
5. **Budget and hygiene.** The prompt section and the tool result respect §3's caps; the
   hygiene test of §6 is part of the digest.

A rule hit never fails a run on its own (it is not in `limits` or `standard`); only categories 2
and 3 are gate-relevant, under the existing 90 % / every-case-once rules. `compare` and the results
header record `rules_version`, so two runs on different profiles are not compared as equal.
Live runs stay the owner's per-run go-ahead (ADR 0012 §4.4); nothing here calls a model in CI.

### 6. Legal and hygiene rules for the public repo

The repository is public and the source is somebody's product, so:

1. **Only aggregate statistics.** A profile holds percentiles, ranges, counts of classes and
   shares. No class title, description, segment, interval list, instructor name, playlist or any
   other content from the source library, and no excerpt of one, ever, in a profile, a fixture, a
   test, an eval case, an issue, a PR or a commit.
2. **No employer or brand name anywhere** (code, comments, docs, fixtures, branch names, issue and
   PR text, commit messages). The source is called "a professional studio library", also in
   `source` fields and in the UI. The ADR, the profile files and the rationales are written so.
3. **Reproducibility notes are private.** The collection and aggregation scripts, the raw
   snapshot, the exact programs read and the commands used live outside the repository (the
   owner's private notes). The public profile says only
   `source`, `n`, `evidence` and the snapshot date. Re-running the analysis is an owner step.
4. **Mechanical checks.** The schema allows no free text and no extra fields; a test fails on any
   profile string outside the closed enums and the `source` constant; rationales are an allowlist in code, reviewed word by word. A denylist of names cannot live in the public
   repository, so the **owner-run** hygiene script (private) greps the whole repo for the names it
   holds; the PR checklist of every change under `design/profiles/` and `evals/` asks for it.
5. **No third-party content is involved** (no Spotify, MOWL or Swedebeat data, no track title). Profiles are about structure, not music;
   the threat model's data classes (Spotify-derived data, ADR 0010) are untouched, and the rules
   never read a track title.
6. **Statistics are not scores.** The ADR and the UI never claim a class is "good" or "bad" or
   that it matches a professional; they say what is usual.

### 7. Running (P9, shipped 2026-11-03)

Running profiles exist since P9 (#401, #486): two files under `profiles/running/` (`interval` classes of 30 and
45 minutes), from 64 programs of a professional studio library collected on 2026-11-03 and analysed under §6.
What holds for running, as built:

- **Evidence rule, written down here (it was only in the runbook):** `moderate` needs 15 to 24 classes
  after identical timelines are counted once, from at least 5 independent sets (camps of one studio), and
  no two classes of a cell agreeing on 90 % or more of their per-second zone timeline; `weak` below that
  and `strong` from n >= 25 as for cycling. The two shipped cells have 16 and 18 classes from 5 and 6
  sets and are `moderate`: every rule is `info`, basis `range_10`, never blocking.
- **Weak cells ship nothing:** plain 30, plain 45, plain ~55, interval ~55 and the single test classes have
  too few classes or one set; `select_profile` returns `None` for them, and a running class of such a type
  gets no rules, no section and no note. **No threshold is invented, borrowed from cycling or estimated.**
- **Left out on purpose:** the TSS-rate rule (the running load model is not calibrated), standing (running
  has none) and a short cool-down rule (its bound fell below the 60 s hard minimum).
- **Wording:** four running sentences; a sentence that names the activity is tied to its sport and the
  loader refuses a profile that uses another sport's sentence. Schema stays 1 (only enum additions); the six
  cycling profiles, their digests and `rules_version` 1 are unchanged.
- **Incline:** no incline metric yet: a draft has no gradient field until R1, and only 9 of the 34 interval
  classes use incline above the base setting. The incline aggregates are in the P9 PR description.
- **Not wired:** no brief can select a running profile until R1 adds a sport to the brief.

### 7a. Interval, sprint, climb, work/rest and intensity metrics (#644)

Six metrics and twelve rule ids (a low and a high one each) were added as code, so that the next
cycling refresh can ship bounds for them: `longest_interval_s`, `median_interval_s`,
`sprint_count`, `climb_time_share`, `work_rest_ratio` and `intensity_factor`. **No profile ships a
bound for them** and no shipped profile changed; the profile `schema` stays 1 (enum additions only,
`schema.json` regenerated), as in §7. Rules on them are `info` or `warn` like any other, never
blockers.

They are measured as `mowl corpus-analyze` measures them (mowl-workouts `docs/corpus-stats.md`), so
a bound made from that corpus is compared with the same quantity:

| Metric | Corpus key | Draft measure |
|---|---|---|
| `longest_interval_s`, `median_interval_s` | `interval_sec_max`, `interval_sec_median` | longest and median `sec` over every interval, warm-up and cool-down included |
| `sprint_count` | `acc_count` | intervals with `acc`; **always 0 for a designed draft**, see below |
| `climb_time_share` | `climb_pct` | share of the timeline seated, zone 3 or above, top cadence 1 to 80 rpm |
| `work_rest_ratio` | `work_rest_ratio` | zone 4+ seconds over zone 1 to 2 seconds of the segments that are not warm-up or cool-down |
| `intensity_factor` | `intensity_factor` | `sqrt(TSS / (hours x 100))` |

Differences, all from measuring a draft instead of a stored program: shares are fractions, not
percent; the corpus reads the TSS MOWL stored while a draft uses the library estimate
`workout.tss` (within 1 % of the server's, except for a draft with a zone 7 interval, whose TSS
weight is a guess); an interval with no cadence number is never a climb (`design.check` fits or
drops `"beat"` before the rules run, so this is rare); and `work_rest_ratio` is undefined when the
body has no zone 1 to 2 time, where the corpus leaves the class out too, so the engine measures
`None` and the rule does not fire.

**`sprint_count` gap.** The metric counts intervals carrying `acc`, but the draft schemas
(`agent/schemas.py`, strict, no `acc`) cannot carry it and `to_spec` never writes it, so it is 0 for
every designed or soundless class. It is meaningful only for a workout read back from MOWL. A low
bound (`sprints_few`) would fire on every designed class with a note the model cannot satisfy, and
`sprints_many` could never fire. So **no profile may ship a `sprints_few` or `sprints_many` bound**
until drafts can carry sprints (a draft schema change, a later issue); a guard test fails if one
does, and `test_a_real_draft_has_no_sprints_to_count` pins the limitation.

`segments` and `intervals` stay in the `Metric` enum with **no rule id**, on purpose: the number of
intervals follows the length and the beat-fitted music, and the number of segments follows how the
instructor names the parts, so a note on them would ask the designer to fight the brief. A later
issue can wire them with data that says otherwise.

### 7b. A provisional load note for 55 minute cycling classes (#388, 2026-11-06)

The owner will supply no more corpus data (2026-11-05) and agreed to use the values already
reported: for cycling classes sold as 45 and 55 minutes (n 59 and 113, all categories, no class-type
split), the **median and p25 to p75 of TSS** and the **median alone** of every other metric. §2 allows
bounds only from p5/p95 (n >= 25) or an observed range; neither exists. This section adds one
narrow, labelled exception:

- **Basis `tukey_iqr`** (schema enum addition; `schema` stays 1): the Tukey fences of the reported
  quartiles, `p25 - 1.5 x IQR` and `p75 + 1.5 x IQR`. It is **provisional**: it forces `severity:
  info` and `evidence: moderate`, and is to be replaced by `p5`/`p95` bounds (a new profile version)
  if corpus data ever supplies them. The loader refuses it with `warn` or on a strong profile.
- **Only where a p25 to p75 is reported.** That is TSS alone. The intensity factor, interval
  lengths, work to rest, climb share and every other metric were reported as a median only, and a
  median or a quartile is never a bound, so none of them gets a rule. No new rule id is needed:
  the two existing `tss_rate_low` and `tss_rate_high` (metric `tss_per_minute`, sentences
  `load_enough`, `load_bounded`) carry the fences, so the dismissal cap (28 of 32 ids) is untouched.
  `sprints_*` stay unshipped (§7a).
- **From TSS to a rate.** The metric is TSS per minute of the timeline, and the corpus cell "55" holds
  timelines of more than 50 and up to 55 minutes (the timeline rounded up to 5 minutes). So the low
  bound divides the lower fence by 55 and the high bound divides the upper fence by 50 (rounded
  outward to 3 decimals): the widest rate the cell allows, so the note errs on the quiet side.
  TSS 68.2 (66 to 72) gives fences 57 and 81 and the bounds 1.036 and 1.62.
- **Keys.** The source has no class-type split and the 45 and 55 cells mix plain and interval
  classes. The keys stay `(sport, class_type, label)`; there is no generic class type, and
  `select_profile` and the derived type (§2) are unchanged. The 55 cell is therefore shipped
  twice with identical rules, as `cycling.plain.55` and `cycling.interval.55`, so both derived
  types reach it. The 45 minute cell is **not** shipped: `cycling.plain.45` and
  `cycling.interval.45` already exist with percentile bounds, including the same two TSS rules,
  which a provisional note must not replace; `hiit`, `journey` and `ftp_test` stay without a profile
  rather than receive the all-classes numbers.
- **Selection: a strong profile always wins.** A provisional profile (one with a `tukey_iqr` rule)
  is chosen only when no other profile is within the 3 minute window; otherwise the nearest
  non-provisional profile wins, and among equals the nearest label. So 57 minutes selects the
  strong 60 (three away) and not the 55 (two away), and 52 to 56 minutes, which had no profile,
  select the 55 with its two info notes. The reason: a provisional note must never replace
  the percentile rules a class already had, and 57 minutes had them before this section.
- **Numbers by hand.** The `tukey_iqr` numbers are derived from the reported quartiles by hand, not by
  the private generator (§6); they are the one exception to "never typed by hand" above.
- **TSS source.** The corpus TSS is MOWL's stored TSS field over the class's total minutes; the
  engine's `tss_per_minute` is `workout.tss(spec)` over the timeline minutes. The two can differ a
  little, which is another reason the note is info-only and the bounds are widened outward.
- **Evals.** An `info`-only profile can never fail a `follows` case, so, as for running, each 55
  minute profile has a `deviates` case (`cp_easy_spin_55`, `cp_hard_push_55`): the brief asks for
  an easy or a hard class, the note fires and is explained by the brief.
- Suite version 14.

### 8. The issues

Review class: **Opus** = the model's input and output boundary, provenance, migrations with RLS;
**Sonnet** = pure code, data and UI over reviewed seams. All depend on this ADR being accepted.
A tracker issue (#388) stays open. No new migration number is claimed here; the next free one at
merge is used.

| # | Title | Scope | Done when | Review | Depends on |
|---|---|---|---|---|---|
| P1 | `feat(design): rule profile schema, loader and hygiene tests` | `profiles/schema.json`, `index.json`, `classbeat.design.rules.profile` (load, validate, digest, `select_profile` for an explicit class type and length), the closed enums, `RATIONALES`, the first profiles from the private aggregates for the **strong** cycling classes only (30, 45, 60 x plain, interval), the version-without-digest test, the no-free-text test | a profile with an unknown field, a free string, or a `p5` basis with n < 25 is refused; `select_profile` matches within 3 minutes and returns `None` otherwise; digests stable | Sonnet | this ADR |
| P1b | `feat(web): dismissed_rules column and class dismissal route` | migration for `classes.dismissed_rules jsonb NOT NULL DEFAULT '[]'` (cap 32 ids; RLS unchanged; export and deletion verified, test added; app-role column grant checked), new owner-scoped route to dismiss and undo a rule id on a class, 404 matrix | cross-user 404s; cap and unknown-id validation (ids from the profile enum); export and deletion cover the column | Opus | none |
| P2 | `feat(design): soft-rule engine` | pure `evaluate(spec, brief, profile) -> list[RuleHit]` over the fitted spec for the v1 metric catalogue, severity lowering for `brief_overrides`, ordering and cap 5, dismissal filter | pair fixtures for every shipped rule (inside / outside), override and cap tests; no import of the agent or the database | Sonnet | P1 |
| P3 | `feat(agent): class_type in the brief and profile selection` | optional `Brief.class_type`, the derivation when absent, job input and `classes.brief` validation, `select_profile(brief)` wiring; the new-class form is **not** changed | an old brief still validates and designs as before; derivation table tested; brief size guard unchanged | Opus | P1 |
| P4 | `feat(agent): rules section and tool result for design and revise` | `model_input` section (cap, wording, absent when `None`), `rules` in `preview_draft` / `submit_draft`, provenance `rules`, dismissed rules (P1b's column) excluded on revise, caps and planted-value tests (no brief or music text in the section) | a replay run shows the section and hits; no profile -> byte-identical input to today; `submit_draft` accepts a draft with every rule violated; **no tool name, description or schema and no system prompt text is edited, so every stage's `prompt_version` is unchanged (test)** | Opus | P2, P3, P1b |
| P5 | `feat(web): pattern notes on the revision API` | revision `GET` returns `ruleHits` computed from the stored brief and the **current** profile, the "notes use the current rules" flag when the provenance digest differs, dismissed rules filtered, owner-only and 404 tests | cross-user 404s; a dismissed rule stays hidden on the next revision; a changed profile changes old revisions' notes and sets the flag; nothing stored per revision | Sonnet | P2, P1b, P4 |
| P6 | `feat(web): "Pattern notes" block on the class page` | `RevisionView` block, copy per §4, dismiss and undo, muted `explained_by_brief`, no alert role, nothing disables an action, a11y and 44 px targets | component and page tests, axe clean; a revision with no hits renders as today | Sonnet | P5, P1b |
| P7 | `test(evals): class_patterns category` | the `class_patterns` manifest category and cases of §5 (fixtures, scripted runs, brief-wins predicates), `rules_version` in the results header and `compare`, suite digest and `suite_version` bump | the gate's verdict covers the new category; replay green; live baseline is an owner-run step, not part of done | Sonnet | P4 |
| P8 | `docs(runbook): refresh rule profiles from new aggregates` | owner runbook: how aggregates become a profile (the generator script stays private), the PR checklist of §6, when to bump versions, how to add a sport | a reviewer can follow it without the private notes; contains no source name | Sonnet | P1 |
| P9 | `feat(design): running profiles` | A running analysis under §6, then profiles (done: #486, two `moderate` cells); an incline metric stays open until R1 gives drafts a gradient | done 2026-11-03 for the two cells; more cells when more classes exist | Opus (schema bump) | owner's data, P1 |

Shortening the prompt's hand-written "typical class" bullets is a separate prompt change after P4
and P7 have a baseline; it is not in this list.

## Alternatives

- **Hard rules from the statistics.** Rejected: the sample is one library, partly re-releases of
  the same class, and the brief would lose to it. A refusal on a pattern teaches the instructor to
  work around the tool.
- **Rules only in the prompt.** Rejected: unchecked, untestable, and the model cannot tell which
  of its drafts deviated. The engine makes the same expectations measurable and shows the user.
- **Rules in the system prompt.** Rejected: every profile edit would change `prompt_version`
  (ADR 0006 §6) and with it the model gate's match and the system block's cache.
- **Profiles in the database, edited in the admin.** Rejected for now: needs migrations, an admin
  screen, audit and review of data changes; a reviewed file is simpler and gives a digest for
  free. Revisit if profiles change often or per tenant.
- **Store the hits on every revision.** Rejected: they are a pure function of data we already
  keep, and a stored copy goes stale when the profile changes. Notes are computed on read from
  the current profile (§4); provenance records which profile the model saw and flags, with one
  muted line, when it differs from today's.
- **Thresholds at medians or quartiles.** Rejected: a median-based warning would fire on half of
  all professional classes. The 5th and 95th percentiles flag what is unusual.
- **Show hits only to the model.** Rejected: the instructor decides; seeing "usual" next to their
  choice is the point, and dismissal teaches the revise stage.

## Consequences

- The designer gets measured guidance without losing the brief; a bad profile costs a note, not a
  refused class.
- Review cost: P3 and P4 touch the model boundary (Opus); the rest is Sonnet.
- Token cost is small and bounded (one section of at most 8 lines, at most 5 hits per tool result),
  and zero when no profile applies.
- Small samples are honest: weak evidence ships nothing, moderate evidence only informs.
- The re-release problem means the strong profiles' effective n is lower than the counts; the
  bounds are reported as p5 / p95 of what exists, and the first live baseline (P7) is the real
  test of whether they are too tight. A rule that fires on most good drafts is dropped by a profile
  version, not tuned silently.
- A schema change (new metric) needs a code release; a data change is a reviewed file.
- The statistics describe one studio library and one time; the UI says "usual", never "correct".

## Threat model

No new row. The profiles are trusted repository data, read-only and not influenced by users; the
model gets no new tool and no write path, and the section is built from enumerated fields, so the
existing prompt-injection rows (T8) already cover it. No third-party content is involved. The next free row ids on `main` are T112 and up (ADR 0016 holds T108 to T111); this ADR
claims none.

## Owner questions (defaults the issues build to)

1. Class type in the new-class form, now or later? Default: **later**; the derived type is used.
2. Should dismissal also reach the model? Default: **yes**, on that class's revise stages.
3. Ship `info`-only HIIT (moderate evidence) in P1? Default: **yes**, after the strong profiles
   are reviewed; journey, 75 minutes and FTP test ship nothing.
