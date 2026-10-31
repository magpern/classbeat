# ADR 0011: Tiers, quotas and the admin area (M5)

Status: Accepted

## Context

M5 is roadmap §9 (tiers Free, Instructor and Pro; entitlements from a subscription, an admin grant,
a code or a trial; billing prepared but off), §4's credit rules (a class credit **reserved** at
start, **committed** on success, **refunded** on system failure or early cancellation; a per-user
concurrent-job limit; a per-job cost ceiling), §7's M5 security work (atomic quota reservations, an
admin audit log) and the roadmap's "Admin area". The M5 row's "done when": **limits hold under
concurrency; refunds work.**

Relied on, unchanged:

- ADR 0002: owned tables under forced RLS, `user_session`, system tables, and the `classbeat_maint`
  role reserved for "audited admin and maintenance paths (M5)".
- ADR 0003: `users.role`, the admin's 12-hour TOTP window, step-up (a TOTP or recovery code within
  5 minutes, §6 and §7: "sensitive admin actions (from M5: grants, codes, flags, disabling users)"),
  invites (`invites`, single-use, SHA-256 of 16 random bytes), the rate-limit mechanism (reserve
  first, 429 with `Retry-After`), `require_admin` and `GET /api/admin/whoami`.
- ADR 0005: the job states, `jobs.credit_state` (`none`, `reserved`, `committed`, `released`), the
  **credit seam** `CreditLedger` (`reserve` in the create transaction, `allows` at approval,
  `commit`/`release` from **settle**, exactly once per job), `QuotaRefused`, the owner advisory lock
  around job creation, `CLASSBEAT_JOBS_PER_USER`, and the admin seam ("M5's admin page lists waiting
  approvals and uncertain operations through `classbeat_maint` with the audit log").
- ADR 0006 §5: the per-job cost ceiling per kind (`CLASSBEAT_AGENT_JOB_COST_MICROS`, $1.50, and
  `CLASSBEAT_SONG_JOB_COST_MICROS`, $3), enforced before every turn; `agent_runs` with
  `cost_micros` and `price_version` (ADR 0007 §10), the durable usage record.
- ADR 0007 §2 and §4: `JobKind.consumes_credit` (only the design kinds take a credit; revise, save
  and update do not) and the **revision seam** `RevisionPolicy.limit_for` with `revision_limit`
  (409 with limit and count).
- ADR 0008 §4: `design_with_songs` consumes the class credit; `revise_songs` does not;
  `CLASSBEAT_SONG_PICKING` is the feature switch.
- The M3c boundary agreed with ADR 0012 (M3c, in review in parallel): M3c owns
  `classbeat.models` (`model_routes` keyed by an opaque `route_key` and stage group (`design`, `songs`), `set_route` that
  refuses a model without a passing eval, `resolve_route` used by the worker). M5 owns what a
  route key means for a tier, choosing a job's route key at creation (into the `route_key` job-input field M3c adds, #215), and the admin screen that
  calls `set_route`. Threat-model rows: M3c T79 to T84, M5 from T85.

What exists today: every job runs on `UnmeteredLedger` (always allows, records nothing beyond
`jobs.credit_state`); the revision limit is one setting (25) for everyone; the job limit is one
setting (2); `UserContext.entitlements` is always empty; `AdminPage.tsx` shows only `whoami`;
invites are made only by the CLI.

## Decision

§1 is the data, §2 entitlements and codes, §3 quota accounting, §4 concurrency and cost, §5 the
admin database path, §6 the admin area, §7 flags and settings, §8 what stays off, §9 the API and
pages, §10 threat model and tests, §11 the owner's questions. The issue split is at the end.

### 1. Data (one migration, `0007_tiers`)

All in one migration so parallel M5 PRs never race on Alembic heads (M4's only migration, `0006`,
has merged). M3c adds migrations too (ADR 0012, #211 and #215), each "the next free revision when it
lands": `0007_tiers` is likewise **the next free revision**, and whichever of #207, #211 and #215
lands later re-chains its `down_revision` (and its file name) onto the head of `main`; the existing
single-head check stays. System tables (no `owner_id`, `SYSTEM_TABLES`) and owned tables (ADR 0002 §2:
`owner_id`, `UNIQUE (owner_id, id)`, owner-inclusive keys, `enable_owner_rls`) as marked.

| Table | Kind | Columns (beyond `id`, `created_at`) | Notes |
|---|---|---|---|
| `plans` | system | `id text` primary key (`free`, `instructor`, `pro`; check `^[a-z_]{1,30}$`), `name`, `rank int` (unique; higher wins), `classes_per_month int` (0 to 10 000), `revisions_per_class int` (0 to 200), `concurrent_jobs int` (1 to 16), `monthly_cost_micros bigint` (0 to 10^10), `route_key text` (default `'default'`, ADR 0012; migration 0014 sets it to the plan's own id, so a model route is per plan: `free`, `instructor`, `pro`; a plan with no route for a stage group uses ADR 0012's bootstrap choice, and a route on `default` covers no seeded plan; the column default stays `'default'`, so a later migration that adds a plan must set its `route_key` to the plan's id), `features text[]` (each in a code list, §7), `updated_at`, `updated_by uuid NULL` (→ `users`, set null) | Seeded with §11's defaults. No price column (billing is off, §8). `free` can't be deleted (no `DELETE` grant to anyone) |
| `entitlements` | owned (the holder) | `plan_id text NULL` → `plans`, `features text[]` (default `{}`), `source text` check `IN ('grant', 'code', 'trial', 'subscription')`, `starts_at`, `ends_at NULL`, `code_id uuid NULL` → `redeem_codes` (restrict, as `code_redemptions`), `granted_by uuid NULL` → `users` (set null), `note text NULL` (≤ 200, admin-only), `revoked_at NULL`, `revoked_by uuid NULL` | Check `plan_id IS NOT NULL OR cardinality(features) > 0`; `ends_at > starts_at`; `source <> 'code' OR code_id IS NOT NULL`. Partial unique index `(owner_id) WHERE source = 'trial'`: one trial per account, ever. A restrictive `INSERT` policy `WITH CHECK (source IN ('code', 'trial'))`: the app role can only add what redeem and trial add; grants come only through `classbeat_maint` (BYPASSRLS, §5). `subscription` has no writer until M7 |
| `redeem_codes` | system | `code_hash bytea` unique (SHA-256), `label text` (≤ 80), `plan_id NULL` → `plans`, `features text[]`, `grant_days int` (1 to 366), `max_redemptions int` (1 to 1000), `redeemed_count int` (check `0 <= redeemed_count <= max_redemptions`), `expires_at`, `disabled_at NULL`, `created_by uuid NULL` (set null) | Codes are 16 random bytes, Crockford base32, shown once (as invites) |
| `code_redemptions` | owned | `code_id` → `redeem_codes` (restrict), `entitlement_id` → `entitlements` (cascade, owner-inclusive) | `UNIQUE (owner_id, code_id)`: a user redeems a code once |
| `credit_ledger` | owned | `job_id uuid NULL` (→ `jobs`, owner-inclusive, `ON DELETE SET NULL (job_id)`), `period date` (first day of the UTC month), `plan_id text`, `state text` check `IN ('reserved', 'committed', 'released', 'refunded')`, `reason text NULL` (`failed`, `cancelled`, `expired`, `admin_refund`), `settled_at NULL`, `refunded_by uuid NULL` | `UNIQUE (owner_id, job_id)`; index `(owner_id, period)`. Restrictive policies: `INSERT` only `WITH CHECK (state = 'reserved' AND settled_at IS NULL AND refunded_by IS NULL)`, no `DELETE` (`USING (false)`; only the account's cascade removes rows), `UPDATE` only `USING (state = 'reserved') WITH CHECK (state IN ('committed', 'released'))`. A `BEFORE UPDATE` trigger allows, by `current_user`: for `classbeat_app`, exactly `reserved → committed | released`; for `classbeat_maint`, exactly `committed → refunded` (so the admin can never release a live reservation, which would make its later commit a no-op and the class free); for every role, the `job_id → NULL` of the cascade; no other column change. Any other role (the owner role included) is refused. The trigger function is `SECURITY INVOKER`: a `SECURITY DEFINER` function would make `current_user` its owner, so the role check would see the owner role and refuse every settle |
| `quota_periods` | owned | `period date` | `UNIQUE (owner_id, period)`; the row a reservation locks (§3); holds no count |
| `app_settings` | system | `key text` primary key (a code list, §7), `value jsonb` (≤ 2048 bytes, validated per key), `updated_at`, `updated_by uuid NULL` | Read per request; no cache to invalidate |
| `admin_audit` | system | `actor_id uuid NULL` (→ `users`, set null), `actor_email text` (at the time), `action text` (a code list), `target_user_id uuid NULL` (set null), `target_kind text NULL`, `target_id text NULL` (≤ 100), `params jsonb` (≤ 2048, per-action schema, never a secret, code or token), `outcome text` check `IN ('ok', 'refused', 'error')`, `reason text NULL` (an enum code), `request_id text`, `ip_hash bytea` (HMAC, as the rate limits) | Append-only: `REVOKE ALL` from `classbeat_app` (an exception in guard 2's allowlist), `classbeat_maint` has `INSERT, SELECT` only, and a trigger refuses `UPDATE` and `DELETE` except the foreign keys' `SET NULL` of `actor_id` and `target_user_id` |

The two guard triggers (`credit_ledger_guard`, `admin_audit_guard`) are the only user code besides
the queue functions. ADR 0005 section 3's amendment (2026-10-30, #207) admits them, pinned by
digest. `refunded_by` and `revoked_by` carry no foreign key, because a `SET NULL` from a deleted
admin would be a ledger change the trigger refuses. After an admin's account is deleted, those
columns keep a dangling admin id, which is an opaque UUID and no personal data.

**Note (2026-10-31, #271): the database enforces the app's terms too.** Migration
`0010_m5_hardening` makes three checks of `classbeat.plans` and the settle hold for any SQL run as
`classbeat_app`, not only for our code paths:
- `entitlements_app_sources` also requires `starts_at <= now()` and an `ends_at` that is set and at
  most `now() + 366 days`, and for a trial a plan only (no code, no features) ranked at most
  `instructor` (a missing plan refuses).
- `credit_ledger_guard`'s app settle requires `reason` NULL for `committed`, one of `failed`,
  `cancelled`, `expired` for `released`, and `settled_at = now()` (§3's settle statement);
  `admin_refund` is only the maint refund's, which must also set `refunded_by`.
- A third guard trigger, `redeem_codes_guard`, lets the app only add one use to a live code
  (`redeemed_count = old + 1`, at most `max_redemptions`, not disabled or expired, no other column);
  every other role must leave the count as it is. ADR 0005 section 3's amendment of 2026-10-31
  admits it.

Decided with it (Architect, 2026-10-31):
- The settle-reason check is defence in depth and is enough. That the reason matches how the job
  actually ended is application code's job (the settle reads the job's state): the app role can
  update `jobs` anyway, so a database check of the job's state would add nothing against SQL run
  as that role.
- **Accepted risk:** the database does not check a code entitlement's plan, features or days
  against its `redeem_codes` row. `classbeat.plans.redeem` enforces those terms; the database
  enforces only the source and the 366-day ceiling, so SQL injected as the app role could add a
  code entitlement of any plan or features for up to 366 days, naming any existing code.
- No role other than the app may change `redeemed_count`, the owner and the superuser included.
  A data migration that must correct counts disables the trigger inside that migration, as the
  owner (`ALTER TABLE public.redeem_codes DISABLE TRIGGER redeem_codes_guard`, then `ENABLE`, in
  the same transaction).

`docker/postgres/init/10-roles.sql`, when run again after the migrations, now restores the grants
of the table above after its blanket re-grant (before this note a re-run gave the app full DML on
every M5 table, `admin_audit` included); `tests/test_roles_rerun.py` checks it.

**The app role's grants on the new tables** (guard 2 checks them as an allowlist; the default
privileges of ADR 0002 §4 are revoked table by table where narrower):

| Table | `classbeat_app` |
|---|---|
| `plans`, `app_settings` | `SELECT` |
| `entitlements`, `code_redemptions` | `SELECT, INSERT` (no `UPDATE`: a bug can't extend `ends_at` or un-revoke) |
| `redeem_codes` | `SELECT`, `UPDATE (redeemed_count)` |
| `credit_ledger` | `SELECT, INSERT, UPDATE` (narrowed by the policies and the trigger above) |
| `quota_periods` | `SELECT, INSERT, UPDATE` (`UPDATE` is what `SELECT … FOR UPDATE` needs; no column is ever updated) |
| `admin_audit` | none |

**Jobs that are open when M5 deploys:** the migration backfills a `reserved` ledger row (the period
of the job's `created_at`, plan `free`) for every job with `credit_state = 'reserved'` that has not
ended (`queued`, `running` or `waiting_for_approval`, which can wait up to 7 days), so they count and
settle like any other. It is an `INSERT` only (the trigger is not involved) and states how it
reaches owned rows, as ADR 0002 §4 asks of data migrations: per owner with `app.user_id` set.

`invites` is **reused unchanged**: the admin area creates and lists invites with the CLI's code and
hashing (ADR 0003 §7); an invite still only lets someone sign up. Giving a tester a plan is a grant
after sign-up or a redeem code, so M5 does not touch the sign-up transaction (M1, security-critical)
and an invite never carries authority beyond an account.

Retention (guard 10, ADR 0007 §11): `entitlements`, `code_redemptions`, `credit_ledger` and
`quota_periods` go with the account; `admin_audit` keeps `actor_email` and a nulled target after an
account is deleted and is kept until the M6 review sets a period (§11, Q9). None holds
Spotify-derived data.

**Why** one ledger row per credit-consuming job instead of a counter: the ledger is the single
source of truth (a counter can drift from the jobs it counts), it survives deleting the class
(`job_id` set null), and it is what the admin refund and the user's "3 of 30" both read.

### 2. Plans, entitlements, codes, trial

**The effective plan** (`classbeat.plans.resolve.effective_plan(db, ctx) -> Plan`, read inside the
caller's transaction): among the user's entitlements that are active (`revoked_at IS NULL`,
`starts_at <= now()`, `ends_at IS NULL OR ends_at > now()`) and name a plan, the one with the
highest `rank`; `free` when none. **Effective features** = the plan's `features` plus every active
entitlement's `features`. Overlapping entitlements don't add up: a second 30-day code for the same
plan while the first runs gives no extra days (§11, Q6).

**Why** resolved per transaction and not put in `UserContext.entitlements`: a grant, revoke or
expiry takes effect on the next request without touching the two context issuers (`classbeat.auth`,
`classbeat.jobs`, both security-critical), and the reservation reads the plan under the same lock it
counts under. `UserContext.entitlements` stays empty; the roadmap §1 field is left for M7.

**Grant** (admin, step-up, §6): an `entitlements` row with `source = 'grant'`, a plan and/or
features, `starts_at = now()`, optional `ends_at`, a note. **Revoke** sets `revoked_at`/`revoked_by`
(rows are never deleted, so the history stays readable). Both through `classbeat_maint` with an
audit row.

**Redeem** (`POST /api/account/redeem {code}`, signed in, CSRF, rate limited by the ADR 0003 §9
mechanism: 10 per user and hour, 30 per IP and hour). One `user_session`: look up the hash `FOR
UPDATE`; `UPDATE redeem_codes SET redeemed_count = redeemed_count + 1 WHERE id = :id AND
redeemed_count < max_redemptions AND disabled_at IS NULL AND expires_at > now() RETURNING`; insert
the entitlement (`source = 'code'`, `ends_at = now() + grant_days`) and the `code_redemptions` row.
Unknown, expired, disabled and used-up codes all answer 400 `code_invalid` (no oracle of which);
`already_redeemed` only for the user's own earlier redemption (the unique index; the count rolls
back with it).

**Trial** (`POST /api/account/trial`, signed in, CSRF): when the `trial` setting (§7) has
`days > 0`, inserts `source = 'trial'` for its plan and days; the partial unique index makes a
second trial `trial_used` whatever the timing. **User-started, not at sign-up**, so M5 changes no
auth code and a tester on a grant never burns a trial by accident. Off by default (§11, Q3).

**Why** codes are a separate table from invites: invites are single-use sign-up tokens consumed in
the sign-up transaction; redeem codes are multi-use, carry a plan and days, and are redeemed by a
signed-in user. Merging them would put plan logic into the M1 sign-up path or make invites
multi-use.

### 3. Quota accounting (classes per month, revisions per class)

**Classes per month** are the credit-consuming jobs (`consumes_credit`: `design_draft`,
`design_with_songs`). `classbeat.quota.MeteredLedger` implements ADR 0005's `CreditLedger`:

- **`reserve(db, ctx, job_id)`**, in the job's create transaction (already under the owner advisory
  lock, but not relying on it): `period = date_trunc('month', now() AT TIME ZONE 'UTC')`;
  `INSERT INTO quota_periods … ON CONFLICT DO NOTHING`; `SELECT … FOR UPDATE` that row; **held** =
  ledger rows of the period with `state = 'committed'` or (`state = 'reserved'` and `job_id IS NOT
  NULL`); plan = `effective_plan`; refuse when `held >= plan.classes_per_month` (`QuotaRefused`
  reason `classes`) or when the period's cost (§4) is at `monthly_cost_micros` (`cost`); otherwise
  insert `reserved` with the period and plan. `QuotaRefused` gains `reason`, `limit`, `used`,
  `period_end` and `plan`; the jobs API answers 409 `quota` with them (the existing seam, now with a
  body), and nothing is created.
- **`allows`** (approval, ADR 0005 §2): the credit is already held, so it checks only the cost cap.
  A missing ledger row (a job the backfill did not reach) is allowed, as `commit` and `release` treat
  it as a no-op; a row in any state other than `reserved` refuses. A list approved after a downgrade
  still runs: its credit was taken before.
- **`commit` / `release`** from settle, in the same transaction as the `jobs.credit_state` update:
  `UPDATE credit_ledger SET state = …, reason = …, settled_at = now() WHERE owner_id = … AND job_id
  = … AND state = 'reserved'`. Zero rows (a job created before M5 under `UnmeteredLedger`) is a
  no-op, logged by count.
- **Refunds.** Everything that ends a job other than `completed` releases its credit: `failed`
  (any category), `cancelled` (at any time), `expired` (an unapproved list). This is ADR 0005 §5's
  settle unchanged, and it reads roadmap §4's "system failure or early cancellation" generously:
  a class that never reached a draft was not delivered. Abuse of that (cancel late, keep the spend)
  is bounded by the cost cap (§4), not by keeping the credit. An **admin refund** turns a
  `committed` row `refunded` (step-up, audited, §6), for a delivered class that was useless; the
  period's held count drops by one at once.
- **Deleting a class** does not give the credit back: the committed row stays and only `job_id`
  goes null. A `reserved` row whose job went with its class (deleted mid-run) stops counting, by
  the definition of held, because nothing was delivered; its cost still counts (§4).
- **The period** is the UTC calendar month of the reservation (as the usage view, ADR 0007 §10); a
  job reserved on the 31st and finished on the 1st counts in the month it started (§11, Q4).
- **A plan change** applies to the next reservation; nothing already held is taken back; a
  downgrade below `held` refuses new classes until the next period.

**Revisions per class:** `classbeat.quota.PlanRevisionPolicy` replaces `SettingRevisionPolicy` in
the existing seam: `min(plan.revisions_per_class, CLASSBEAT_REVISIONS_PER_CLASS)` (the setting stays
the storage bound). The count is the existing one, inside the revise kinds' `admit` under the owner
lock, of revisions that exist: a revise job that fails or is cancelled creates no revision, so it
uses nothing, and that is its refund. `revise_songs` counts as `revise_draft` does (ADR 0008 §6).

**Why** a lock row and a count instead of `SERIALIZABLE` or a check constraint: one short row lock
per user and month serialises exactly the reservations that compete, needs no retry loop, and
cannot be bypassed by a caller that forgot the advisory lock; a constraint can't express "count of
rows in a period below a limit that lives in another table".

### 4. Concurrency limits and cost ceilings

- **Concurrent jobs per user:** `plan.concurrent_jobs`, capped by `CLASSBEAT_JOBS_PER_USER` (which
  becomes the hard upper bound for every plan), counted as today (`queued` and `running`, under the
  owner advisory lock, at create and at approve). `jobs.user.create` and the approve path take a
  `JobLimitPolicy` (`limit_for(db, ctx) -> int`) instead of the integer, resolved inside the
  transaction. M4's `pool_busy` (one pick at a time) is unchanged and separate.
- **Per-job cost ceiling:** unchanged, per kind, from the settings (ADR 0006 §5); shown read-only
  in the admin settings. **Why** not per plan or admin-editable in M5: it is a safety bound on a
  single run that the live smoke runs calibrate; changing it is a reviewed setting change, and a
  plan's spend is governed by the monthly cap below.
- **Monthly cost cap per user:** `plan.monthly_cost_micros`, compared with the sum of the user's
  `agent_runs.cost_micros` whose `started_at` falls in the period (all kinds, revisions included,
  released jobs included). Checked at `reserve` and `allows`, and in the revise kinds' `admit`
  (`QuotaRefused` reason `cost`, 409 `quota`). A run already going finishes within its per-job
  ceiling. It is the backstop that makes generous refunds safe.
- **Environment upper bounds for the money limits** (stricter-wins, as §7):
  `CLASSBEAT_CLASSES_PER_MONTH_MAX` (default 200), `CLASSBEAT_MONTHLY_COST_MICROS_MAX` (default
  300 000 000, $300) and `CLASSBEAT_TRIAL_DAYS_MAX` (default 30). A stored `trial.days` above `CLASSBEAT_TRIAL_DAYS_MAX` is clamped when used. The effective value of a plan's
  `classes_per_month` and `monthly_cost_micros` is the smaller of the row and the bound, the admin
  can't store more than the bound (422), and a trial's plan can't rank above `instructor`. The
  backstop of T86 therefore has a ceiling the admin area can't raise.
- **Priority queue (Pro):** not in M5 (§8).

### 5. The admin database path (`classbeat_maint`)

Every admin read and write goes through **`classbeat.admin.db.admin_session(actor, action, *,
write=False, target=…, params=…)`**, the only code that may build a session on the maint engine
(an AST guard, as `user_session`'s).

- **The role** (`docker/postgres/maint/30-maint.sql`, run by the `db-functions` step as the
  superuser after the migrations, idempotent like `10-roles.sql`): `classbeat_maint LOGIN
  NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT`, member of no role, password from the new
  secret `database_maint_password`, mounted on **`db-functions` and `web` only** (never the worker,
  migrate or maint containers). Role defaults: the app role's three timeouts plus
  `default_transaction_read_only = on`.
- **Column privileges, as an allowlist** (guard 11, a new test, in the style of guard 2): `SELECT`
  only on the columns the admin area shows, never on a secret-bearing column. The test keeps a
  `SECRET_COLUMNS` registry (connection ciphertexts, nonces and wrapped keys, `public`, every
  `*_hash` and `password_hash`, TOTP secrets and recovery codes, OAuth and Spotify flow state, PKCE
  verifiers, nonces, job `input`, revision `content`, `classes.brief`, messages, `external_id`,
  `result`, `request_context`) and fails when maint can read any of them or when a new column of
  those tables is readable without being listed. It also fails when code under `classbeat.admin` calls `NOTIFY`, `pg_notify` or an advisory-lock function (a read-only transaction still allows them; see the audit bullet below). Writes: `UPDATE (disabled_at)` and `DELETE` on
  `users`, `DELETE` on `sessions`, `INSERT` and `UPDATE (revoked_at, revoked_by)` on
  `entitlements`, `UPDATE (state, reason, settled_at, refunded_by)` on `credit_ledger`, `INSERT` and
  `UPDATE (expires_at)` on `invites` (disabling an invite expires it, so the M1 consume path is
  unchanged), `INSERT` and `UPDATE (disabled_at)` on `redeem_codes`, `INSERT, UPDATE` on `app_settings`,
  `UPDATE` of the limit columns on `plans`, `INSERT, SELECT` on `admin_audit`; for AD5, on M3c's
  tables (ADR 0012): `SELECT` on `model_eval_results` and `SELECT, INSERT, UPDATE, DELETE` on
  `model_routes` (added to `30-maint.sql` by AD5, after #211). Nothing on the queue functions or
  `job_events`: the admin's job counts come from `jobs.state` through maint's column `SELECT`, not
  from `queue_stats()` (whose grants `20-queue.sql` resets on every run); no `UPDATE` on `jobs` or
  `external_operations`.
- **Audit on every use, and reads stay read-only.** Every transaction on the maint engine starts
  with `SET TRANSACTION READ WRITE` (allowed only before the first query; the role default is read
  only) and inserts its `admin_audit` row (`outcome = 'ok'`) as its first statement. Then:
  - a **read** (`write=False`) runs `SET TRANSACTION READ ONLY` before yielding. Postgres allows
    switching a transaction to read-only after a write and refuses the switch back, so after the
    audit insert nothing the read path does can write, and the audit row commits with the read.
    Checked on `postgres:17.11` as a role with `default_transaction_read_only = on`: after
    `SET TRANSACTION READ WRITE; INSERT …; SET TRANSACTION READ ONLY;` an `INSERT` fails with
    `cannot execute INSERT in a read-only transaction`, both `SET TRANSACTION READ WRITE` and
    `SET transaction_read_only = off` fail with `transaction read-write mode must be set before any
    query`, and `COMMIT` keeps the audit row.
  - a **write** (`write=True`, which the route passes only after the step-up check) yields with the
    transaction still read-write; the audit row commits or rolls back with the action, so the log
    never shows a write that did not happen.

  A read-only transaction still allows `NOTIFY`/`pg_notify` and advisory locks, so admin code never
  calls them (guard 11 checks it).

  A refused or failing action (either kind) writes an `outcome = 'refused' | 'error'` row in its
  own short transaction (the same `READ WRITE`, insert, commit). Reads are audited once per request,
  with the filter in `params`.
- **Pooled connections:** a maint connection is reset with `DISCARD ALL` when it returns to the pool,
  and `default_transaction_read_only` is re-checked on checkout (a session-level `SET
  default_transaction_read_only = off` is allowed inside a read-only transaction and outlives it).
  The role check and the pre-ping run outside `admin_session`'s transaction.
- **Startup:** web checks the maint role's attributes, memberships and role settings at startup and
  on every new maint connection (as ADR 0002 §4 does for the app role), refuses to start when the
  maint URL is set and the check fails, and starts without the admin area (503 `admin_unavailable`)
  when no maint secret is mounted. The maint pool is small (2, no overflow) and lazy.

**Why** this and not narrow `SECURITY DEFINER` functions (the queue's pattern): the admin area is
broad (lists with filters, a dozen actions) and will keep changing; one function per screen, each
with a digest, would be a large security surface of its own. The roadmap and ADR 0002 already chose
a distinct audited login role; column privileges give the "never tokens" rule a database layer, the
read path is made unable to write by the explicit `SET TRANSACTION READ ONLY` after the audit insert (the
role's read-only default only covers transactions started outside `admin_session`), and the worker never holds the credential.
**Why** not the app role with a cross-user bypass: the app role must never see across users (guard
2, T26).

### 6. The admin area

All under `/api/admin/*`: `require_admin` (role `admin`, TOTP within 12 hours, ADR 0003 §7) for
every route; **step-up** (TOTP or recovery code within 5 minutes) for every write; CSRF on writes;
`Cache-Control: no-store`; a rate limit (60 requests per admin per minute); `422` for anything a
schema rejects. Pages under `/admin` in a lazy chunk; the server is the authority. The router shell
(`classbeat.admin.routes`, its dependencies and the router-enumerating guard test) is built with the
maint path in AR1, so the admin feature modules only add routes to it.

**Refusals are audited without a flood.** Only callers whose `users.role` is `admin` get a
`refused` audit row (an admin hitting `mfa_required` or `stepup_required`, or a refused admin
action); a non-admin's 403 writes no audit row and opens no maint connection. Refusal rows are
limited to 20 per admin and 100 in total per minute; beyond that they are counted in a log line, so
neither the table nor the maint pool (2 connections) can be exhausted.

**Admin targets.** Disable and delete refuse a target whose role is `admin`, the acting admin
included (409 `target_is_admin`): demoting an admin stays the CLI's (ADR 0003 §7), and no admin can
lock themselves or another admin out from the browser. A delete records the target's id in
`target_id` (text), because the foreign key nulls `target_user_id` in the same transaction.

**Reaching the code prompt (#330).** The session's `role` is `user` until the TOTP check passes
(and again after 12 hours), so the page cannot tell such an admin from a user. `GET
/api/auth/session` therefore carries `session.admin_pending_mfa`: true only when the caller's own
stored role is `admin` and the check is missing or older than 12 hours, false for every other
account (a real non-admin sees `false`, never anything about admin routes). It reads the session
row already loaded for the request, writes and audits nothing, and decides nothing: `/api/admin/*`
keeps its own `403 forbidden` / `403 mfa_required`. The page fetches the lazy admin chunk for
`role == admin` or `admin_pending_mfa`, and `AdminLayout` shows the code prompt (or the first-time
enrolment) from the `mfa_required` answer of `whoami`; after the check the session is fetched
again and the area opens.

| Area | Reads | Writes (step-up) |
|---|---|---|
| Users | list (email, created, role, disabled, effective plan and its source and end, classes held this period of the limit, cost this period) with search by email prefix and paging; detail adds entitlements (with notes), the credit ledger of the last three periods, and **connection status only**: MOWL (status, connected, last used), Spotify (status, so whether a Client ID is set, and the last error category, ADR 0009 §8), Swedebeat (status). Never a token, ciphertext, Client ID value, playlist id, brief or class content | grant and revoke entitlements; disable and enable (disable revokes all the user's sessions, ADR 0003 §3; a disabled user's jobs end as T53); delete (the ADR 0003 account deletion for the target, with the target's email typed to confirm); refund a committed credit. Never role changes: granting and revoking admin stay CLI-only (ADR 0003 §7) |
| Codes | invites (created by, used, expiry; never the code) and redeem codes (label, plan, days, uses, expiry, disabled) | create an invite or a redeem code (the code shown once), disable either |
| Usage and cost | per period: classes, revisions, saves, runs, tokens and `cost_micros` by plan, kind and model, from `agent_runs` and `credit_ledger`; top users by cost; the `price_version` in use and the price table (read-only, `classbeat.agent.prices`) | none |
| Jobs | counts per state (from `jobs.state`), waiting approvals (owner email, kind, waiting since, expires), recent failures by category, uncertain external operations (owner email, kind, since, the job's state); no job input or operation content | none in M5: an uncertain operation stays the user's to resolve (ADR 0005 §7); an admin "resolve" could cause a duplicate write in the user's account |
| Settings and flags | every §7 key with its effective value and where it comes from (environment or database); plans with their limits; the read-only settings (per-job ceilings, billing, Swedebeat) | edit §7's keys and the plans' limits (within §4's environment bounds); model routes per plan through ADR 0012's `set_route` (refused unless the eval passed; issue AD5). Provider key files live on the worker only, so the web-side `set_route` checks the catalog, the eval result and the data policy, and the key file is checked by the worker's `resolve_route` re-check (ADR 0012 §5, agreed with M3c): a route to a provider without a key fails its job as `model_config` in `resolve_route` (ADR 0012), never silently |
| Audit log | filter by actor, action, target, period; newest first | none |

**MFA and step-up** reuse ADR 0003 unchanged (`require_admin`, `require_stepup` in `classbeat.auth.deps`): `POST /api/auth/mfa/stepup` with a TOTP code sets
`stepup_at`; an admin write without a fresh step-up is 403 `stepup_required` and the page asks for
a code and repeats the request once. **Why** step-up for every write and not only some: the admin
writes are few, and a stolen admin session within its 12-hour window should not be able to change
anything without the second factor.

### 7. Flags and settings

`classbeat.flags` reads `app_settings` per request; the environment sets the defaults and a
**stricter-wins** rule, so the database can tighten what the environment allows but never loosen
it:

| Key | Values | Rule |
|---|---|---|
| `signup_mode` | `invite`, `closed` | effective = `closed` if either says `closed`; `open` only from `CLASSBEAT_SIGNUP_MODE` (never from the admin) |
| `song_picking` | `off`, `granted`, `on` | effective `off` while `CLASSBEAT_SONG_PICKING` is off (the kill switch); else the stored value, default `granted`: only users whose effective features include `song_picking` (Amendment 1: "the owner switches features on for the testers") |
| `trial` | `{plan, days}` | `days = 0` (default) is off |
| `banner` | `{text, level}` | text ≤ 280 characters, `info` or `warning`, rendered as text; served to everyone by `GET /api/settings/public` (with the effective sign-up mode) |

**Amendment 2026-10-31 (FL1, Architect):** `closed` is the emergency brake and also stops confirmation. While the effective sign-up mode is `closed`, confirming a pending sign-up, password or provider, answers 403 `signups_closed`; the pending row and its invite stay, so it can still be confirmed if sign-ups reopen before it expires (24 hours). `invite` and `open` do not affect confirmation: the row was admitted when it was made. A provider flow or hand-off that carried an invite consumes it when the account is created, whatever the mode is by then, so the invite cannot be reused. ADR 0003 section 5's confirmation steps are read with this addition.

**Amendment 2026-10-31 (AD4, proposed in the PR, confirmed by the Architect on 2026-10-31):** `classbeat_maint` has no `DELETE` on `app_settings` (§5), so the admin clears a flag by storing the JSON `null`; every reader takes a `null` value as no row, so the environment's value applies. Reads of a stored value that does not validate fall back to the key's stricter default; writes of one are refused with 422 and an audit row. A plan limit is refused above the environment's bound for each of the four limits, `CLASSBEAT_JOBS_PER_USER` and `CLASSBEAT_REVISIONS_PER_CLASS` included.

**Amendment 2026-10-31 (#319, #227):** the banner's text is plain text: every character of Unicode categories `Cc`, `Cf`, `Zl` and `Zp` is refused when it is written (422 `invalid_value`) and makes a stored banner read as none, so an emoji sequence joined by U+200D (ZWJ, a `Cf` character, for example a family or a flag-and-symbol emoji) is refused by design, while single emoji, accented letters and combining marks are kept.

Feature codes in M5: `song_picking`. `billing` is shown, read-only and off; the admin can't turn it on
(§8). `swedebeat_enabled` became a real flag on 2026-10-31 (ADR 0015 §6): it is not a feature code, it is
switched for everyone. Plan limits are edited on `plans`, not in `app_settings`.

### 8. What stays off

- **Billing:** `CLASSBEAT_BILLING_ENABLED` (default `false`) is shown; there is no Paddle code, no
  price shown to users, no `subscription` writer and no webhook. M7.
- **Swedebeat:** *superseded 2026-10-31 (S1, #362, [ADR 0015](0015-swedebeat-connection.md) §6, roadmap
  Amendment 9):* `swedebeat_enabled` is a real flag in §7's table: stored `off` or `on` (absent is `on`), only
  under `CLASSBEAT_SWEDEBEAT_ENABLED` (default `false`, the kill switch); it is no longer read-only.
- **Priority queue for Pro:** it changes the queue's `SECURITY DEFINER` claim and its digests;
  with a beta of a few testers it buys nothing; deferred to M7 (§11, Q8).
- **Feature gates by tier** (races, games, FTP tests, history, multi-week series): M5 stores
  `features` but gates only `song_picking`; the other gates come with the features or with M7
  (§11, Q7).
- **Editable price table:** read-only in the admin; a price change stays a reviewed code change
  with a `PRICES_VERSION` bump (ADR 0007 §10), so stored costs keep their meaning.

### 9. API and pages

| Endpoint | What |
|---|---|
| `GET /api/account/plan` | effective plan and source, its end, limits, classes held and the period's end, cost this period as a share of the cap (no dollar figure), effective features, whether a trial is available |
| `POST /api/account/redeem`, `POST /api/account/trial` | §2 |
| `GET /api/settings/public` | banner and sign-up mode (no auth) |
| `POST /api/jobs`, approve, revise kinds | 409 `{reason: quota, quota: classes \| cost, limit, used, period_end, plan}` (`period_end` the ISO date the next period starts; `limit` and `used` for `classes` only, so a cost refusal shows no money, as the account page; QT1, as FE1 reads it); 429 `{reason: busy, limit}` with the plan's limit; `revision_limit` unchanged |
| `/api/admin/*` | §6 |

Account page "Plan and usage" (roadmap §8): plan, "3 of 30 classes this month", redeem a code,
start a trial; the new-class and change pages word `quota` in plain sentences ("You have used your
3 classes for October. More from 1 November."). The banner on every page. Admin pages: §6.

### 10. Threat model and tests

New threats T85 to T96 in `docs/security/threat-model.md`. Tests (fakes only, no network):

- **Concurrency:** N = 20 parallel reservations for one user on separate connections with a limit
  of 3 give exactly 3 reserved; the same across a period boundary; parallel approvals and creates
  against `concurrent_jobs`; parallel redeems of a code with one use left give one entitlement;
  parallel trials give one.
- **Refunds:** every terminal state settles once (committed or released, the ledger and
  `credit_state` together), a released credit frees a slot at once, deleting a class keeps a
  committed credit and frees a reserved one, an admin refund frees a slot and is audited, a
  pre-M5 job settles without a ledger row.
- **Policies:** the trigger and restrictive policies refuse every other ledger change for the app
  role and maint; the app role can't insert a `grant` entitlement or touch `admin_audit`; guard 11.
- **Admin:** every `/api/admin/*` route is 403 for a non-admin, `mfa_required` without TOTP, and
  every write is `stepup_required` without step-up (a test enumerates the router, so a new route
  without the dependency fails); every call writes exactly one audit row, admin refusals included
  and non-admin 403s none; inside a read `admin_session`, after its audit insert, `SHOW
  transaction_read_only` is `on`, an `INSERT`/`UPDATE` fails and `SET TRANSACTION READ WRITE` fails,
  while the audit row is committed; a failing read writes one `error` audit row and no `ok` row; a
  pooled maint connection is reset (`DISCARD ALL`, `default_transaction_read_only` back on) after a
  session-level `SET default_transaction_read_only = off`; maint cannot release a `reserved` ledger row; disable and
  delete refuse an admin target; no
  admin response contains a planted token, hash, Client ID, brief or job input (planted-value test);
  log redaction of codes.
- **Flags:** stricter-wins for each key; `song_picking` `granted` admits only users with the
  feature.
- **E2E (done when):** limits under concurrency through the API from parallel browser contexts;
  failure, cancel and expiry refunds; redeem raising the limit; an admin with TOTP granting a plan,
  refunding and seeing it in the audit log.

### 11. Questions for the owner (defaults let development proceed on fakes)

| # | Question | Default until answered |
|---|---|---|
| Q1 | The limits per tier | Free: 3 classes a month, 3 revisions each, 1 job at a time, $10 a month cost cap. Instructor: 30, 10, 2, $60. Pro: 100, 25, 3, $200 (roadmap §9's class and revision figures; Pro revisions, concurrency and caps are the Architect's) |
| Q2 | Which plan do the beta testers get | Every account starts on Free; the owner grants Instructor (no end date) and the `song_picking` feature to each tester in the admin |
| Q3 | Trial: on, which plan, how long | Off; when switched on, 14 days of Instructor, once per account, started by the user |
| Q4 | Quota period | The UTC calendar month |
| Q5 | Refund on cancel | Every non-completed job releases its credit, at any time; the cost cap is the backstop |
| Q6 | Do overlapping codes add up | No; the highest plan among active entitlements applies |
| Q7 | Feature gates for Free (races, games, FTP tests, history) | None in the beta; only numeric limits |
| Q8 | Priority queue for Pro | Deferred to M7 |
| Q9 | How long the admin audit log is kept | Until the M6 review sets a period |
| Q10 | Prices (€9 / €19 drafts) | Not shown anywhere while billing is off; decided before M7 from measured cost |

## Alternatives

- **A counter per user and month** instead of ledger rows: atomic with a conditional `UPDATE`, but
  a second source of truth that drifts on a missed release and can't show which job held what.
- **Counting `jobs` rows:** a deleted class cascades its jobs, so deleting would refund.
- **Plan in `UserContext.entitlements`:** stale until the next sign-in or job claim, and it touches
  both context issuers.
- **`SECURITY DEFINER` functions for the admin area:** §5.
- **Admin through the app role in `user_session` of the target:** that impersonates the user and
  leaves no audit; rejected.
- **Plans on invites:** puts plan logic into the M1 sign-up transaction; a grant after sign-up or a
  redeem code does the same with no auth change.
- **Keeping the credit on user cancel:** strict, but users would be charged for runs that delivered
  nothing; the cost cap bounds the abuse instead.
- **Per-plan, admin-editable per-job ceilings:** §4.

## Consequences

- `UnmeteredLedger` stays only for tests; web and worker wire `MeteredLedger`,
  `PlanRevisionPolicy` and `PlanJobLimitPolicy`.
- `QuotaRefused` and the `quota` body grow fields; the frontend words them.
- A new secret `database_maint_password` (`scripts/worktree.sh init` creates it), a new SQL step in
  `db-functions`, a new guard (11) and an exception in guard 2 for `admin_audit`.
- `CLASSBEAT_JOBS_PER_USER` becomes a cap over the plans; `CLASSBEAT_SONG_PICKING` becomes a kill
  switch under the database flag.
- The admin can read emails, plans, usage and connection status of every user, and nothing of their
  classes or credentials.
- Roadmap Amendment 7 records what M5 defers or reads differently (price table, priority queue,
  plans on invites, `UserContext.entitlements`).

## M5 issue breakdown

One issue per PR, milestone "M5 Tiers and admin". "Opus" = Opus Developer (quota, entitlements,
roles, admin authorization, auth); the Reviewer is a fresh context, Opus unless marked. No issue is
startable before this ADR is accepted. Fakes only.

| # | Issue | Owner | Reviewer | Depends on |
|---|---|---|---|---|
| DB1 (#207) | Migration `0007_tiers` (§1), policies and triggers, seeds, `classbeat.plans.resolve`, retention entries, guard updates | Opus | Opus | none |
| AR1 (#210) | `classbeat_maint` role, column grants, `admin_session` with audit and the read-only switch, startup checks, guard 11, secret and compose, the admin router shell and its guard | Opus | Opus | DB1 |
| PL1 (#212) | Redeem, trial, `GET /api/account/plan` | Opus | Opus | DB1 |
| QT1 (#214) | `MeteredLedger`, `PlanRevisionPolicy`, `PlanJobLimitPolicy`, cost cap, env bounds, `quota` bodies, wiring | Opus | Opus | DB1, #180, #181, #182 |
| FL1 (#216) | `classbeat.flags`, stricter-wins, sign-up mode and song-picking wiring, public settings | Opus | Opus | DB1, QT1 (both edit the M4 song kinds' `admit`), #180, #182 |
| AD1 (#218) | Admin users: list, detail, grants, disable, delete (admin targets refused) | Opus | Opus | AR1 |
| AD2 (#220) | Admin codes: invites and redeem codes | Opus | Opus | AR1, PL1 |
| AD3 (#221) | Admin usage, cost and jobs views; admin refund | Opus | Opus | AR1, QT1 |
| AD4 (#222) | Admin settings, flags, plans' limits, audit-log view | Opus | Opus | AR1, FL1 |
| AD5 (#223) | Model routes per plan (ADR 0012's `set_route`) and the route key at job creation | Opus | Opus | AR1, QT1, #211, #215 (M3c) |
| FE1 (#224) | Account plan and usage, redeem, trial, quota sentences, banner | Sonnet | Sonnet | PL1, QT1, FL1, #184 |
| FE2 (#225) | Admin pages: shell and step-up, users, codes | Sonnet | Sonnet | AD1, AD2 |
| FE3 (#226) | Admin pages: usage, jobs, settings, audit | Sonnet | Sonnet | AD3, AD4, FE2 |
| SEC1 (#227) | M5 security suite (§10) | Sonnet | Opus | PL1, QT1, FL1, AD1 to AD4 |
| T1 (#228) | E2E done-when, status, AGENTS.md notes | Sonnet | Sonnet | FE1, FE3, SEC1, #186 |

Order: DB1; then AR1, PL1 and QT1 in parallel (QT1 once #180 to #182 merged); FL1 after QT1;
AD1 to AD3 in parallel, AD4 and FE1 after FL1; FE2, AD5 and SEC1; FE3; T1 last. M5 is done technically when SEC1
and T1 are green.
