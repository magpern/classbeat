# ADR 0031: Plans and billing with a dummy provider (M7a)

Status: Accepted (owner go for M7a, 2026-11-11; roadmap Amendment 20). M7b is not covered and not
authorized.

## Context

The owner said "go, implement M7" on 2026-11-11 for **M7a only**: three plans, quality levels,
limits with upgrade prompts, a pricing page and plan menu, a dummy billing provider, the
subscription writer, upgrade, downgrade and proration rules, tester access and admins on the top
plan. Real Paddle, public sign-up, legal pages, the commercial song-picking route and provider
agreements are **M7b** and keep roadmap §9's gate (A1 to A5 for every subscriber, agreements
signed). Plan: [docs/plans/commercial.md](../plans/commercial.md) (revision 2).

What M5 and later built, and this ADR relies on (checked on `main` at 955ff5b):

- `plans` (ADR 0011 §1, migrations `0007_tiers`, `0026_plan_gates`, `0042_plan_save_caps`): ids
  `free`, `instructor`, `pro`; `classes_per_month`, `revisions_per_class`, `concurrent_jobs`,
  `monthly_cost_micros`, `max_class_minutes`, `games_allowed`, `races_allowed`,
  `ftp_tests_allowed`, prices `monthly_price_cents`, `intro_price_cents`, `intro_price_until`,
  `currency` (all NULL today), `route_key`, `features`, and the Save and Update caps
  `saves_per_hour`, `saves_per_day` (`0042`, NULL = the setting; unchanged here). Seeded today: classes 3 / 30 / 100,
  revisions 3 / 10 / 25, jobs 1 / 2 / 3, minutes 30 / 60 / 90, games off on Free only, races and
  FTP tests on everywhere.
- `classbeat.plans.resolve.effective_plan`: the highest-ranked plan of the user's active
  entitlements, `free` when none, read in the caller's transaction. It does not look at
  `users.role`; an admin today is on whatever their entitlements give.
- Enforcement points: `classbeat.quota.route.with_plan` (the four model kinds' `admit`: length,
  races, FTP tests on the brief, 409 `quota` `length` or `feature`; then `route_key` and
  `plan_gates` stored as server fields), `with_plan_run` (re-reads the current plan in the worker,
  but only for an input stored without `plan_gates`, and fails closed then),
  the stages' `not_allowed` tool error for a draft with a game, an FTP test or a length over the
  cap plus 5 %, `classbeat.quota.gates.check_stored_draft` (Make current, Save, Update,
  duplicate), `MeteredLedger.reserve` / `take_class` (classes per UTC calendar month, cost cap),
  `PlanRevisionPolicy` (revisions, capped by `CLASSBEAT_REVISIONS_PER_CLASS`, default 25) and
  `JobLimitPolicy` (jobs, capped by `CLASSBEAT_JOBS_PER_USER`, **default 2**).
  `classbeat.design.interpret_policy.held` holds an over-plan request to the cap and records it
  "Not as asked" with `over_plan_length`, `plan_no_games`, `plan_no_races` or
  `plan_no_ftp_tests`.
- Model routes (ADR 0012 §5, migration `0014_plan_route_keys`): `plans.route_key` is the plan id,
  `model_routes` is keyed by `(route_key, stage_group)`, `set_route` refuses a model without a
  passing eval result, a plan with no route uses the bootstrap model, and `agent_runs` records
  the `route_key` and `eval_result_id` the stage ran on (`classbeat.agent.records`).
- **Song picking is not a plan feature today.** `plans.features` is empty on every seeded row and
  `classbeat_maint` has no `UPDATE` on `plans.features` (`docker/postgres/maint/30-maint.sql`), so
  no plan can carry `song_picking`. The gate is `flags.song_picking_admits`: the
  `CLASSBEAT_SONG_PICKING` kill switch, then the stored flag `off` / `granted` / `on`; `granted`
  admits users whose *entitlements* carry the `song_picking` feature (the per-tester switch of
  Amendment 1 and ADR 0010).
- Entitlements: the app role may insert only `source IN ('code', 'trial')` (restrictive policy,
  tightened by `0010_m5_hardening`) and has no `UPDATE`; `classbeat_maint` (BYPASSRLS, mounted on
  `web` and `db-functions` only) has `INSERT` and `UPDATE (revoked_at, revoked_by)`. The CHECK
  `features <@ '{song_picking}'` is on `plans`, `redeem_codes` and `entitlements`. `subscription`
  has no writer.
- Prices: `0030_plan_price_privilege` gives `classbeat_app` no `SELECT` on the four price columns;
  only `classbeat_maint` reads them (`classbeat.admin.plans`). Guard 2
  (`backend/tests/role_guard.py`) fails if the app can read one.
- Maint sessions: only `classbeat.admin.db` (and `config.py`, which defines the settings) may name
  the maint engine or factory (`backend/tests/test_maint_privileges.py`, `MAINT_NAMES`,
  `MAINT_MODULES`); every maint transaction starts with an `admin_audit` row; `Actor` is built only
  by the admin gate (`classbeat.admin.routes`); column privileges are guard 11's allowlist. The
  maint pool is 2 connections with no overflow.
- Account tables (`users` and the rest of `classbeat.auth.tables`) are queried only inside
  `classbeat.auth` (guard 8, `backend/tests/auth_tables_scan.py`); other packages call readers
  there, as `classbeat.auth.owners` (jobs, mail) and `classbeat.auth.admin_lookup` (admin) do.
- Export and retention: the export reads the user's owned tables in their own `user_session` as
  the app role (`classbeat.account.export`, the `Section`s and `NOT_EXPORTED` of
  `classbeat.account.registry`); `admin_audit` is not exported, and `classbeat.account.coverage`
  `RETAINED` keeps its `actor_email` after a deletion because only operators are actors.
- No billing code exists: the admin settings show `billing_enabled=False` as a constant
  (`classbeat.admin.settings`); there is no `CLASSBEAT_BILLING_ENABLED` setting.

## Decision

### 1. Plans and where each limit is enforced

Display names change; ids, route keys, eval results and entitlements keep `free`, `instructor`,
`pro` (a rename of ids would touch every one of them for no user-visible gain). Numbers are the
owner's placeholders (`x = 3`, ratio 1 : 3 : 10), stored per row and admin-edited per row as today;
the ratio is not enforced.

| | Free | Individual | Professional | Enforced by (unchanged code) |
|---|---|---|---|---|
| id | `free` | `instructor` | `pro` | |
| Classes per month | 3 | 9 | 30 | `MeteredLedger.reserve`, `take_class` |
| Longest class (min) | 30 | 75 | 180 (= the global `CLASSBEAT_CLASS_MINUTES_MAX`) | `with_plan`, stages, `check_stored_draft`, `held` |
| Games | no | yes | yes | stages' `not_allowed`, `check_stored_draft`, `held` |
| FTP tests | no | yes | yes | `with_plan`, stages, `check_stored_draft`, `held` |
| Races (15 to 35 s bursts, ADR 0029) | yes | yes | yes | `with_plan` (brief count) |
| Revisions per class | 3 | 10 | 20 | `PlanRevisionPolicy` |
| Jobs at once | 1 | 2 | 3 | `JobLimitPolicy` |
| Quality | Standard | Better | Best | the plan's route key (§7) |
| Cost cap per month | unchanged (10 / 60 / 200 USD) | | | `reserve`, `allows`, revise `admit` |

A race is a short hard burst, not a MOWL game, so it stays on every plan (owner, 2026-11-11).
Song picking, the Spotify and Swedebeat connections, running classes, history, export and delete
are the same on every plan.

**M7a-2 changes, and only these:** one data migration sets the names, the numbers and the gates
above on the three rows (FTP tests off on Free; Free's games are already off); the default of
`CLASSBEAT_JOBS_PER_USER` goes from 2 to 3, so Professional's 3 is not clamped (it stays the hard
upper bound of every plan). No enforcement code changes: every gate above already reads the plan
row. Users who hold an Instructor grant today get Individual's numbers (fewer classes, longer
classes); that is the owner's decision.

**Song picking on every plan:** it is already plan-independent (Context), and stays so. No plan
row carries `song_picking`; the flag and the per-tester feature remain the provider-permission
switch of ADR 0010 for the beta, not a product tier. M7a-2 adds a test that no seeded plan's
`features` contains `song_picking` and that `effective_plan`'s id never changes what
`song_picking_admits` answers. The class allowance is the only meter. M7b decides song picking for
the public with the commercial route.

**Imported MOWL workouts (future, not built):** an import passes the same gates as a generated
class (`check_stored_draft` and the brief checks) against the user's current plan and is refused
before anything is stored (409 `quota`, with the upgrade prompt), never trimmed.

### 2. Admins are Professional

`effective_plan` returns the highest-ranked plan (`pro` today; the admin cannot add plans, there is
no `INSERT` grant) when the user's **stored** `users.role` is `admin`. Guard 8 stays as it is: the
role is read by a new reader in `classbeat.auth.owners`, `stored_admin(db, user_id) -> bool`
(`SELECT role = 'admin' FROM users WHERE id = :id AND disabled_at IS NULL`; **no row, a disabled
or missing user, returns `False`**, never an error and never `True`), which
`effective_plan` calls with the caller's session, in the caller's transaction, before its plan
query. No entitlement row, nothing to expire; a demoted admin is back on their entitlements at the
next plan read. The stored role and not the session's MFA-checked role: the plan only sets usage
limits, it grants no admin authority, and the worker, which reads the plan in `with_plan_run` for
a gate-less input, has no session. `users` is a system table without row security, already read
by the app role through `classbeat.auth.owners`. `GET /api/account/plan` reports the source as `admin` (a response value, not an
entitlement `source`). Effective features are not changed: an admin gets `song_picking` or
`billing_test` only from the flag or an entitlement, as anyone.

### 3. The subscription entitlement writer

One module, `classbeat.billing.writer.apply(event)`, is the only code that creates or ends a
`subscription` entitlement, for Dummy now and Paddle in M7b.

- **Where it runs:** in `web`, on the maint engine. The writer's functions take the session they
  are given, and there are exactly two callers:
  - the user's own checkout, change and price reads: a second session builder next to
    `admin_session` in `classbeat.admin.db`, `billing_session(buyer, action, write)`. Its first
    statement inserts a **`billing_log`** row, not an `admin_audit` row; a read then switches to
    `READ ONLY`, as `admin_session` does. `buyer` is a `Buyer` (user id, request id), built only by
    the billing routes' dependency (`classbeat.billing.routes`), never an `Actor`: `Actor` stays
    built only by the admin gate, and `admin_audit` keeps only operators as actors, so
    `classbeat.account.coverage`'s `RETAINED` reason for `actor_email` stays true;
  - the admin's fast-forward (§6): the ordinary `admin_session(write=True)` with an admin `Actor`
    and an `admin_audit` row (action `billing.fast_forward`), passed to the same writer functions.

  A new AST guard allows `billing_session` to be called only from `classbeat.billing.routes`, and
  the writer to be imported only by `classbeat.billing.routes` and `classbeat.admin.billing`.
  `billing_session` lives in `admin/db.py`, so `MAINT_MODULES` stays `config.py` and `admin/db.py`.
  `admin/db.py` is 404 lines, so M7a-3 moves one cohesive part that names none of `MAINT_NAMES`
  (the maint role checks, `role_problems` to `_check_connection`, are the candidate) into its own
  module, so the file's net change is ≤ 0 (CLAUDE.md rule 2); `test_maint_privileges.py` must pass
  unchanged. Opus-reviewed PR. The 2-connection maint pool is now shared with testers; fine for
  M7a, an M7b item.
- **Why not the app role:** the restrictive policy refuses `subscription` to the app role, and must
  keep doing so: SQL injected as the app role must never create a paid plan. **Why not a new
  role:** `classbeat_maint` already has `INSERT` on `entitlements`, is web-only, audited and
  column-allowlisted; a third login role adds a secret, startup checks and a `10-roles.sql` path
  for the same reach. **Why not `SECURITY DEFINER`:** the app role could call it with arguments of
  its choosing, which is the injection this rule is about.
- **Data (one migration in M7a-3):**
  - `billing_events`, owned (`owner_id`, forced RLS, cascades with the account): `provider`
    (check `IN ('dummy')`; M7b adds `paddle`), `event_id text` (≤ 200), `kind` (`started`,
    `renewed`, `upgraded`, `change_scheduled`, `ended`), `plan_id NULL`, `period_start`,
    `period_end`, `amount_minor bigint` (check `>= 0`: no refund event exists), `currency NULL`,
    `outcome` (`applied`, `ignored`), `created_at`. **`UNIQUE (provider, event_id)`, global**, so
    a replay under any owner is a no-op (listed as an exception in the RLS guard's owner-inclusive
    key check, next to the ordinary `UNIQUE (owner_id, id)`). Maint has `INSERT, SELECT`. The app
    role has `SELECT` on every column **except `amount_minor` and `currency`** (guard 2 lists them
    exactly, as for `plans`), for the export only: the app role still reads no price, paid or
    listed.
  - `subscriptions`, owned, `UNIQUE (owner_id)` (one per user): `provider`, `plan_id`,
    `anchor_day smallint` (CHECK 1 to 31, §5), `period_start`, `period_end`, `basis_minor bigint`
    and `basis_currency` (the full monthly price of `plan_id` charged for this period, §5),
    `next_plan_id NULL` (a scheduled downgrade), `cancel_at_end`, `status` (`active`, `ended`),
    `last_event_at`, `created_at`, `updated_at`. Maint `INSERT, SELECT, UPDATE` of the state
    columns. The app role has `SELECT` on every column except `basis_minor` and `basis_currency`
    (Account → Plan and the export; exact list under "Export and retention").
  - `billing_log`, owned (cascades with the account): `action` (a code list: `checkout`,
    `change`, `prices`), `outcome` (`ok`, `refused`, `error`), `reason NULL`, `request_id`,
    `created_at`; no IP, no amount. Maint `INSERT, SELECT`; app `SELECT` (export). Refusals are
    written in their own short transaction, as `admin_session` does.
  - `entitlements.billing_event_id uuid NULL` → `billing_events` (owner-inclusive, `NO ACTION`, so
    the account's cascade to both tables is checked at the end of the statement whatever the
    trigger order; M7a-3 runs the account-delete test with a subscription). CHECK
    `source <> 'subscription' OR (billing_event_id IS NOT NULL AND plan_id IS NOT NULL AND
    cardinality(features) = 0 AND ends_at IS NOT NULL AND ends_at <= starts_at + interval
    '32 days')`, and a unique index on `billing_event_id`, so every paid row traces to exactly one
    recorded event, lasts at most one period and carries no feature (song picking can never come
    with a plan).
- **Export and retention:** `subscriptions`, `billing_events` and `billing_log` are `Section`s of
  the export. `omit` cannot drop the price columns: `classbeat.account.export._read` selects
  `to_jsonb(t)` (the whole row) and removes the omitted keys afterwards, which fails with
  "permission denied" for a column the app role cannot read. So `Section` gains an optional
  **`columns`** tuple: when set, `_read` selects exactly those columns (`jsonb_build_object` of
  `t.<column>`, names validated by the registry's identifier rule) and never the whole row.
  `billing_events` and `subscriptions` must use it, with these lists:
  - `billing_events`: `id, provider, event_id, kind, plan_id, period_start, period_end, outcome,
    created_at`;
  - `subscriptions`: `id, provider, plan_id, anchor_day, period_start, period_end, next_plan_id,
    cancel_at_end, status, last_event_at, created_at, updated_at`.

  The app role's column grants on these tables are exactly these lists plus `owner_id` (guard 2
  lists them as it lists `plans`). `tests/test_account_export_registry.py` gains a check, against
  the live grants, that every column a `columns` section names is one the app role may `SELECT`,
  and that a table where the app role lacks `SELECT` on some column has a `columns` list (so a
  whole-row read of it can never be registered). `billing_log` has no price and is read whole. The
  export's `NOT_INCLUDED` gains "The amounts charged: they are with the payment provider (the test provider
  charges nothing)." All three go with the account (guard 10: cascade, nothing `RETAINED`).
  `admin_audit` changes nothing: a fast-forward's row has an operator as actor and the tester as
  target, nulled on deletion as today.
- **Apply, in one write transaction (`billing_session` or, for a fast-forward, `admin_session`):**
  lock the user's `subscriptions` row (or create
  it); `INSERT INTO billing_events … ON CONFLICT (provider, event_id) DO NOTHING RETURNING id`;
  no row means already applied: commit the audit row and return `duplicate`. Then check order: an
  event whose `period_start` is before the stored period (stale) is recorded `ignored` and changes
  nothing; one that skips ahead (a `renewed` whose `period_start` is not the stored `period_end`)
  is **not recorded** and rolls back (`out_of_order`; a real provider retries it, Dummy never
  emits one). Otherwise apply the kind:
  - `started` / `renewed`: insert one `subscription` entitlement `[period_start, period_end)` for
    the event's plan; store the period, the basis (the amount charged) and, for `started`,
    `anchor_day`; clear `next_plan_id` and `cancel_at_end`.
  - `upgraded`: insert one entitlement for the new plan `[now, period_end)`; the old row stays and
    is outranked; the stored period does not move; the basis becomes the new plan's full monthly
    price.
  - `change_scheduled`: store `next_plan_id` or `cancel_at_end`; no entitlement changes.
  - `ended`: `status = ended`; nothing is revoked, the last period's row runs out by itself.
  Entitlement rows are never updated except `revoked_at` (fast-forward, §6); every period is its own
  row, so no `UPDATE` of `ends_at` is needed and maint gets none.
- The order and refusal rules of `apply` (duplicate, stale, out of order, `amount_mismatch`, Dummy
  while a non-Dummy subscription is active) are a pure function of the stored subscription state
  and the event (`classbeat.billing.apply_policy`), so the rules for a provider the CHECK does not
  allow yet are unit-tested on an in-memory state; the database part only runs its decision.
- Every statement names `owner_id` explicitly (maint bypasses RLS); M7a-3's tests include a
  two-user test that an event for one user never touches the other's rows.

### 4. `BillingProvider` and Dummy

`classbeat.billing.provider` defines a Protocol: `checkout(quote) -> Checkout` and
`schedule_change(subscription, next_plan | cancel) -> Event`, both returning provider-neutral
events for `apply`. The provider never decides plans, periods or amounts: those come from the pure
policy (§5). No refund method exists. **Dummy** (`classbeat.billing.dummy`) completes a checkout
synchronously in the same request: the event id is derived from the user id and the request's
`Idempotency-Key` (a retried click is a duplicate event), the amount is the quote. Paddle (M7b)
implements the same Protocol behind its verified webhook.

`CLASSBEAT_BILLING_ENABLED` (new, default `false`) is the public switch. In M7a, settings
validation **refuses `true`** (no real provider exists), so production cannot turn billing on by
accident; M7b lifts that. The admin settings show the setting instead of the constant.

### 5. Periods and proration (pure: `classbeat.billing.subscription_policy`, M7a-1)

All times UTC. A **period** starts at the purchase instant and ends one calendar month later at
the same time of day, the day clamped to the month's last day. The next period starts at the
previous end, and every end is computed from the stored `subscriptions.anchor_day` (the UTC day of
month of the first period's start, 1 to 31), not from the previous end, so it does not drift back
after a short month: an anchor of 31 gives 31 Jan → 28 or 29 Feb → 31 Mar → 30 Apr. Monthly only.

- **Buy** (Free to a paid plan, or after an `ended` subscription): a new period from now; charge
  the plan's `monthly_price_cents` in full.
- **Upgrade** (a higher rank while active): at once; with `diff = price_new − basis`, where
  `price_new` is the new plan's current monthly price and `basis` the stored price paid for the
  current plan this period (so an admin's price edit mid-period does not change the old side),
  charge `round_half_up(diff × days_left / days_in_period)` in integer minor units, computed as
  `(2 × diff × days_left + days_in_period) // (2 × days_in_period)`. When `diff <= 0` (the admin
  priced a higher plan at or below the paid one) the upgrade still applies at once and the charge
  is **0**; nothing is ever paid back.
  `days_in_period = period_end.date() − period_start.date()` in days (28 to 31).
  `days_left = max(1, period_end.date() − now.date())`: the day of the upgrade counts as a day left,
  so upgrading on the first day charges the full difference and on the last day one day's share.
  The period end does not move. An upgrade clears a scheduled downgrade or cancel.
- **Downgrade or cancel:** recorded (`change_scheduled`), effective at `period_end`: the renewal
  starts the lower plan at its full price, a cancel starts nothing and the account is back on Free
  when the last row ends. Choosing the current plan again clears the scheduled change at no charge.
  **No refund**, ever, and no amount below 0.
- **Refusals:** a plan without a monthly price is not offered (`price_missing`); prices in
  different currencies refuse an upgrade (`currency_mismatch`); a lower or equal rank is never an
  upgrade. The **introduction price is not used in M7a** (it stays admin-only data); M7b decides it.
- **Classes used stay used:** the quota period remains the UTC calendar month (ADR 0011 §3, Q4),
  independent of the billing period. A plan change applies to the next reservation; a downgrade
  below the month's held count refuses new classes until the next month (ADR 0011 §3).
- **Expiry:** nothing is grandfathered (no user has paid). A program already saved in MOWL is never
  touched; a stored draft is checked against the current plan at Make current, Save and Update
  (ADR 0011, 2026-11-03 amendment).

### 6. Tester access, and keeping a dummy subscription apart

- New feature code **`billing_test`**, granted only by an admin grant (step-up, audited): the
  entitlements CHECK becomes `features <@ '{song_picking,billing_test}'` and adds
  `NOT ('billing_test' = ANY (features)) OR (source = 'grant' AND ends_at IS NOT NULL AND
  ends_at <= starts_at + interval '90 days')`; `plans` and `redeem_codes` keep
  `<@ '{song_picking}'`, so no code or trial can carry it. `classbeat.plans.tables` splits
  `FEATURES` into the grant list and the code list.
- **Who may buy:** with billing off, a caller whose stored role is `admin` or whose effective
  features include `billing_test`; everyone else gets 403 `billing_closed` and sees "plans are not
  open yet". Checked in the route's transaction, at every request, never cached.
- **Apart from real money:** `subscriptions.provider` and `billing_events.provider` say `dummy`;
  in M7a the CHECKs allow nothing else, and `apply` refuses a Dummy `started` while a non-Dummy
  subscription is active (M7b's Paddle checkout ends a Dummy one first). Admin → Billing lists
  Dummy subscriptions under their own heading.
- **Renewal:** Dummy does not renew on a clock. An admin's **fast-forward** (step-up, audited in
  `admin_audit`, Dummy only, refused for any other provider) revokes **every** `subscription`
  entitlement of the current period (`revoked_at = now()`; after an upgrade that is both the old
  and the new plan's row, so a leftover higher row cannot outrank a renewal to a lower plan), ends
  the stored period now, and applies the period-end event the rules give: `renewed` (same plan, or
  `next_plan_id`) or `ended` (cancelled, or the user is no longer an admin or tester). A renewal
  after a fast-forward starts its period now and **resets `anchor_day` to today's UTC day**, so
  the next end follows from the new start; the old anchor is not kept. A Dummy period that passes
  without a fast-forward ends; the account is on Free. Accepted for a test provider.
- **A tester whose `billing_test` ends** keeps the Dummy plan until that period ends (at most one
  month, nothing renews it), then is on Free. Bounded and accepted. M7b's Paddle checkout that ends
  a Dummy subscription first must also revoke its current period's rows, since `ended` revokes
  nothing.

### 7. Prices: the read path

Prices are read **only on maint**: `classbeat.billing.prices` opens no session; it reads with the
session it is given, which is the `billing_session` the billing routes opened (the only caller
allowed, §3) or, for the writer during a fast-forward, the admin's `admin_session`. It reads for a
caller who may buy (§6), to show the offer and to compute a quote; `apply` re-computes the quote
from the stored subscription and the current prices and refuses an `upgraded` or `started` event
whose amount differs (`amount_mismatch`). `0030_plan_price_privilege` and guard 2 stay unchanged:
the app role still reads no price, and the same holds for the new price columns
(`billing_events.amount_minor`, `currency`; `subscriptions.basis_minor`, `basis_currency`), which
guard 2 lists as unreadable. Each price read writes one `billing_log` row (`prices`), which is
acceptable because only testers and admins can reach it while billing is off. The public Pricing page and `GET /api/plans` (M7a-2) show names, quality and limits, never
a price. `backend/tests/test_plan_gates.py`'s scan allowlist gains `classbeat.billing.prices`.
M7b re-decides the read when every visitor may see a price.

### 8. Quality levels

`classbeat.plans.quality_model` maps plan id to the user-facing word: `free` → Standard,
`instructor` → Better, `pro` → Best. The model behind each word is the plan's model route (ADR
0012), set by the admin with `set_route`, which refuses a model without a passing eval; a plan with
no route uses the bootstrap model. Users never see a model name; the plan screen says Better and
Best give "more careful designs" and nothing about a model. The admin's model routes screen shows
the word next to each route key. "The quality follows the plan" is checked by `agent_runs.route_key`
equal to the job's plan id (done-when 7).

## Alternatives

- **Rename the ids** to `individual`, `professional`: touches route keys, eval results,
  entitlements and the trial setting for no user-visible gain. Rejected (names only).
- **A `song_picking` plan feature on every plan:** would make the plan carry a permission it does
  not decide, and `granted` would admit every account at once, bypassing the per-tester switch.
  Rejected; the plan never gates song picking.
- **Admin plan from the session's MFA role:** the worker has no session, and the plan is not
  authority. Rejected; the stored role.
- **Writer as the app role** with a looser policy, or a `SECURITY DEFINER` function: both let SQL
  injected as the app role create a paid plan. Rejected. **A new `classbeat_billing` role:** same
  reach as maint, more secrets and checks. Rejected for M7a; revisit if M7b's webhook needs a
  process without the admin credential.
- **Extending `ends_at` on renewal:** needs `UPDATE (ends_at)` for maint and loses the per-period
  history. Rejected; one row per period.
- **Prices to the app role** (re-grant two columns) or **a price view**: widens what SQL injected as
  the app role can read, against the reviewed 0030 decision, while only testers need prices.
  Rejected for M7a.
- **Proration by seconds or by floor:** seconds make amounts hard to state and test; floor
  under-charges by up to one minor unit every time. Rejected; days and half-up.
- **Billing actions in `admin_audit`** with the tester as actor: would make ordinary users actors
  of the operator log, so its retained `actor_email` would keep a non-operator's address after
  deletion (ADR 0014 §5) and `Actor` would need a second builder. Rejected; `billing_log`, owned.
- **Exporting billing events with amounts** through an app grant: the paid price is a price.
  Rejected; amounts stay with the provider and on maint.
- **Refusing an upgrade whose price is not higher:** blocks a plan change because of an admin's
  pricing slip. Rejected; the charge is 0.
- **A clock-driven Dummy renewal sweep:** needs a periodic job that holds maint, which the worker
  must not. Rejected; admin fast-forward.

## Consequences

- One data migration (M7a-2) and one schema migration (M7a-3); no new trigger or database
  function, so ADR 0005 §3's digest pin is untouched. Guards 2, 10 (retention), 11 and the GDPR
  export registry each gain the three new tables; the export includes the user's subscription,
  billing events and billing log without any amount (§3). Guard 8, `MAINT_MODULES` and `Actor`'s
  builder are unchanged.
- Testers with Instructor grants move to Individual's numbers when M7a-2 deploys.
- An admin testing limits must use a non-admin tester account, because an admin is always
  Professional.
- Billing cannot be switched on in M7a. M7b must: add `paddle` to the provider CHECKs, a verified
  webhook that feeds `apply`, check that Paddle's proration matches §5 (or charge §5's amount as a
  one-off), decide the price read for the public and the introduction price, size the maint pool
  (2 connections, shared with testers in M7a) or move billing off it, and revoke a Dummy period's
  rows when a Paddle checkout ends it.
- Roadmap Amendment 20 records M7's split; §9's Pro "priority queue" and multi-week series and the
  annual price are not in M7a.
