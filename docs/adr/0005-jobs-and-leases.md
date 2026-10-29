# ADR 0005: Jobs, leases and external operations

Status: Proposed

## Context

M3a builds the job system of roadmap §4: long agent runs in the worker, progress the browser can
follow, a wait for the user's approval that survives restarts, and external writes that are never
done twice. ADR 0002 §3 left two things to this milestone: the `SECURITY DEFINER` functions for
the cross-user parts of the queue (claim, reaper) and the privileged step that hands them to their
owner role. ADR 0004 §3 fixed that a job stores only a connection id and the worker rebuilds
`Credentials` from the job's `UserContext`. M3a's "done when": crash, lease and stale-worker tests
green, and an E2E run with fakes that drafts a class from a MOWL playlist.

This ADR covers the jobs side (roadmap §4). [ADR 0006](0006-agent-runs.md) covers the agent loop
(§5) and the worker's secrets and outbound allowlist (§9), and ends with the M3a issue breakdown
for both.

What exists (M0 to M2): `classbeat.worker.loop` runs a heartbeat log and the hourly auth purge;
the worker checks its database role and loads the KEK ring at start; `classbeat.context` already
names `classbeat.jobs` as the second issuer of `UserContext`; `user_session` and `system_session`
own their transactions; owned tables follow ADR 0002 §2 and the guards of §6.

## Decision

### 1. Tables

All tables below are **owned** (ADR 0002 §2: `owner_id` referencing `users(id) ON DELETE
CASCADE`, `UNIQUE (owner_id, id)`, owner-inclusive foreign keys, `enable_owner_rls`). As in
ADR 0004, `id` keeps the `gen_random_uuid()` default but the application **always supplies it**
(`uuid4()` before the insert), so a retried request or a fenced write can name its row up front.
Every column is `NOT NULL` unless marked otherwise; every check below is a database `CHECK`.
Hashes are SHA-256 of canonical JSON (ADR 0004 §2), `octet_length(...) = 32`.

**`classes`** (minimal; M3b adds the class page, saved workouts and the rest):
`id`, `owner_id`, `title text` (1 to 100 characters), `created_at`, `updated_at`.

**`class_revisions`** (immutable): `id`, `owner_id`, `class_id` → `classes` (`ON DELETE
CASCADE`), `number int` (≥ 1; `UNIQUE (owner_id, class_id, number)`), `kind text` check
`IN ('draft', 'song_list')`, `content jsonb` (a validated `WorkoutDraft` or `SongList`, ADR 0006
§4; `octet_length(content::text) <= 65536`), `content_hash bytea`, `music jsonb` (the music
reference and fingerprint, ADR 0006 §6), `provenance jsonb` (ADR 0006 §6), `job_id uuid NULL` →
`jobs` (`ON DELETE SET NULL (job_id)`), `parent_id uuid NULL` → `class_revisions`
(`ON DELETE SET NULL (parent_id)`), `created_at`. A restrictive policy
`revisions_immutable AS RESTRICTIVE FOR UPDATE TO PUBLIC USING (false)` makes rows
un-updatable for every role, the owner included (FORCE). Referential actions run without row
security, so deleting a class still cascades and deleting a job still sets `job_id` to `NULL`
(J1 tests both).

**`jobs`**:

| Column | Notes |
|---|---|
| `id`, `owner_id` | as above |
| `kind text` | check `kind ~ '^[a-z_]{1,40}$'`; the code registry (§2) holds the valid kinds. M3a: `design_draft`; M4 adds song picking |
| `state text` | check `IN ('queued', 'running', 'waiting_for_approval', 'completed', 'failed', 'cancelled', 'expired')` |
| `stage text NULL` | the current stage of the kind's pipeline (ADR 0006 §3) |
| `class_id uuid` | → `classes`, `ON DELETE CASCADE` |
| `input jsonb` | the validated job input (§2); `octet_length(input::text) <= 16384`; never secrets |
| `idempotency_key text`, `request_hash bytea` | from the browser (§2); key check `^[A-Za-z0-9_-]{16,64}$`; `UNIQUE (owner_id, idempotency_key)` |
| `attempt int`, `max_attempts int` | attempts of the current **phase**; `0 <= attempt <= max_attempts`, `1 <= max_attempts <= 10` (default 3). **Approve** starts a new phase and resets `attempt` to 0 (§2); the cost ceiling still spans the whole job (ADR 0006 §5) |
| `available_at timestamptz` | a queued job is claimable from then on (backoff, §4) |
| `lease_version bigint` | the fencing token (§3), default 0, only ever increases |
| `leased_by text NULL`, `lease_expires_at timestamptz NULL`, `heartbeat_at timestamptz NULL` | set exactly while `running`: check `(state = 'running') = (leased_by IS NOT NULL AND lease_expires_at IS NOT NULL)`; `leased_by` ≤ 100 characters |
| `cancel_requested_at timestamptz NULL` | set by the user (§2) |
| `approval_revision_id uuid NULL`, `approval_hash bytea NULL`, `expires_at timestamptz NULL` | the list waiting for approval (→ `class_revisions`, `ON DELETE SET NULL (approval_revision_id)`); check `state <> 'waiting_for_approval' OR (approval_hash IS NOT NULL AND expires_at IS NOT NULL)` |
| `approved_at timestamptz NULL` | |
| `error_category text NULL` | from §4's list; check `(state = 'failed') = (error_category IS NOT NULL)` |
| `credit_state text` | check `IN ('none', 'reserved', 'committed', 'released')` (§5) |
| `next_seq bigint` | the last `job_events.seq` handed out, default 0 |
| `created_at`, `updated_at`, `started_at NULL`, `finished_at NULL` | check `(state IN ('completed', 'failed', 'cancelled', 'expired')) = (finished_at IS NOT NULL)` |

Partial indexes (not unique): `(available_at) WHERE state = 'queued'`, `(lease_expires_at) WHERE
state = 'running'`, `(expires_at) WHERE state = 'waiting_for_approval'`, and
`(owner_id, created_at)`. **One open job per class:** a unique index on `(owner_id, class_id)
WHERE state IN ('queued', 'running', 'waiting_for_approval')` (it includes `owner_id`, so guard 1
accepts it).

**`job_events`**: `id`, `owner_id`, `job_id` → `jobs` (`ON DELETE CASCADE`), `seq bigint`
(`UNIQUE (owner_id, job_id, seq)`), `kind text` (check on §6's list), `payload jsonb`
(`octet_length(payload::text) <= 2048`), `lease_version bigint` (the token the writer held, 0 for
user writes), `created_at`. Rows are never updated or deleted, enforced by restrictive
`USING (false)` policies for `UPDATE` and `DELETE` (amended in J1, #84); cascades still remove them.

**`external_operations`** (§7): `id`, `owner_id`, `job_id uuid NULL` → `jobs` (`ON DELETE SET NULL
(job_id)`), `kind text` (check on a list; M3a: `mowl_attach_spotify_playlist`; M3b adds the MOWL
saves, M4 the Spotify writes), `idempotency_key text` (≤ 120 characters, built per kind, §7;
`UNIQUE (owner_id, kind, idempotency_key)`), `request_hash bytea` (defined per kind, §7), `state
text` check `IN ('pending', 'succeeded', 'failed', 'uncertain')`, `external_id text NULL` (≤ 200;
check `state <> 'succeeded' OR external_id IS NOT NULL`), `result jsonb` (default `'{}'`, ≤ 4096
bytes, non-secret), `error_category text NULL`, `lease_version bigint` (the job's token when the
call was made), `resolved_by text NULL` check `IN ('worker', 'user', 'admin')`, `created_at`,
`updated_at`, `resolved_at NULL`. A row outlives its job and class (`job_id` becomes `NULL`) and
is deleted with the account, and the attach rows also with the MOWL connection (ADR 0006 §3); #71
decides whether `result` must go earlier.

**`mowl_playlist_links`** (the per-user MOWL playlist link of roadmap §6, never shared): `id`,
`owner_id`, `source text` check `IN ('spotify')`, `source_id text` check `~ '^[0-9A-Za-z]{22}$'`,
`playlist_id bigint` (MOWL's `PlaylistID`, > 0), `UNIQUE (owner_id, source, source_id)`,
`created_at`. Inserted with `ON CONFLICT (owner_id, source, source_id) DO NOTHING` (no secret in
the row, so ADR 0004 §4's rule against upserts doesn't apply). It holds no track data. It is
deleted with the account and, with the attach operations, when MOWL is disconnected (ADR 0006 §3);
#71 decides whether it also goes when no class refers to it.

M3a adds no music-analysis table: the worker builds the analysis in memory for each job (ADR 0006
§2). Shared analysis and its purge come with #71 (before M3b/M4).

### 2. States and transitions

```
queued ──claim──> running ──> completed | failed | cancelled
  ^                 │  │
  └──retry/reap─────┘  └──> waiting_for_approval ──approve──> queued
                                  └──> cancelled | expired
```

| From → to | By | When / how |
|---|---|---|
| (new) → `queued` | user session | `POST /api/jobs` (below); `credit_state = 'reserved'` in the same transaction |
| `queued` → `running` | claimer (§3) | `queue_claim`, only while `attempt < max_attempts`; `attempt + 1`, new lease |
| `running` → `running` | worker | heartbeat, fenced (§3) |
| `running` → `queued` | worker | retryable error and `attempt < max_attempts`; `available_at` = backoff (§4). With `cancel_requested_at` set it goes to `cancelled` instead |
| `running` → `queued` | worker | shutdown hand-back or `operation_busy` (§3, §7): fenced, `attempt - 1` (it doesn't count), `lease_version + 1`; `cancelled` instead if a cancel was requested |
| `queued` → `failed` | reaper | a queued row with `attempt >= max_attempts` (should not exist): `internal`, so it never blocks the queue |
| `running` → `queued` / `failed` / `cancelled` | reaper | lease expired (§3): requeue, `lease_lost` when attempts are spent, or `cancelled` if a cancel was requested |
| `running` → `waiting_for_approval` | worker | fenced; writes the `song_list` revision, `approval_hash`, `expires_at` (§4); lease cleared |
| `waiting_for_approval` → `queued` | user session | **Approve** (below): a new phase, `attempt = 0`, `available_at = now()`; the next claim makes it `running`. This is roadmap §4's `waiting_for_approval → running`: the job goes back through the queue so it takes a slot and a fresh lease |
| `running` → `completed` / `failed` / `cancelled` | worker | fenced; `cancelled` only after `cancel_requested_at` (§4) |
| `queued`, `waiting_for_approval` → `cancelled` | user session | **Cancel**, immediately |
| `running` → (cancel requested) | user session | **Cancel** sets `cancel_requested_at`; the worker stops at its next checkpoint (§4) |
| `waiting_for_approval` → `expired` | expiry sweep (§3) | `expires_at` passed |

No other transition exists. Every transition is one `UPDATE … WHERE id = :id AND state = :from`
(plus the fence for worker writes) that also inserts its event (§6); 0 rows means someone else
moved the job first and the caller re-reads. Terminal states never change again.

**Who acts.** The user's session acts only on the user's own jobs (`UserContext` from
`classbeat.auth`, RLS, 404 for a foreign id). The worker acts only on a job it holds the lease
for. The claimer, reaper and expiry sweep are the only cross-user paths, through §3's functions.

**Kinds.** `classbeat.jobs.kinds` maps each kind to its input model (Pydantic, `extra="forbid"`)
and its stage pipeline (ADR 0006 §3). M3a registers `design_draft`, input `{class_id | title,
brief, music: {source: "mowl_spotify_playlist", spotify_playlist_id}, mowl_connection_id}`. Tests
register a test-only kind whose fake stage ends with a song list, to exercise approval before M4.

**Creating a job.** `POST /api/jobs` (signed in, CSRF) needs an `Idempotency-Key` header (16 to
64 URL-safe characters, made by the browser per user action). In one transaction under
`pg_advisory_xact_lock` on the owner: look up `(owner_id, idempotency_key)`; the same key with the
same `request_hash` (canonical JSON of the validated body) returns the existing job (200), a
different hash is 409 `idempotency_mismatch`; a class that already has an open job is 409
`class_busy` (the unique index of §1); otherwise check the per-user limit
(`CLASSBEAT_JOBS_PER_USER`, default 2, counts `queued` and `running`; 429 above it), create the
class if none was named, reserve credit (§5), insert the job and its `queued` event, and
`pg_notify('jobs_queued', '')`. The MOWL connection id must equal `ctx.connection("mowl")`.
**Approve** and **Cancel** are idempotent by their target state: approving again with the same
`revision_id` and hash after the job left the wait (`approved_at` set for that revision) returns
200 with the current state, and cancelling a job that is cancelled or has a cancel requested
returns 200; neither writes a second event.

**Approve** (`POST /api/jobs/{id}/approve`, body `{revision_id, content_hash}`): recent auth is
not required (it is the user's own work); CSRF is. In one transaction, `SELECT … FOR UPDATE` on
the job, then refuse with 409 and a reason unless: the job is `waiting_for_approval` and
`expires_at > now()` (`expired`), `revision_id = approval_revision_id` and it is the newest
`song_list` revision of the class (`superseded`), `content_hash = approval_hash`
(`hash_mismatch`), the needed connections are in `ctx.connections` and
`classbeat.connections` reports them `connected` (`ctx.connections` holds no status, ADR 0004
§1; `not_connected`), the per-user limit above has room, counting `queued` and `running` as at
creation (`busy`, 429; a wait holds no slot, and the list stays approvable until it expires), and
the credit seam allows it (`quota`). With one open job per class, no other job can add a list
meanwhile, so the `superseded` check needs no lock beyond the job row. A job waits for approval
**at most once**, so the `attempt` reset happens at most once per job; in M4 a revision that
changes songs is a new job with its own wait. Accepted: `approved_at = now()`, `attempt = 0`,
state `queued`, event `approved`. A refused approval starts nothing.

### 3. Claiming, leases and fencing

**The queue role and functions.** A role `classbeat_queue` (`NOLOGIN NOSUPERUSER NOCREATEDB
NOCREATEROLE NOREPLICATION BYPASSRLS NOINHERIT`, member of no role, nobody a member of it)
owns five `SECURITY DEFINER` functions (ADR 0002 §3). Each sets
`search_path = pg_catalog, pg_temp`, schema-qualifies every table, takes no id from a
caller, and returns only `(job_id, owner_id, lease_version)` or counts. (Amended in J1, #84: `public`
is left off the path so no object there can shadow a built-in, and the queue check refuses any other
function or operator in `public`. `queue_claim` and `queue_unsettled` return
`(job_id, owner_id, lease_version)` rows, `queue_reap` and `queue_expire` an integer count,
`queue_stats` `(metric, total)` rows; a `p_limit` outside 1 to 100 raises.)

**No user code runs as `classbeat_queue`** (amended in #102, finding B2 of #99): a trigger fires
without `EXECUTE` and, inside a queue function, runs as `classbeat_queue`, so the queue tables
carry no triggers or rules and the database holds no user-defined function, procedure, aggregate
or operator besides the five. The queue check, guard 9 and `20-queue.sql` (naming each object)
refuse any `pg_proc`/`pg_operator` row at OID ≥ 16384 or outside `pg_catalog`,
`information_schema`, `pg_toast` and the temp schemas (the five in `public` excepted), any
non-internal trigger (foreign-key triggers are internal) and any rule but a view's `_RETURN`, on
any table; defaults, `CHECK`s and index expressions then reach only built-ins. Guard 2 refuses
`PUBLIC` `EXECUTE` on any user function. Write `updated_at` in the statement, not by trigger.
Extensions need their own schema (never `public` or `pg_catalog`), and adding one, or any user
function, needs an amendment here that allows that schema.

| Function | Does |
|---|---|
| `queue_claim(p_worker text, p_lease_seconds int, p_kinds text[])` | checks `p_worker ~ '^[A-Za-z0-9._:-]{1,100}$'` and 30 ≤ `p_lease_seconds` ≤ 600; `SELECT id … WHERE state = 'queued' AND available_at <= now() AND attempt < max_attempts AND kind = ANY(p_kinds) ORDER BY available_at, created_at LIMIT 1 FOR UPDATE SKIP LOCKED`; sets `running`, `attempt + 1`, `lease_version + 1`, `leased_by`, `lease_expires_at = now() + lease`, `heartbeat_at`, `started_at` (first time); inserts the `claimed` event; returns the row or nothing |
| `queue_reap(p_limit int)` | for up to `p_limit` (≤ 100) `running` jobs with `lease_expires_at < now()`, `FOR UPDATE SKIP LOCKED`: `lease_version + 1` (fences the old holder), then `cancelled` if a cancel was requested, else `queued` with backoff if `attempt < max_attempts`, else `failed` with `lease_lost`; also fails any `queued` row with `attempt >= max_attempts` as `internal`; inserts the events. A bad row is skipped, never raised on, so one row cannot stall other users' claims |
| `queue_expire(p_limit int)` | for `waiting_for_approval` jobs with `expires_at < now()`, `FOR UPDATE SKIP LOCKED`: `expired`, event |
| `queue_unsettled(p_limit int)` | lists terminal jobs with `credit_state = 'reserved'` (for §5); no update |
| `queue_stats()` | counts per state and of `uncertain` external operations, for the operator CLI and the admin seam (§7); no ids |

`classbeat_queue` gets exactly `USAGE` on `public`, `SELECT, UPDATE` on `jobs`,
`SELECT, INSERT` on `job_events`, and `SELECT` on `external_operations`. `classbeat_app` gets
`EXECUTE` on the functions; `PUBLIC` gets nothing. All times come from the database's `now()`,
never a worker clock.

**The privileged step** (ADR 0002 §3 leaves it to this ADR). Handing a function to
`classbeat_queue` needs a superuser (Alembic as `classbeat_owner` can't: it would need membership
in a `BYPASSRLS` role), so the role, its grants and the functions live in one idempotent script,
`docker/postgres/queue/20-queue.sql`, run **as the superuser** by a one-shot compose service
`db-functions` (image `postgres:17.11`, `psql --single-transaction -v ON_ERROR_STOP=1`), which
depends on `migrate` completing and which web and worker depend on. Its only secret is
`postgres_password`, mounted as a file; its entrypoint builds a `PGPASSFILE` from it at run time,
never an `environment:` value, so the `docker inspect` check holds. **This amends ADR 0002 §4's
role table:** the superuser is used by the db container's init script **and** by `db-functions`,
nothing else. The script refuses to run unless `current_user` is a superuser, creates or repairs
the role with the attributes above, revokes every table and schema privilege `classbeat_queue`
holds (not the owner's own `EXECUTE` on its functions) before re-granting exactly the list above,
runs `CREATE OR REPLACE FUNCTION … ; ALTER FUNCTION … OWNER TO classbeat_queue; REVOKE ALL … FROM
PUBLIC; GRANT EXECUTE … TO classbeat_app`, and sets `COMMENT ON FUNCTION … IS 'classbeat-queue
v<N>'`. Migrations never create these functions. A migration that changes a column the functions
read bumps `N` in the same PR. The database tests run the same script as the test superuser. On
the VPS (M6) the same service runs on deploy; during a deploy that bumps `N`, web and worker still
running fail the queue check on new connections until they are recreated, which is acceptable on
one host.

**Startup check.** Web and worker extend `verify_database_role` (ADR 0002 §4): each function
exists once, is `SECURITY DEFINER`, is owned by `classbeat_queue`, has exactly that `search_path`
in `proconfig`, the expected version comment, an `md5(pg_get_functiondef(oid))` equal to the one
the code ships (regenerated by a CI step against the pinned Postgres image, which fails when it is
stale; the README says how to refresh it), and an ACL of exactly
`{classbeat_queue=X/classbeat_queue, classbeat_app=X/classbeat_queue}` (the owner's own entry
included); `classbeat_queue` has the attributes above, no memberships either way, and exactly the
privileges above. Like the role check it runs again on every new pooled connection, and because
that check returns early while the database is down, the worker additionally **claims nothing
until the queue check has passed** once. Any difference refuses to start (no override). Guard 2
allows `classbeat_queue` exactly its listed privileges on the three tables and `USAGE` on schema
`public`, keeping its grantee check for every other role.

**From claim to `UserContext`.** `classbeat.jobs` is the job issuer of ADR 0002 §1. After a
claim it:
1. asks `classbeat.auth` (guard 8) whether the owner exists and is not disabled; a disabled
   owner's job fails with `account_disabled`;
2. issues a context with `role="user"` (a job never carries admin authority, whoever owns it),
   empty `entitlements` in M3a, and the owner's connection refs read through
   `classbeat.connections.refs_in` in that owner's `user_session` (ADR 0004 §1's two steps);
3. reads the job's row and input in a `user_session` with that context. Nothing about the user
   comes from anywhere but the claimed row. The claim functions return no data beyond the id,
   owner and token, so there is no ambient authority: everything else is read under RLS as the
   owner.

**Fencing.** The worker keeps `(job_id, lease_version)` for the run. **Every** write it makes goes
through one helper, `fenced_session(factory, job)`: a `user_session` whose first statement is the
fence. An AST guard **allowlists** the modules that may call `user_session` directly
(`classbeat.jobs`, `classbeat.connections`, `classbeat.auth`; a later owned-data package joins the
list in its own PR); anything else, the worker and agent code included, fails it. The one
exemption during a run is `classbeat.connections`' own status, `last_error` and `last_used_at`
writes, which are conditional on `wrapped_dek` (ADR 0004 §4) and touch no job data. A restrictive
insert policy comparing `lease_version` is not added; the helper and the guard carry the rule.

```sql
UPDATE jobs SET updated_at = now()
 WHERE id = :job_id AND owner_id = :owner_id AND lease_version = :v AND state = 'running'
RETURNING cancel_requested_at, next_seq
```

0 rows raises `LeaseLost` and rolls the transaction back. The row lock taken by the fence is held
until commit, so the reaper (which skips locked rows) cannot bump the token in between. Events
take their `seq` in the same statement (`next_seq = next_seq + 1`) and record `lease_version`;
revisions, `agent_runs` (ADR 0006), external operations and state changes are written in the same
fenced transaction. A stale worker therefore cannot add an event, a revision or an operation
result, nor move the job.

**Leases.** `CLASSBEAT_JOB_LEASE_SECONDS` (default 120). A heartbeat task per running job extends
`lease_expires_at = GREATEST(lease_expires_at, now() + lease)` every lease/3 with a fenced
update in its own short transaction, so it never shortens an extension made for a call. A failed
heartbeat (0 rows or 3 database errors in a row) marks the run lost: it stops at the next
checkpoint, starts no further model, library or external call, and abandons any in-flight model
request. The fenced transaction that writes an external operation's `pending` row (§7) also
extends `lease_expires_at` to at least `now()` + the call's timeout + 30 s, so a slow provider
call never outlives the lease. No transaction is ever open across a model or provider call
(ADR 0002 §5, `idle_in_transaction_session_timeout`).

**The worker loop** replaces today's heartbeat log: up to `CLASSBEAT_WORKER_CONCURRENCY` (default
2) runs as asyncio tasks; when a slot is free it calls `queue_claim`, woken by `LISTEN
jobs_queued` on its own connection or a 2 s poll; every 30 s it runs `queue_reap`, `queue_expire`
and the settle step (§5); the hourly auth purge stays. Several workers are safe: `SKIP LOCKED`
gives each job to one claimer, and the fence makes a lost lease harmless.

**Shutdown.** Compose gives the worker `stop_grace_period: 30s`. On SIGTERM the worker stops
claiming, starts no new model turn or external call, waits up to 25 s for calls in flight to
return and record their outcome, then hands each lease back with a fenced requeue (`state =
'queued'`, `attempt - 1`, `lease_version + 1`, event `requeued`; `cancelled` if a cancel was
requested), so a deploy costs no attempt. These transactions run under the role's 5 s
`lock_timeout`, inside the grace period. An external call still in flight after 25 s is left
`pending` under the old token, so the next holder treats it as `uncertain` and the user resolves
it (§7); acceptable for a rare slow call during a deploy. Anything not handed back is reaped
when the lease expires.

### 4. Retries, cancellation and expiry

Every failure maps to one category (the same words as ADR 0004 §5 where they overlap):

| Category | Retry | From |
|---|---|---|
| `upstream_unavailable`, `rate_limited` | yes | `ConnectionFailure` of those categories (ADR 0004 §5); `rate_limited` waits at least its `Retry-After` |
| `model_unavailable` | yes | model API 429, 5xx, overload, timeout or connection error after the SDK's own retries (ADR 0006 §2) |
| `lease_lost` | yes (reaper) | §3 |
| `operation_busy` | yes, without an attempt | another running job holds the same external operation (§7); requeued like a hand-back, with backoff |
| `not_connected` | no | the connection is gone, or its status is already `needs_reconnect`, before any provider call (ADR 0004 §3) |
| `needs_reconnect`, `key_unavailable`, `decrypt_failed` | no | during the run: a 401 or 403 (the status flips), or a key failure (ADR 0004 §2, §5) |
| `invalid_input`, `account_disabled` | no | §2, §3 |
| `model_refused`, `model_config`, `invalid_output`, `limit_exceeded` | no | ADR 0006 §2, §5 |
| `uncertain_write` | no | §7 |
| `outbound_blocked`, `internal` | no | ADR 0006 §9; anything else |

Retryable: back to `queued` with `available_at = now() + min(30 s · 2^(attempt-1), 15 min)`
with ±20 % jitter (for `rate_limited`, at least its hint, capped at 1 h). A retry starts the
current stage from its beginning; succeeded external operations are reused by key (§7), and model
cost counts across attempts against the job's ceiling (ADR 0006 §5). Non-retryable, or attempts
spent: `failed` with the category. The user sees a plain sentence per category, never an error
text.

**Cancellation.** The worker checks at its **checkpoints**: before each model request, before
each tool call, before each external operation, between the library reads of the music step
(ADR 0006 §3), and at each heartbeat (the fence returns `cancel_requested_at`). A requeue (retry,
reap or hand-back) of a job with a cancel requested ends it `cancelled` instead. A model turn or
tool call already running finishes; its cost is recorded. Then the worker writes `cancelled`
(fenced). A cancel never interrupts an external call between its `pending` row and its outcome.

**Expiry.** Entering `waiting_for_approval` sets `expires_at = now() +
CLASSBEAT_APPROVAL_TTL_HOURS` (default 168, 7 days; at least 1). `queue_expire` moves it to
`expired`; no model or provider call happens after the wait begins. The song-list revision stays
with the class; approving it is refused (`expired`), and the user can ask for a new list.

### 5. The credit seam

M5 owns quotas; M3a fixes only the seam. `classbeat.jobs.credits.CreditLedger` (a Protocol):

```python
async def reserve(db, ctx, job_id) -> None      # raises QuotaRefused; in the create transaction
async def allows(db, ctx, job_id) -> bool       # approve-time check (§2)
async def commit(db, ctx, job_id) -> None       # completed
async def release(db, ctx, job_id) -> None      # failed, cancelled, expired
```

M3a ships `UnmeteredLedger` (always allows; writes nothing beyond `jobs.credit_state`). **Settle**
runs for each row from `queue_unsettled`, in that owner's `user_session`: `UPDATE jobs SET
credit_state = 'committed' | 'released' WHERE id = :id AND credit_state = 'reserved' AND state IN
(terminal)` and the ledger call in the same transaction, so a credit settles exactly once whoever
ended the job (worker, reaper, sweep or user). `completed` commits; every other terminal state
releases (roadmap §4: system failure, early cancellation, expiry). Token usage and cost per job
live in `agent_runs` (ADR 0006 §6); M3b turns them into usage records.

### 6. Progress: events and SSE

**Event kinds and payloads.** `queued`, `claimed {attempt}`, `stage_started {stage}`, `tool_called
{tool, ok, error?}` (`error` an enum code, never text), `revision_created {revision_id, kind,
number}`, `waiting_for_approval {revision_id, expires_at}`, `approved`, `retry_scheduled
{category, available_at}`, `cancel_requested`, `cancelled`, `completed {revision_id}`, `failed
{category}`, `expired`, `requeued`, `external_operation {kind, state}`. Each kind has a Pydantic
model with `extra="forbid"`; payloads hold only enum values, ids of the owner's own rows, counts
and timestamps. **Never** in a payload: tokens or credentials, error or exception texts, model
text, tool arguments or results, track titles, artists or any other provider-derived data
(Amendment 1 allows those to the model, not into events), playlist ids or URLs. The browser reads
content from revisions, through owned endpoints.

**SSE.** `GET /api/jobs/{id}/events` (signed in; 404 for a foreign or unknown id). Each event is
`id: <seq>`, `event: <kind>`, `data: <payload JSON>`. A `Last-Event-ID` header (or `?after=` for
the first connect) replays events with a larger `seq`, then the stream waits. Every read is a
short `user_session` (ADR 0002 §5); none is held open. A comment line every 15 s keeps proxies
open; the server ends a stream after 10 minutes, after a terminal event and after
`waiting_for_approval`, and the browser reconnects with its last id. At most 5 open streams per
user (429 beyond). `GET /api/jobs/{id}` returns the state for clients without SSE.

**Wake-up.** The transaction that inserts an event also runs `pg_notify('job_events',
<job id>)`: only the id, as ADR 0002 §5 and T30 require. Each web process has one dedicated
`LISTEN` connection, which never queries owned tables, and wakes its local streams for that id;
the stream then reads in a user session, so a notification for someone else's job reveals
nothing. If the listener is down, streams poll every 5 s, and the listener reconnects with
backoff. Lost notifications only add latency.

### 7. External operations

Every write to a user's account goes through `external_operations` (roadmap §2, §4). The key is
deterministic and defined per kind: `<job_id>:<step>` for a job's own steps (M3b's saves), and
`spotify:<source_id>` for `mowl_attach_spotify_playlist`, so every job of the user on that
playlist finds the same row. The **request hash** is defined per kind and covers only what the
write does, never the job, class or connection that asks for it: for the attach, the canonical
JSON of `{kind, source, source_id}`. A hash mismatch therefore means a bug, never a later job.
Protocol for one call:

1. **Fenced transaction**, serialized on the operation row. `SELECT … FOR UPDATE` on `(owner_id,
   kind, key)`. No row: `INSERT … ON CONFLICT (owner_id, kind, idempotency_key) DO NOTHING
   RETURNING id` as `pending` with this `job_id` and `lease_version`; if it returns no row another
   job got there first, so re-load with `FOR UPDATE` and decide again. `succeeded` with the same
   hash: reuse `external_id` and `result`, no call. A different hash: `internal`. `pending` held
   by another job still `running` under that row's `lease_version`: `operation_busy`. `uncertain`,
   or any other `pending` (its holder died or lost its lease mid-call): treat as `uncertain` (step
   4). `failed`: `UPDATE … SET state = 'pending', job_id = :job, lease_version = :v WHERE id = :id
   AND state = 'failed'`, and 0 rows means decide again. Writing `pending` also extends the lease
   (§3). Of two racing jobs exactly one calls the provider. Holder rule: ADR 0006 §3 (#125).
2. **The call**, outside any transaction, through `classbeat.connections` (ADR 0004 §5).
3. **Fenced transaction** recording the outcome, conditional on `state = 'pending' AND job_id =
   :job AND lease_version = :v`: `succeeded` with the external id; `failed` when the provider
   refused (4xx) or the request provably never left (DNS, connection refused); `uncertain` when it
   may have taken effect (timeout or connection loss after sending, 5xx). If the fence fails here,
   the row stays `pending` under the old token and the next holder treats it as uncertain.
4. **Uncertain.** One automatic reconciliation through the kind's `Reconciler` (a read-only check
   with the user's credentials): `succeeded` if it finds the effect, `failed` if it proves there
   was none. Otherwise the row stays `uncertain` and the job fails with `uncertain_write`.
   **An uncertain write is never retried automatically.** The user sees what may or may not have
   happened, and can **Check again** (reconcile once more) or **Do it anyway** (their explicit
   decision: `failed`, `resolved_by = 'user'`, then a new job). Both are CSRF-checked user
   actions. Neither applies to a `succeeded` row (409): it is final.

**Admin seam.** `queue_stats()` gives counts only; `python -m classbeat jobs stats` prints them.
M5's admin page lists waiting approvals and uncertain operations through `classbeat_maint` with
the audit log (ADR 0002 §4); M3a adds no cross-user listing.

### 8. Threat model and tests

New threats T45 to T53 and accepted risks go into [the threat model](../security/threat-model.md)
with this ADR; T5, T11, T12, T13, T19 and T30 cite it.

**Tests** (database tests against Postgres, in CI; every one named in an issue's criteria):

- **Double claim:** many concurrent `queue_claim` calls on one job give it to exactly one; with N
  jobs and M claimers each job is claimed once.
- **Stale worker:** worker A claims, its lease expires, the reaper requeues, worker B claims;
  every write A then tries (event, revision, `agent_runs`, external-operation outcome, state
  change, heartbeat) fails with `LeaseLost` and changes nothing; B's run completes.
- **Crash:** a worker killed (task cancelled, connection dropped) mid-stage and mid-external-call;
  the reaper requeues; the retry reuses succeeded operations and turns a `pending` one from the
  old token into `uncertain`, which fails the job and is never re-sent to the fake upstream.
- **Lease expiry with attempts spent:** `failed`/`lease_lost`, credit released exactly once.
- **Phases:** a pre-approval phase that was retried (attempt 2 of 3) waits, is approved, and the
  post-approval phase runs with a fresh `attempt` and completes. A **poison row** (`queued` with
  `attempt = max_attempts`, written by the test) does not stop other users' jobs from being
  claimed, and `queue_reap` fails it as `internal`.
- **Shutdown:** SIGTERM during a run hands the lease back without counting an attempt and starts
  no new call; a cancel requested before a requeue ends the job `cancelled`.
- **Cancellation:** queued and waiting jobs cancel at once; a running job stops at the next
  checkpoint (between model turns and between tool calls) with no further fake-model or
  fake-MOWL call; a cancel racing completion ends in exactly one terminal state.
- **Approval:** wrong hash, superseded list, expired list, not-waiting job, another user's job
  (404), a missing connection, and a repeated approval; none starts a stage. Expiry: tests set
  `expires_at` in the past (the database clock is not faked) and check `queue_expire`, the
  refused approval and the released credit.
- **Racing attach:** two jobs claimed at the same moment on the same playlist (no row, and a
  `failed` row) give exactly one fake-MOWL attach and no `internal` failure; the other job gets
  `operation_busy` without losing an attempt, then reuses the result.
- **Account switch:** disconnect MOWL, reconnect as a different fake account; no stale link or
  reused `external_id`, and a new attach happens.
- **Uncertain write:** fake MOWL times out after accepting the attach; the job fails with
  `uncertain_write`; no second attach; **Check again** and **Do it anyway** behave as §7, and a
  **Check again** that succeeds writes the link.
- **Idempotency:** the same key and body returns the same job; the same key with another body is
  409; two keys create two jobs up to the per-user limit.
- **Isolation:** guards 1 and 2 cover the new tables and grants; new **guard 9** checks the queue
  role and functions as §3's startup check does; events, SSE and job endpoints give 404 for
  another user's ids; a `NOTIFY` carries only an id; event payload models refuse extra fields and
  a planted secret never appears in events, logs or SSE output; the stack check finds
  `postgres_password` mounted on `db` and `db-functions` only, never on web, worker, migrate or
  maint.
- **Outbound** (ADR 0006 §9): a host off the list, or a redirect to one, is refused.

## Alternatives

- **A system-table queue without RLS**, or a separate queue database: rejected by ADR 0002 §2 and
  §3 (two sources of truth; workers could scan everyone's jobs).
- **Advisory locks instead of leases**: they vanish with the connection, but they cannot fence
  writes made on another connection and hide state from the reaper and the UI.
- **Redis or a message broker**: no new infrastructure (ADR 0001); Postgres with `SKIP LOCKED` is
  enough at this scale and keeps job state transactional with its data.
- **Keeping the worker on the job across the approval wait**: holds a slot for up to 7 days and
  dies with the process; the roadmap asks for the release.
- **Retrying uncertain writes after a delay**: the roadmap forbids it; a duplicate workout or
  playlist is worse than asking the user.
- **WebSockets for progress**: SSE is one-way, resumes with `Last-Event-ID` and needs no extra
  protocol handling behind Caddy.

## Consequences

- The M3a issues (ADR 0006, last section) implement this. The schema and queue functions, the
  jobs core and fencing, the jobs API with approval, external operations, the first external write
  (the design pipeline) and the worker's secrets and allowlist get an Opus Developer (AGENTS.md).
- ADR 0002 §4's role table is amended: the superuser also runs `db-functions` (§3).
- Compose gains `db-functions`; the worktree script and CI run `20-queue.sql` after migrations.
  The superuser password is used by that service only, never by web, worker or `maint`.
- Every later job kind reuses the lease, fence, event and external-operation rules unchanged; M4
  adds Spotify kinds and reconcilers, M3b the MOWL saves, M5 a real `CreditLedger`.
- Guard 2's allowlist grows by `EXECUTE` on the queue functions for `classbeat_app` and by
  `classbeat_queue`'s table privileges and `USAGE` on `public`; guard 9 is new.
