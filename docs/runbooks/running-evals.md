# Running classes: the owner's live eval and switching running on

[ADR 0012](../adr/0012-eval-suite-and-model-providers.md) (amendment of 2026-11-03) and
[ADR 0018](../adr/0018-mowl-running-classes.md) (Amendment 3). Suite version 10 added running cases (version 11 added the soundless ones, [soundless-evals.md](soundless-evals.md)): the
`running` category (six cases), `run_adversarial` in `adversarial` and two running `class_patterns` cases,
all in the `design` stage group. CI replays them against `FakeProvider` (`backend/tests/test_evals_running.py`)
and never calls a model. **No live eval has been run or recorded yet.**

## What the gate needs before real users get running

A running design or revise runs only on a `design` route that is **not stale for any reason**: its result
was recorded by `suite --live --record` on the running suite (version and manifest digest) with the running
code's prompt versions of `design`, `revise`, `running_design` and `running_revise`, and no newer result for
that model failed. The bootstrap model (no route) never serves running. Without such a route a running job
ends `model_config` before any MOWL or model call, and the admin refuses to store `running_enabled` = `on`
(422 `running_not_evaluated`) until **every plan's route key** has one. `CLASSBEAT_RUNNING_ENABLED` stays the
kill switch.

Since #509 ([ADR 0018](../adr/0018-mowl-running-classes.md) Amendment 5) running is on only when **all three**
hold, checked on every page load and every request: `RUNNING_ENABLED=true`, `running_enabled` stored exactly
`on`, and every plan's route key covered as above. What the admin sees:

- **Settings page, `running_enabled`:** *effective* `on` only with all three. With nothing stored (also after
  **Clear**, the DELETE) it shows `off`, even with `RUNNING_ENABLED=true`. Storing `on` answers
  `running_not_evaluated` while any plan lacks a covering route.
- **After a plan is added or a route is cleared or goes stale** (a newer failed result, a new suite version or
  prompt), *effective* turns `off` by itself, while *stored* still says `on`. The New class form stops offering
  Running at the next page load; nothing needs to be stored again once the route is back.
- **Users:** a running design or change is refused before anything is charged. A user whose own plan lacks a
  covering route sees "Running classes are not available on your plan yet"; when only another plan lacks
  one, running is simply off for everyone (`feature_off`). A job that was already queued when its route went
  away ends with "classbeat cannot design classes right now" (`model_config`), with its credit returned.

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
4. **Switch running on.** Both are needed: set `RUNNING_ENABLED=true` in `.env` (web and worker; restart both),
   **and** store `running_enabled` = `on` on the admin's Settings page, which refuses with
   `running_not_evaluated` while a plan's key lacks a covering route. Check that *effective* shows `on`. Record
   the decision in `docs/status.md`. To switch it off, store `off` or **Clear** it (both are off), or set
   `RUNNING_ENABLED=false`.

## After a change

A new running prompt changes `running_design` or `running_revise`; a change to `design/sport_rules.py`,
`agent/running_input.py`, a running case or a running check is a new suite version. Either makes the route
stale, so running jobs stop (`model_config`) until a new recorded pass and `models enable`; cycling keeps
working on the stale route, flagged.
