# Refreshing the rule profiles from new aggregates: a note for the owner

[ADR 0017](../adr/0017-class-pattern-soft-rules.md) sections 2, 6, 7 and 8 (P8). The soft rules the
designer is given, and the notes the class page shows, come from JSON profiles under
`backend/src/classbeat/design/profiles/`. This is the owner's checklist for changing them. Merging
needs no live model call; only the last two sections cost money, and each live run is the owner's
own go-ahead.

## What lives where

- In the repository: `profiles/schema.json`, `profiles/index.json`, one file per profile
  (`profiles/<sport>/<class_type>_<label>.json`; today six under `cycling/`: plain and interval at 30,
  45 and 60 minutes), the closed enums (`design/rules/enums.py`), the sentences (`RATIONALES` in
  `design/rules/rationales.py`) and the loader (`design/rules/profile.py`).
- **Not** in the repository: the collection and aggregation scripts, the raw snapshot, the programs
  read and the commands used (ADR 0017 section 6.3). They stay in the owner's private notes. The
  source is only ever called "a professional studio library".

## The contract with the private generator

**Input** (private): the aggregate rows of one `(sport, class_type, label_minutes)` cell. **Output**
(the only thing that crosses into the repo): one profile file of numbers and ids.

Per profile: `n` (classes in the cell), `evidence` (`strong`, `moderate` or `weak`) and `snapshot`
(`YYYY-MM-DD`). Per rule: one bound for one metric.

| Bound | Statistic | `basis` | Condition |
|---|---|---|---|
| `low` set, `high` null | 5th percentile | `p5` | `n >= 25` (`MIN_N_PERCENTILE`) |
| `high` set, `low` null | 95th percentile | `p95` | `n >= 25` |
| either | observed range widened 10 % | `range_10` | `n < 25`; forces `severity: info` |

Metrics are the `Metric` catalogue in `enums.py`, measured on a timeline the way
`design/rules/engine.py` measures a draft. `RULE_SPECS` fixes the metric and side of every rule id;
the loader refuses a mismatch. Evidence decides what ships (the loader enforces it):

- **strong** (`n >= 25`): `warn` and `info` rules.
- **moderate**: `info` rules only; a `warn` is refused.
- **weak**: ships **nothing**; do not add a file with an empty `rules` list.

Medians and quartiles are never bounds, and length (`total_minutes`) is never a rule. Numbers come
from the generator, never typed by hand; a bound that fires on most good drafts is fixed by a new
version, not tuned silently. `brief_overrides` come from `BriefOverride` and `rationale_id` must
exist in `RATIONALES`. A new rule id, metric, override or sentence is a **code** change (enums,
`RULE_SPECS`, `RATIONALES`, engine, then `schema.json`), reviewed as code.

## Bump `version`, or add a profile

- **Existing cell, any byte of the file changes** (numbers, rules, `n`, `evidence`, `snapshot`):
  raise that file's `version` by one and update its `index.json` entry.
- **A cell with no file yet** (for example `hiit` at 30 minutes, `info` only): add
  `<class_type>_<label>.json` at `"version": 1` and a new `index.json` entry. `id` must be
  `<sport>.<class_type>.<label_minutes>` and the file name must match.
- `rules_version` in `index.json` is the **maximum** profile `version`, not a change counter.
- A change to enums, `RATIONALES` or the engine leaves profile versions alone but still moves the
  suite digest (below).

## The pull request

1. Branch, commits and PR text name no employer, brand, class title or instructor.
2. Edit the profile (indent 2, trailing newline) and bump `version`.
3. Refresh the digest. From `backend/`, per changed file:
   `uv run python -c "from pathlib import Path; from classbeat.design.rules.profile import digest_of; print(digest_of(Path('src/classbeat/design/profiles/cycling/interval_45.json').read_bytes()))"`
   (line endings are normalised, so Windows and Linux agree). Put it, the `version` and the new
   `rules_version` into `index.json`.
4. If the schema changed, regenerate `schema.json` from `dumps_schema()` in `profile.py`.
5. Bump the suite (next section).
6. Run the verification below and the **owner-run private hygiene script** over the whole repo (ADR
   0017 section 6.4; a denylist cannot live in the public repo). Say it was run, not what it greps for.
7. Paste this checklist in the PR: [ ] aggregates only: no title, description, segment, interval list,
   instructor or playlist; [ ] no employer or brand name in the diff; [ ] no free text (every string
   is an enum value, `source` or an id); [ ] `version` bumped, `index.json` digests and
   `rules_version` updated; [ ] `suite_version` bumped and pinned; [ ] tests green; [ ] private
   hygiene script run; [ ] weak ships nothing, moderate ships `info` only.

## Suite digest and version

`design/profiles/index.json` is in `RULE_SOURCES` in `backend/src/classbeat/evals/suite.py`, so a
changed profile changes the suite digest: a new `rules_version` is a new suite. The suite refuses to
load and `tests/test_evals_suite.py` fails until:

1. `suite_version` in `backend/src/classbeat/evals/suite/manifest.json` is raised by one;
2. the manifest's `digest` is set to `current_digest()` from `classbeat.evals.suite`;
3. the new `suite_version: digest` pair is appended to `RELEASED` in `tests/test_evals_suite.py`
   (append only; a released digest never changes).

If a new bound puts a `class_patterns` fixture out of range, fix the fixture in the same PR.

## Verification a reviewer can follow

From `backend/` (setup in the repo `README.md`):

- `uv run pytest tests/test_design_rule_profiles.py tests/test_design_rule_engine.py tests/test_evals_class_patterns.py tests/test_evals_suite.py`
  covers: index digests, versions and `rules_version`; unknown fields and free strings refused;
  `p5`/`p95` need `n >= 25`; evidence limits; profile files hold only ids and numbers; schema in
  sync; the suite pin; every `class_patterns` case.
- `git diff origin/main -- src/classbeat/design/profiles` shows only numbers and enum ids, each
  changed file with a higher `version` and the matching `index.json` digest.
- The diff holds no source name beyond "professional studio library".
- Results files record `rules_version` in their header, and `python -m classbeat.evals compare`
  refuses runs made with different values, as intended.

## After the merge (the owner)

New profiles apply to the next design or revise job and to the next read of an old revision (notes
are recomputed from the current profile, with the muted line "Notes use the current rules" when the
provenance digest differs). The new `suite_version` and digest, however, make every recorded eval
result **stale** (reasons `suite_version` and `manifest_digest` in `classbeat.models.routes`): an
existing route keeps working, flagged, but a new `models enable` is refused until a passing result
exists on the running suite. So:

1. Deploy as usual ([vps-deploy.md](vps-deploy.md)).
2. Re-run the live suite for each model to enable and record the pass, as in
   [m3c-model-enable.md](m3c-model-enable.md). Each run needs its own go-ahead and `--max-usd`.
3. The first live run after a change re-baselines the "follows the pattern" cases (ADR 0017
   section 5). A rule that fires on most good drafts calls for a new profile version, not a looser
   check.

## Adding a sport (for example running)

`Sport` in `enums.py` is `Literal["cycling"]` and `select_profile` returns `None` for any other
sport, so running classes get no rules, no prompt section and no note. **P9 (running profiles) is
blocked until aggregate data exists**; no running threshold may be invented, borrowed from cycling
or estimated. Once the owner has a running analysis made under the same rules (aggregates only, the
same `n >= 25` and evidence grading):

1. Code PR (Opus review, schema bump): add the sport to `Sport`, let `select_profile` and the brief
   choose it, add any new metric (for example incline) with its `RULE_SPECS` entry, rationale ids and
   engine support, regenerate `schema.json`, and update the cycling-only assertions in
   `tests/test_design_rule_profiles.py`.
2. Data PR: `profiles/running/<class_type>_<label>.json`, `index.json` entries, digests, versions,
   suite bump and the checklist above. The directory stays absent until then.
