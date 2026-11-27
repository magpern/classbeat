# Soundless classes: the owner's live eval

[ADR 0007](../adr/0007-classes-revisions-and-saving.md) (amendment of 2026-11-03, "soundless classes", items 2 and 6,
the amendment of 2026-11-08, which retired the admin flag, #824, and the amendment of 2026-11-27, which
retired the environment's switch, #1543),
[ADR 0012](../adr/0012-eval-suite-and-model-providers.md) (section 5 and its amendment of 2026-11-03).
Suite version 12 holds soundless cases: the `soundless` category (`soundless_45`, `soundless_30_sprints`,
`soundless_revise_sprints`) and `soundless_adversarial` in `adversarial`, all in the `design` stage group. CI
replays them against `FakeProvider` (`backend/tests/test_evals_soundless.py`, and `test_evals_smoke.py` for the
four smoke files `smoke/soundless_*.json`) and never calls a model. **No live eval has been run or recorded
for soundless yet.**

## What coverage means (no gate since #1543)

"No music" is always offered and never refused on a switch, a flag or coverage (owner, 2026-11-26). A
`design` route **covers soundless** when its result was
recorded by `suite --live --record` on suite version **12 or later** (`models.routes.SOUNDLESS_SUITE_VERSION`),
on the current manifest digest, with the current code's prompt versions of all eight stages (the six `design`
group ones, `soundless_design` and `soundless_revise` among them, and the two song ones), and no newer
result for that model failed. A result recorded on version 10 or earlier never covers soundless, however
recent its prompt versions. The bootstrap model (no route) never covers soundless. Without such a route a
real no-music design or revise is **still served** with the soundless prompts, as a stale route serves other
stages (ADR 0012 section 5): the worker logs `model route stale` with reason `soundless_not_evaluated` once
per process and route key. A composer's **sketch** keeps its rule: its admit needs the job's own plan to be
covered (`soundless_not_evaluated`) and the worker ends it `model_config` on an uncovered model. So this run
is what turns the warning off and the sketch on; it does not switch "No music" on.

The suite sends the soundless stages synthetic briefs only and **no music at all**: the model sees the brief,
never a playlist, a track or a BPM. A soundless case is judged on the asked length, the zone share and the
TSS bounds of its intensity, what the brief asked for (sprints, climbs, an FTP test), no `"beat"` cadence and
no text that names music (`no_beat`, `no_music_reference`); it has no music-sync check.

## Rules

- **Agents and CI never run these commands.** Each live run needs the owner's go-ahead for that one command
  and its `--max-usd`; `--record` needs its own. Log every run under "Live activation log" in
  [docs/status.md](../status.md): date, who authorized it, the command (no key), the cost printed, the verdict.
- The suite sends synthetic data only; no user data reaches the provider.
- Never paste a key or a provider's response body anywhere. The commands print costs, counts and reasons.

## Steps

Run from the stack's checkout, in the worker container (it has the key file and the database).

1. **Optional look at the soundless cases only** (not recorded; does not count for the gate). The smoke
   command runs the four soundless smoke cases on the chosen Anthropic model:

   ```bash
   docker compose exec worker python -m classbeat.evals smoke --live --model claude-opus-5-5 \
     --case soundless_45 --case soundless_30_sprints --case soundless_revise_sprints \
     --case soundless_adversarial --out /tmp/soundless-smoke.json
   ```

   Without `--max-usd` it prints the plan and its cost ceiling and stops (about 4 x $1.98 for Opus at effort
   `medium`; the real spend is usually a small part of it). Run it again with `--max-usd` set to the printed
   figure once the owner agrees. Failed checks print with their detail.
2. **Recorded run** (what the gate relies on). The soundless cases are part of the `design` group, so the
   gate needs a full suite run; there is no soundless-only verdict:

   ```bash
   docker compose exec worker python -m classbeat.evals suite --live --provider anthropic \
     --model claude-opus-5-5 --effort medium --repeat 3 --out /tmp/suite-v12.json
   ```

   It prints the plan and the cost ceiling (101 cases, 83 in the `design` group and 18 in `songs`, times 3)
   and stops. With the owner's go-ahead for that ceiling, add `--max-usd N --record --authorization "<the
   owner's words and date>"`. The verdict prints per group; a `design` pass includes the soundless cases, and
   the results header names the prompt version of every stage, `soundless_design` and `soundless_revise`
   included. One recorded run covers running and soundless together (the same group, the same result).
3. **Enable the route for every plan key** that will serve soundless (the admin's Model routes page or
   `docker compose exec worker python -m classbeat models enable KEY design anthropic claude-opus-5-5 medium
   --yes`), as in [m3c-model-enable.md](m3c-model-enable.md). `models list` must show no stale reason.
4. **Nothing to switch on** (#1543). The worker's `model route stale` warning with reason
   `soundless_not_evaluated` stops for a key once its route covers soundless. Record the run in
   `docs/status.md`. The live save and update check of a no-music class (`PlaylistID: -1`) is a separate
   pre-deploy item there ("Waiting for the owner").

## After a change

A new soundless prompt changes `soundless_design` or `soundless_revise`; a change to
`agent/soundless_input.py`, a soundless case or a soundless check is a new suite version. Either makes the
route stale, so running jobs and sketches stop (`model_config`) until a new recorded pass and `models
enable`; cycling and real no-music classes keep working on the stale route, flagged.
