# M3c: evaluating and enabling OpenAI and DeepSeek models

[ADR 0012](../adr/0012-eval-suite-and-model-providers.md) sections 4 to 6. This runbook is the owner's
checklist for running the regression suite live against a model, recording a pass, and enabling the model
for a stage group. **Technical completion** of M3c (the roadmap's "done when") is proved on replayed
provider transcripts and needs none of this (`backend/tests/test_m3c_done_when.py` and
`test_m3c_done_when_jobs.py`). **Enabling real models** is separate and is the owner's.

## Rules

- **Agents run none of these steps without the owner's go-ahead for that run.** A go-ahead covers one
  command with one `--max-usd`; it is not one for the next run, the next model or `--record`. Every run is
  logged under "Live activation log" in [docs/status.md](../status.md): date, who authorized it, the
  command (without any key), the cost printed and the verdict.
- The suite sends **synthetic data only** (invented music and briefs); no user data reaches a provider
  through it. That is why an `owner_pending` model can be evaluated.
- Never paste a key, a signed URL or a provider's response body into a log, an issue, a PR or this file.
  Keys live only in files; the commands below print costs, counts and reason codes.
- Until a pass is recorded and a route enabled, **the bootstrap model serves users**: provider
  `anthropic` with `CLASSBEAT_AGENT_MODEL` and `CLASSBEAT_AGENT_EFFORT` (default `claude-opus-5-5`,
  `medium`). It is served without a recorded pass (question 5: it needs a full-suite run before M5's tiers
  use it, but production runs it today). The routes below are the only way another provider is reached.
- **OpenAI and DeepSeek routes stay impossible until the owner flips `data_policy`.** Their catalog
  entries ship `owner_pending` (`classbeat.agent.catalog`); `models enable` refuses them with
  `data_policy` however good the result is. A "yes" to ADR 0012 owner question 1 (OpenAI) or 2 (DeepSeek)
  is a reviewed catalog change to `allowed` plus a dated line in [ADR 0010](../adr/0010-live-activation-permissions.md)
  extending the risk acceptance to that provider (with `store=False` and the API's no-training default noted).
  DeepSeek additionally needs `CLASSBEAT_DEEPSEEK_ENABLED=true` on web and worker.

## Where the keys go (the owner creates them; agents never see them)

| Provider | Create it | Place it | Setting |
|---|---|---|---|
| OpenAI | an API key in the owner's OpenAI project, with a spend limit set in OpenAI's dashboard | `secrets/openai_api_key` in the stack's checkout (git-ignored; one line, no quotes) | none |
| DeepSeek | an API key in the owner's DeepSeek account, with a balance cap | `secrets/deepseek_api_key` | `DEEPSEEK_ENABLED=true` in `.env` only to serve jobs (not needed for an eval); compose passes it as `CLASSBEAT_DEEPSEEK_ENABLED` to web and worker alike |
| Anthropic | already in use | `secrets/anthropic_api_key` | none |

`scripts/worktree.sh init` creates each file empty; an empty file counts as no key. Compose mounts the
three files on the **worker only** (the web app refuses to start if it can read one). Restart the worker
(`docker compose up -d worker`) after placing a key. Do not set `OPENAI_*`, `DEEPSEEK_*` or
`SSLKEYLOGFILE` variables: the command refuses while any is set. The eval is run in the worker container,
which has the key and the database connection.

## Cost cap

Every run prints its plan and **cost ceiling** (the worst case of all runs: each run's job ceiling plus one
turn) and stops unless `--max-usd N` is also given. `N` is the most you accept to spend; each run gets what
is left of it as its ceiling, a failed run still counts what it spent, and a run whose spend is unknown is
charged everything that was left. Costs err high (prices are in `classbeat.agent.prices`, versioned by
`PRICES_VERSION`; verify them against the provider's price page before the first live run). Also set the
spend limit in the provider's own dashboard: it is the cap that does not depend on classbeat. A full
suite at `--repeat 3` is about 78 cases times 3 runs; the printed ceiling is the number to approve.

## Steps

All commands run from the stack's checkout. `P`, `M`, `E` are the provider, a catalog model and an effort
it lists (`openai` takes `low` to `max`; `deepseek` takes `low`, `high` and `max`).

1. **Go-ahead.** The owner tells the agent: "run step 3 for P M E, at most $N" (and, for step 5, "and record").
2. **Key.** Place the key as above and restart the worker.
3. **Adapter check (the first live run per provider).** The adapters and their fixtures were written to the
   providers' documented response shapes, never against a live answer. So the first run is a check of the
   adapter, small and not recorded:

   ```bash
   docker compose exec worker python -m classbeat.evals suite --live --provider P --model M --effort E \
     --repeat 1 --max-usd 2 --out /tmp/adapter-check.json
   ```

   No `--record`. Read the plan first (it prints before anything is sent and stops without `--max-usd`).
   Expect failed runs and possibly `internal` or `invalid_output` outcomes: that is what the run is for.
4. **Update the fixtures in a reviewed PR.** From that run's response shapes (field names, usage fields,
   tool-call and error shapes, headers; never keys, request ids or content that is not invented) update
   `backend/tests/provider_fixtures/<provider>/` and the adapter if the shape differs. Open a PR; a fresh
   Reviewer checks it like any adapter change. No recorded run happens before it merges, because a result
   recorded through an adapter that misreads the provider's answers would gate on wrong costs and outcomes.
5. **Recorded run.** With the owner's go-ahead for this run and a `--max-usd` at or above the printed ceiling:

   ```bash
   docker compose exec worker python -m classbeat.evals suite --live --provider P --model M --effort E \
     --repeat 3 --max-usd N --out /tmp/suite-P-M.json \
     --record --authorization "owner go-ahead YYYY-MM-DD"
   ```

   `--authorization` (1 to 200 characters) is stored with the result: put the date and who authorized it,
   not a secret. The command prints the verdict per stage group (`passed`, `failed` or `incomplete`, with the
   rules that failed) and, with `--record`, `recorded result <id>` for each group. Exit 0 means every group
   passed; 1 a failure (still recorded: a failing result is what makes `enable` refuse); 2 a refusal before
   anything ran. An `incomplete` verdict (an outage of the provider in more than 10 % of runs) is not a pass:
   run again. A recorded result is append-only; nothing edits or deletes it.
6. **Enable.** Only if the model's `data_policy` is `allowed` (see Rules) and, for DeepSeek, the setting is on:

   ```bash
   docker compose exec worker python -m classbeat models results --provider P
   docker compose exec worker python -m classbeat models enable default design P M E --yes
   docker compose exec worker python -m classbeat models enable default songs P M E --yes
   docker compose exec worker python -m classbeat models list
   ```

   `enable` first prints the result it will rely on and changes nothing without `--yes`. It is refused with a
   reason code when the model or effort is not in the catalog (`not_in_catalog`, `effort_not_in_catalog`),
   DeepSeek is off (`deepseek_disabled`), the entry is `owner_pending` (`data_policy`), there is no result
   (`no_result`), the newest result is on another suite version (`suite_version`), failed (`result_failed`),
   was recorded on a changed suite (`manifest_digest`) or for other prompts or tools (`prompt_versions`).
   Each group has its own result and its own route; only `default` exists until M5 maps plans to keys.
   `models list` shows each route as `usable`, `stale: <reason>` or `unusable: <reason>` (for example
   `no_key` when the worker has no key file). A route whose model has a newer failing result is refused at
   the start of every job (`model_config`), with no fallback to Anthropic.
7. **Undo.** `docker compose exec worker python -m classbeat models disable default design` (and `songs`):
   that group returns to the bootstrap model for the next job.

## After a deploy

A deploy that changes a prompt, a tool schema or the suite makes every route stale (`prompt_versions`,
`suite_version`, `manifest_digest` in `models list`). A stale route keeps serving, flagged, until the owner
re-runs step 5 and `enable`s again (owner question 3); a new route for a stale result is refused. A newer
failing result for the same model stops it at once.

## The ADR 0010 lines (owner questions 1 and 2)

Record, as dated lines at the end of [ADR 0010](../adr/0010-live-activation-permissions.md), the owner's answer:
for OpenAI, whether the Spotify §III 14 risk acceptance (given for Claude, for the beta circle) extends to
OpenAI, with `store=False`; for DeepSeek, the privacy decision (where data is processed and kept), the
terms, and that a passing eval exists. A "yes" is then a reviewed PR that sets the catalog entry to
`allowed` and adds the provider to `USER_DATA_PROVIDERS`; until it merges no route to that provider can be
set, whatever has been evaluated.

## Live activation log

Record each run under "Live activation log" in [docs/status.md](../status.md): the date, the owner's
go-ahead, provider, model, effort, `--repeat`, `--max-usd`, the printed cost and the verdict per group.
