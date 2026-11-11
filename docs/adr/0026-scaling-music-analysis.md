# ADR 0026: Scaling music analysis: cap, measure, cache, then more workers, then another host (#733)

Status: Proposed. Docs only: this ADR changes no code and deploys nothing. The first review of a
new design that touches secrets and isolation is Opus (AGENTS.md review gate, item 2).

Related: [ADR 0005](0005-jobs-and-leases.md) (jobs, leases, fencing, the queue functions),
[ADR 0006](0006-agent-runs.md) §9 (the worker's secrets and outbound allowlist),
[ADR 0007](0007-classes-revisions-and-saving.md) §11 (retention and the conditions for any shared
analysis), [ADR 0011](0011-tiers-quotas-and-admin.md) (tiers and quotas),
[ADR 0016](0016-swedebeat-music.md) §3 and §7 (the Swedebeat mix analysis and its limits),
[ADR 0002](0002-tenant-isolation.md) (isolation).

## Context

Owner request (2026-11-07, #733): write down how music analysis would scale "for the future".
Nothing is overloaded today. This ADR fixes the order in which to react, what already exists so no
one builds it twice, what is missing, and the point at which each step is allowed to start.

### What "analysis" is here (checked against the code)

- **Only the Swedebeat mix analysis is heavy.** `classbeat.design.swedebeat_music` fetches up to
  250 MB of audio through a temporary link from the user's own MOWL account and decodes it in a
  limited child process (`design.mixanalysis`: 1.5 GiB resident memory, 180 s wall clock, decoded
  length at most the listed length plus five minutes and never over 120 minutes). It uses numpy,
  numba and librosa: this is the CPU, memory and disk load.
- **Spotify playlists are not analysed by classbeat.** The worker reads tracks and sections through
  MOWL (`library.spotify_analysis`, ADR 0006 §3). The cost is MOWL API calls, bounded by MOWL, not
  our CPU. The result lives in memory for the job; nothing is stored (ADR 0007 §11). So the
  "analysis" a scaling plan has to reckon with is the Swedebeat mix.
- **Analysis is a stage inside a design job, not a job of its own.** `design_draft`,
  `revise_draft` and `swedebeat_verify_mix` call the music step (`design.swedebeat_music`). The job
  also calls the model, reads Spotify and saves to MOWL in other stages.

### What already exists

| Piece | State | Where |
|---|---|---|
| Worker concurrency | exists: `CLASSBEAT_WORKER_CONCURRENCY`, default 2 (1 to 16) running jobs per worker process; the worker's pool is `2n + 2` connections | `config.py`, `worker/loop.py` |
| Global cap on concurrent mix analyses | exists (#574, merged in #630): `CLASSBEAT_MIX_ANALYSIS_CONCURRENCY`, default 1 (1 to 16). A job that must analyse and holds no slot is requeued with `analysis_slot` (attempt not counted) and `queue_claim` claims it only while fewer than the cap running jobs hold one. The count runs under one advisory lock **in the database, across all workers**; other jobs are claimed past a full cap. Kept below the summed `worker_concurrency` so analyses cannot take every slot | `docker/postgres/queue/20-queue.sql`, `jobs/queue.py`, `tests/test_analysis_slots.py`, `test_queue_functions.py` |
| Per-user daily limit on new analyses | exists (ADR 0016 §7, A4, **not** #577 or #626): 10 per user in 24 hours, taken before the listing, attach and fetch, so a failed fetch counts; a cache hit costs none | `design/mixlimit.py` |
| Per-user concurrent jobs | exists: `CLASSBEAT_JOBS_PER_USER`, default 3 since M7a (ADR 0005 §2, ADR 0011). Checked when a job is created or approved (`jobs/http.py` through `quota/policies.py`), not in the queue SQL, so it holds unchanged with N workers | `config.py`, `jobs/http.py`, `quota/policies.py` |
| General API and Save/Update rate limits | exist (#577, merged in #626): per-user and per-IP request windows, saves per hour and per day. They bound the web tier and MOWL writes; they do not bound analysis | `config.py` |
| Plan quotas | exist (ADR 0011): classes per month, monthly cost, class length. They count design jobs and model cost; an analysis takes no credit of its own. I found no analysis-specific plan quota | `quota/`, `plans/` |
| Per-user analysis cache | exists: `swedebeat_mix_maps`, one row per `(owner_id, mix_id)`, 30 days, newest 20 per user, valid only for the same MOWL connection, the same MOWL playlist and the current `music.MAP_VERSION`. A hit makes no request at all | `design/swedebeat_music.py` |
| Fresh-analysis marker | exists: `jobs.fresh_analysis`, only for the "class ready" mail (ME5) | `jobs/mail_handoff.py` |
| Queue and lease machinery for several workers | exists: `FOR UPDATE SKIP LOCKED` claims, a lease and a fencing token per job, a worker id from the container's host name, the reaper | ADR 0005, `queue_claim` |

### What is missing (verified absent)

1. **No queue-wait or analysis-time measurement.** `queue_stats()` and `GET /api/admin/jobs` give
   counts per state and failures per category. `jobs.created_at`, `available_at` and `started_at`
   exist, but `started_at` is set on the first claim only (`coalesce`), so it says nothing about the
   wait of a job requeued for an analysis slot; the `requeued` event has no wait. The fetch logs its
   bytes and seconds (`swedebeat media fetched`); the analysis's own seconds are not recorded
   anywhere, and nothing measures CPU use or web latency during an analysis.
2. **No cache shared between users.** The cache is per user on purpose: ADR 0016 §3 decided
   "never shared between users" (mixes are personal), and ADR 0007 §11 lists what a shared store
   must come with. None of it is built.
3. **No CPU or memory limits** on any service in `compose.yaml` or `compose.prod.yaml`: an analysis
   (up to 1.5 GiB, one or more cores for up to 180 s) shares the box with `web` and `db`.
4. **No way to run an analysis-only worker.** The worker claims every registered kind
   (`queue.claim(..., kinds=registry.names(), slots)`). The claim can filter on kind and on the
   analysis cap, not on "jobs that need analysis", and the analysis is a stage of a job that also
   needs the model key and the Spotify, MOWL and Swedebeat connections.
5. **Never run with more than one worker container.** The queue-level tests claim as distinct
   worker ids; no test or drill starts `--scale worker=N` against one database.

### What a worker needs (for stage 3)

- **Local disk:** only a private temp directory (`<tmp>/classbeat-mix-audio`, mode 0700, one
  subdirectory per fetch of up to 250 MB plus the child's numba cache), removed on every way out
  and swept at worker start (`sweep_work_root`). No volume, no state that must survive a restart.
  The worker's `/tmp` is the container's writable layer today.
- **Secrets the worker container mounts** (`compose.prod.yaml`): `database_password`,
  `app_secret_key`, `connection_kek`, `anthropic_api_key`, `openai_api_key`, `deepseek_api_key`.
  The analysis path itself needs the database, `connection_kek` (it opens the user's MOWL token to
  list, attach and ask for the temporary link) and egress to MOWL and to the one confirmed media host
  (`MOWL_MEDIA_HOSTS` in `egress.py`, a literal, confirmed in the owner's live check of 2026-11-03
  and added in #524). Production therefore does fetch and analyse audio for an uncached mix today:
  that load, and that outbound access, is what a stage 3 host must account for. The analysis child
  gets none of it: a minimal environment, no socket, no files outside its directory. But the
  process around it holds the KEK ring and so can decrypt every user's MOWL, Spotify and Swedebeat
  tokens. Any host that runs this worker holds that power.
- **Network:** the database is on the internal `backend` network only and publishes no port; the
  worker also joins `egress`.

## Decision

Stage by stage. A stage starts only when the trigger of the one before it fires and its
preconditions hold. Until then, do nothing beyond stage 1.

### 1. Cap, measure, cache (now)

Keep what exists (the global analysis cap, the daily and concurrent-job limits, the per-user
cache). Add, in follow-up issues (not in this PR):

- **Measure.** Record for each job that analyses: the queue wait (from `available_at` to the claim
  that holds the analysis slot) and the analysis seconds (fetch and decode, separately), and make
  the p95 of each readable by the admin and by a command like `jobs stats`, without ids and without
  titles. Queue wait of jobs with and without `analysis_slot` are kept apart. Also a number that
  shows analysis CPU hurting the web tier (web request latency during an analysis, or host load).
  The mechanism is chosen in the issue and stays within what the project has (structured log lines
  and a `SECURITY DEFINER` stats function in the style of `queue_stats`); this ADR does not add a
  monitoring stack.
- **Limit the damage.** Give the worker container a CPU and memory ceiling in the compose files so
  an analysis cannot starve `web` and `db` on the one box.
- **Cache once per mix across users: only after a decision, and not assumed.** ADR 0016 chose
  per-user maps. A shared map saves the fetch (up to 250 MB) and the decode, not the attach: each
  user's class still needs the mix attached in their own MOWL account. Sharing needs, before any
  code: (a) the owner's decision that a map derived from audio fetched under one user's entitlement
  may serve another user (a licence question, not a technical one); (b) a live check that the same
  `mix_id` is the same audio and the same listed length for every user (unverified; live checks
  need the owner's authorization); (c) everything ADR 0007 §11 requires of a shared analysis: a
  system table keyed by mix, length fingerprint and `MAP_VERSION`, readable only through a narrow
  `SECURITY DEFINER` function that checks the requester's own link first, owned rows recording only
  the analysis id, and the purge function. If (a) is no, stage 1 stays measure and limit.

### 2. More workers on the same box

`docker compose up -d --scale worker=N` is the next step. Reading the code, nothing prevents it:
the worker service has no `container_name` and no published port, the worker id comes from the host
name, claims are `SKIP LOCKED`, leases and fencing are per job, and the analysis cap is counted in
the database. To record before use:

- It raises **job** throughput, not **analysis** throughput: the cap is global, so
  `CLASSBEAT_MIX_ANALYSIS_CONCURRENCY` must be raised as well, the same value on every worker
  (each worker passes its own value to `queue_claim`), and kept below the summed
  `worker_concurrency`.
- The database pool is `2n + 2` per worker: N workers need `N * (2 * concurrency + 2)` connections
  on top of web's, against Postgres's `max_connections` (not set in the compose files, so the
  image default).
- Each worker has its own `/tmp`. Never share one temp volume between workers: `sweep_work_root` at
  start would delete another worker's running audio.
- Memory: the cap times 1.5 GiB plus the process itself must fit beside `web` and `db`.
- The first use is preceded by a test or drill with two worker containers against one database
  (jobs finish once, a killed worker's job is reaped, the cap holds), because none exists.

**Trigger to leave stage 1 for stage 2:** the p95 queue wait of jobs that need an analysis slot is
above about one minute at peak (the window and the minimum number of samples for a p95, for example the busiest hour of the week, are fixed by the stage 1 measurement issue, so the trigger can be checked), with idle CPU on the box (the cap, not the box, is the limit: raise
the cap or add a worker). Stage 1's CPU ceiling covers a box that is already full.

### 3. A separate CPU-optimised analysis host

A second server runs a worker container against the same Postgres over a private network or
WireGuard. **Trigger:** the p95 queue wait is still above about one minute at peak (as defined for stage 2) with the box's
CPU saturated by the workers of stage 2, or analysis CPU measurably starves the web tier despite
the ceiling. **Preconditions, none met today** (this stage needs its own ADR and its own security
review, Opus, before any code):

- **What the host may hold is the central question.** Today the only worker is the full worker
  (all model keys, `connection_kek`, every connection kind). Options to be compared in that ADR:
  (i) the full worker on a second host (simplest, widest: a second place where every user's tokens
  can be decrypted); (ii) a claim filter and a split of the analysis into its own job kind, so the
  host needs only `connection_kek` and no model key; (iii) the main worker does the MOWL calls and
  hands the analysis host only audio it fetched, so the host holds no KEK (the temporary link is a
  signed URL and must not be stored or logged, so this needs a design). (ii) and (iii) change the
  job pipeline and are code, with the fencing and idempotency tests of ADR 0005.
- **The database stays unreachable from the internet.** A private network or WireGuard, TLS to
  Postgres with certificate verification, `pg_hba.conf` limited to that host and the
  `classbeat_app` role (the worker's `verify_database_role` check keeps refusing a privileged
  role), and a published port on the db host that nothing else can reach. Today the db publishes
  none.
- **Secrets on a second host:** how the files are put there with mode 0640 and the secrets group,
  rotated and revoked, and that its logs and backups follow the same rules (the web/worker split
  of ADR 0006 §9 widens to three processes on two hosts).
- **Audio on the host:** a temp directory in memory or on an encrypted disk; the swept, 0700 root
  stays. The outbound allowlist (`install_worker_allowlist`) still applies in the process.
- **Operations:** patching, monitoring and an owner-authorized deploy for a second server (a paid
  service and a deploy, which agents never do alone).

### 4. Queue-length autoscaling

Only if load turns out to be **spiky** (long idle periods, bursts): a managed container service
that starts workers from the queue length. It inherits every precondition of stage 3 and adds the
secrets in a provider's store and a database reachable from outside the box. **Trigger:** stage 3
runs mostly idle yet misses the one-minute target during bursts, and the cost of an always-on host
exceeds the managed service's. Not designed here.

## Alternatives

- **Kubernetes.** Rejected for now: operations and attack surface for one maintainer and two or
  three services, while the Postgres queue already coordinates work, leases and fencing. Revisit
  only if there are many services or hosts.
- **A load balancer.** Rejected: it spreads web requests, and the web tier is not the expected
  bottleneck. Analysis is queue-driven, and the queue already balances it across workers.
- **A broker (Redis, RabbitMQ, a cloud queue).** Rejected: another service, another place for job
  state, and it would duplicate the lease and fencing guarantees the database queue gives
  atomically with the job's own rows (ADR 0005).
- **A bigger single server.** The cheapest answer to a first spike and not excluded: stage 2 is
  already this plus more worker processes.
- **Raising the cap only.** Part of stage 2; alone it lets analyses crowd out design jobs, which is
  what the #574 cap prevents.
- **Analysis inside the web process.** Rejected: the child-process limits and the web/worker secrets
  split (ADR 0006 §9) exist so a hostile file never runs near the session cookie keys.

## Consequences

- Nothing changes in the code or the deployment with this ADR. Stage 1 becomes follow-up issues
  (below); stages 2 to 4 wait for their triggers, so no infrastructure is built before measured
  need.
- The ADR states what is missing so later work does not mistake the existing cap, daily limit and
  per-user cache for new work, and the Spotify path for something we analyse.
- A shared cache reverses an earlier decision (ADR 0016 §3) and so needs the owner's licence
  decision; the technical conditions are already in ADR 0007 §11. Until then every user's first use
  of a mix costs one full fetch and analysis (up to 10 a day per user).
- A second analysis host cannot be added by configuration: it needs a code change to split or filter
  the analysis, and a security review of where the KEK ring may live. This is deliberate friction.
- Owner decisions this ADR needs later, not now: the licence question for a shared map; the
  authorization to deploy a second server and to spend on it.

## Follow-up

The Architect opens a separate issue for stage 1's missing pieces: (1) the queue wait and analysis
seconds measurement with a readable p95 and a CPU/latency number for the web tier; (2) CPU and
memory ceilings for the worker in the compose files; (3) a two-worker test or drill and the stage 2
notes in the developer commands; (4) the shared-map decision, left to the owner's answer on
licence and a live check of `mix_id` identity.

## Status

Proposed. Accepted when this PR merges after an Opus `PASS`.
