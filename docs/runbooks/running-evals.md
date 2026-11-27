# Running classes: the owner's live eval

[ADR 0012](../adr/0012-eval-suite-and-model-providers.md) (amendment of 2026-11-03) and
[ADR 0018](../adr/0018-mowl-running-classes.md) (Amendment 3). Suite version 10 added running cases (version 11 added the soundless ones, [soundless-evals.md](soundless-evals.md)): the
`running` category (six cases), `run_adversarial` in `adversarial` and two running `class_patterns` cases,
all in the `design` stage group. CI replays them against `FakeProvider` (`backend/tests/test_evals_running.py`)
and never calls a model. **No live eval has been run or recorded yet.**

## What a running class needs now

Since #1544 ([ADR 0018](../adr/0018-mowl-running-classes.md) Amendment 6; owner, 2026-11-26: running is
always available) there is **no switch**: `RUNNING_ENABLED` and the admin's `running_enabled` are retired
(migration 0082 deleted a stored row and the Settings page no longer lists the key). Every instructor can
start a running class; it still designs on one of the user's MOWL playlists (without one the reading says
"Running needs a playlist from your MOWL music.").

The recorded eval still matters, for quality: a running design or revise uses the `design` route, and a
route is **covered for running** when it is not stale for any reason (its result was recorded by
`suite --live --record` on the running suite version and digest, with the running code's prompt versions of
`design`, `revise`, `running_design` and `running_revise`, and no newer result for that model failed). The
bootstrap model (no route) never covers running. On a route that does not cover running the job is **served
with the stale-route warning** (`model route stale`, reason `running_not_evaluated`, once per process and
route key; ADR 0012 section 5), not refused. A route with a newer failure is still unusable
(`model_config`). The running schema and the library's `validate_running` check every draft either way.

## Rules

- **Agents and CI never run these commands.** Each live run needs the owner's go-ahead for that one command
  and its `--max-usd`; `--record` needs its own. Log every run under "Live activation log" in
  [docs/status.md](../status.md): date, who authorized it, the command (no key), the cost printed, the verdict.
- The suite sends synthetic data only (invented briefs and music); no user data reaches the provider.
- Never paste a key or a provider's response body anywhere. The commands print costs, counts and reasons.

## Steps

Run from the stack's checkout, in the worker container (it has the key file and the database).

1. **Optional look at the running cases only** (not recorded; does not count for the gate). The smoke
   command runs the seven running smoke cases on the chosen Anthropic model:

   ```bash
   docker compose exec worker python -m classbeat.evals smoke --live --model claude-opus-5-5 \
     --case run_interval_30 --case run_interval_45 --case run_steady_40 --case run_incline_35 \
     --case run_bike_words_30 --case run_revise_easier --case run_adversarial --out /tmp/running-smoke.json
   ```

   Without `--max-usd` it prints the plan and its cost ceiling and stops. The ceiling is each case's job
   ceiling ($1.50, `CLASSBEAT_AGENT_JOB_COST_MICROS`) plus one turn's over-run, so for Opus at effort
   `medium` about 7 x $1.98 = $13.86; the real spend is usually a small part of it. Run it again with
   `--max-usd 14` (or the printed figure) once the owner agrees. Failed checks print with their detail.
2. **Recorded run** (what the gate relies on). The running cases are part of the `design` group, so the gate
   needs a full suite run; there is no running-only verdict:

   ```bash
   docker compose exec worker python -m classbeat.evals suite --live --provider anthropic \
     --model claude-opus-5-5 --effort medium --repeat 3 --out /tmp/suite-v11.json
   ```

   It prints the plan and the cost ceiling (101 cases, 83 in the `design` group and 18 in `songs`, times 3) and
   stops. With the owner's go-ahead for that ceiling, add `--max-usd N --record --authorization "<the
   owner's words and date>"`. The verdict prints per group; a `design` pass now includes the running cases.
3. **Enable the route for every plan key** that will serve running (the admin's Model routes page or
   `docker compose exec worker python -m classbeat models enable KEY design anthropic claude-opus-5-5 medium
   --yes`), as in [m3c-model-enable.md](m3c-model-enable.md). `models list` must show no stale reason.
4. **Nothing to switch on** (#1544). The worker's `model route stale` warning with reason
   `running_not_evaluated` stops for a key once its route covers running. Record the run in
   `docs/status.md`. The owner's live check of a running save and update is a separate pre-deploy item there
   ("Waiting for the owner").

## After a change

A new running prompt changes `running_design` or `running_revise`; a change to `design/sport_rules.py`,
`agent/running_input.py`, a running case or a running check is a new suite version. Either makes the route
stale: running jobs, cycling and real no-music classes keep working on it with the warning, until a new
recorded pass and `models enable`; a sketch stops (`model_config`).
