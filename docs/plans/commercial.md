# Plan: milestone M7 "Commercial" (revision 2)

**Status: M7a APPROVED** (owner, 2026-11-11: "go, implement M7", which authorizes M7a only).
**M7b stays gated**: not authorized, not scheduled, no issue.

Design: [ADR 0031](../adr/0031-plans-and-dummy-billing.md). Roadmap
[Amendment 20](../roadmap.md) records the split and amends §9. Where this plan and ADR 0031
differ, the ADR wins. Milestone "M7a Plans and billing (dummy)", umbrella #945.

Revision 1 folded in the owner's simplification of 2026-11-11: what we sell is **features of our
own app** (workout generation with song picking), not anything bought from Spotify or Swedebeat.
Revision 2 (M7a-0) records the owner's go, aligns the text with ADR 0031 (checked against the code)
and links the issues.

## What changes from revision 0

- Plans are shown as **Free, Individual, Professional**. Entitlement, quota and admin code stay as
  M5 built them.
- **Song picking is not a paid feature.** Every plan has it. Spotify and Swedebeat connections are
  available on every plan (Swedebeat if the user has it).
- A **dummy billing provider** comes first. Real Paddle, public sign-up and legal text are a later
  step (M7b) and keep their gate.
- The work splits in two so most of it can be built now without any provider or real money:
  - **M7a (buildable on fakes, billing off):** tiers, limits, upgrade prompts, pricing and
    plan menu, dummy provider, upgrade, downgrade and proration, admin tester access.
  - **M7b (gated):** real Paddle, public sign-up, legal pages, abuse controls, and the commercial
    song-picking route that roadmap A1 to A5 and the provider agreements require.

## Tiers (numbers are placeholders; the admin edits them, ADR 0011)

`x` is the free allowance. The owner's ratio is 1 : 3 : 10; the placeholder `x` is 3.

| Plan | Classes per month | Longest class | Games, FTP tests | Quality | Revisions per class | Jobs at once |
|---|---|---|---|---|---|---|
| Free | x (3) | 30 min | no | Standard | 3 | 1 |
| Individual | 3x (9) | 75 min | yes | Better | 10 | 2 |
| Professional | 10x (30) | no plan limit (stored as 180, the global `CLASSBEAT_CLASS_MINUTES_MAX` cap of ADR 0011) | yes | Best | 20 | 3 (the default of `CLASSBEAT_JOBS_PER_USER` goes from 2 to 3) |

All numbers are placeholders the admin edits. Everything else is the same on every plan: song
picking, the Spotify and Swedebeat connections, running classes, history, export and delete.

- **Quality** is the model route per plan, which M5 and ADR 0012 already provide (`plans.route_key`,
  one route per plan and stage group). Users see only **Standard, Better, Best**, never a model
  name; the admin maps each to a model and effort through `set_route`, which refuses a model that
  has not passed the eval suite. So Free's Standard route must pass the same evals as the others,
  and a plan with no route falls back to ADR 0012's bootstrap choice. The plan screen says Better
  and Best give "more careful designs", and no claim is made about a specific model.
- **Games and FTP tests** are Individual and up. **Races stay on every plan:** a race here is a
  short hard burst (15 to 35 s in zone 6 or 7, ADR 0029), a sprint-style effort, not a MOWL game
  (owner, 2026-11-11). ADR 0011 already has the three gates; M7a-2's data migration turns
  `ftp_tests_allowed` off for Free (games are already off there) and leaves `races_allowed` on.
- **Considered and not limited:** song picking (owner: unaffected), history length, number of
  saved classes, export (a legal right), connections. Add one later only if cost data asks for it.
- **Admins are Professional automatically.** The plan resolver treats `users.role = admin` as the
  highest rank, with no entitlement row and nothing to expire. It is checked in the same
  statement as today's plan read, so a demoted admin loses it at once (ADR 0031 §2: the stored
  role, not the session's MFA role).
- **Names:** the plan's display name changes. The ids (`free`, `instructor`, `pro`) stay, because
  they key model routes, eval results and entitlements (ADR 0031 §1: names only).
- **Song picking:** checked in M7a-0 (ADR 0031 Context): no plan gates it today. `plans.features`
  is empty on every row and the admin cannot edit it; the gate is the kill switch and the
  `song_picking` flag, whose `granted` mode reads the per-tester *entitlement* feature (ADR 0010).
  It stays plan-independent; M7a-2 pins that with a test. The class allowance is the only meter.

## M7a: what gets built

### 1. Limits that say why
The server already refuses a class over the plan's length with 409 `quota` `length`, and a game
or FTP test with `feature`. M7a adds the **graceful front end**:
- the New class screen knows the plan's length cap and shows it near the prompt;
- a request that clearly asks for more is stopped **before the design job starts** (the job that
  takes the class credit) with a short message and an upgrade button, not a failed job. The
  interpreter already holds such a request to the plan and marks it "Not as asked" with
  `over_plan_length`, `plan_no_games` or `plan_no_ftp_tests` (`design.interpret_policy.held`); the
  page now asks the user to design within the plan or see plans before the hand-off. The server
  refusal stays as the authority;
- the same wording on the workspace when a revision asks for more;
- **a gentle nudge toward Individual** on Free: when the monthly allowance is nearly used, on the
  first refusal, and once on the account page. Never a modal that blocks work, never repeated in
  one session after dismissal, and no countdown or fake urgency.

### 2. Plan and pricing menu
- A public **Pricing** page (prerendered like the other public pages, en/sv/da) and an
  **Account → Plan** screen: the three plans, what each includes, the price, the current plan,
  the period end, and the actions below.
- Prices stay unreadable by the app role (migration 0030 unchanged). They are read only through
  an audited maint session for a caller who may buy (ADR 0031 §7). The public Pricing page and
  `GET /api/plans` show no price. Nothing shows a price while billing is off and the viewer is not
  a tester or admin. The Pricing page ships as `draft` in `publicPaths.json`.

### 3. Subscription rules (monthly only in this step)
- **Buy:** a plan for one month from now.
- **Renewal:** automatic at period end until cancelled.
- **Cancel or downgrade (to a lower plan or to Free):** takes effect **when the paid period
  ends**; until then the user keeps what they paid for. **No refund of money paid.**
- **Upgrade (Individual → Professional):** takes effect at once. The user pays the **difference
  for the rest of the period**: `(price_new − price_old) × days_left / days_in_period`, rounded
  half-up to the minor unit, with days counted in UTC dates and the upgrade day counted as a day
  left (ADR 0031 §5), and the period end does not move. `price_old` is the price actually paid for
  this period (stored on the subscription); if the new price is not higher, the upgrade still
  applies and costs 0. The class count already used stays used
  (the quota period stays the UTC calendar month), and the new limits apply from now.
- The introduction price is not used in M7a (it stays admin-only data; M7b decides).
- **Expiry without renewal:** the account returns to Free. A program already saved in MOWL is
  never touched. Nothing needs grandfathering: no one has created classes yet (owner, 2026-11-11),
  and ADR 0011's rule that a stored draft is checked against the **current** plan at Make current,
  Save and Update already covers any later case.
- Annual plans, coupons and trials stay as M5 has them (codes, trials, admin grants are separate
  entitlement sources and keep working; the highest rank wins, ADR 0011).

### 4. Dummy billing provider and live testing
- A `BillingProvider` interface (pure policy for plan, period and proration; the provider only
  creates a checkout and reports events). **Dummy** is the first implementation: a checkout that
  completes on one click and emits the same events a real provider will. Paddle (M7b) is the
  second implementation behind the same interface.
- `CLASSBEAT_BILLING_ENABLED=false` (a new setting) stays the default and the public switch:
  everyone sees the plans and the limits, and the upgrade button says plans are not open yet. In
  M7a the settings refuse `true`, since no real provider exists.
- **Live testing while it is off:** a user holding a `billing_test` feature entitlement (granted
  by an admin in the admin area, audited, expiring) and every admin get the working purchase flow
  against the dummy provider, in production, with no real money. A dummy subscription is marked
  `provider = dummy` and can never be mistaken for a paid one: it cannot be created when a real
  provider is active for that user, and the admin lists them separately.
- The dummy provider has an admin-only way to fast-forward a period, so renewal, expiry, downgrade
  and proration can be tested without waiting a month. Dummy does not renew on a clock: a period
  that passes without a fast-forward ends (ADR 0031 §6).
- `billing_test` is granted by an admin grant only (never a code or trial), with an end at most
  90 days away; the database checks both (ADR 0031 §6).
- The tester flag is the only thing that changes with billing off; no other user can reach it.

### 5. Entitlement writer
`subscription` finally gets a writer: `classbeat.billing.writer.apply`, in `web` on the audited
maint path, never the app role: a user's own checkout runs in `billing_session` (audited in an
owned `billing_log`, so testers never become actors of the operator log), an admin's fast-forward in
`admin_session`. It takes a provider event, is idempotent on `(provider, event_id)`, refuses
out-of-order events, and inserts one entitlement row per paid period, each traced to its event by
a database check (ADR 0031 §3). The export includes subscriptions, billing events and the billing
log without any amount; the app role reads no price, paid or listed. It is
the same writer Paddle will use in M7b. Security-critical (entitlements and external events):
Opus Developer and Opus Reviewer.

## Later: importing a workout from MOWL (note from the owner, not in M7a)
Classbeat should one day import an existing MOWL workout. The rule is fixed now so the later
design does not change it: **an imported workout goes through the same plan gates as a generated
one** (length, games, races, FTP tests; ADR 0011's `check_stored_draft`). If it does not comply
with the user's plan it is **rejected before anything is stored**, with the reason and an upgrade
prompt, never trimmed or edited silently. Import itself needs a read path from MOWL that does not
exist today (the library has no import; `mowl-workouts` would grow one) and is its own milestone
with its own plan. Nothing in M7a builds it.

## M7b: gated, not scheduled
Real Paddle (merchant of record: VAT, invoices, card data never ours), open sign-up with abuse
and cost controls, owner-approved legal pages and sub-processor list, the commercial
song-picking route, and provider agreements. These are as in revision 0 and keep the roadmap
gate: A1 to A5 for every subscriber, agreements signed. M7b starts only on a new instruction.

## PR split (M7a)

| # | Issue | PR | Depends on | Developer / Reviewer |
|---|---|---|---|---|
| M7a-0 | #945 (umbrella, stays open) | Plan, ADR 0031, roadmap Amendment 20, status, milestone and issues | owner's go | Architect / **Opus** |
| M7a-1 | #946 | Pure `billing/subscription_policy.py` (period, proration, scheduled changes) and the `BillingProvider` interface, with table-driven tests | M7a-0 | Sonnet / **Opus** (money arithmetic) |
| M7a-2 | #947 | Plan names and numbers (data migration), `CLASSBEAT_JOBS_PER_USER` default 3, admin is Professional, quality mapping, public `GET /api/plans` (no price), song picking pinned plan-independent | M7a-0 | **Opus / Opus** (roles, entitlements) |
| M7a-3 | #948 | Subscription writer on `billing_session`, Dummy provider, billing routes, price reads, `billing_test` grant, `CLASSBEAT_BILLING_ENABLED`, admin list and fast-forward | #946, #947 | **Opus / Opus** |
| M7a-4 | #949 | Limits that say why: stop before the design job, upgrade message, gentle nudge (en) | #947 | Sonnet / Sonnet |
| M7a-5 | #950 | Pricing page (draft) and Account → Plan screen (en) on M7a-3's API | #948 | Sonnet / Sonnet (prices come only from M7a-3's API, so no privilege change here) |
| M7a-6 | #951 | sv/da translation of M7a strings | #949, #950 | Sonnet / **Opus** |
| M7a-7 | #952 | `e2e/tests/plans-done-when.spec.ts` on the compose stack with the dummy provider, status | all | Sonnet / Sonnet |

#946 and #947 run in parallel; #949 can run beside #948. Each PR follows CLAUDE.md rules 1 to 3.

## Done when (M7a)
1. A Free user cannot start a class over 30 minutes or with a game or FTP test: they are told why before
   anything runs, and offered Individual.
2. A tester buys Individual with the dummy provider: 75-minute classes, games and 9 classes a
   month work at once; the plan screen shows the period end.
3. Upgrading to Professional mid-period charges exactly the prorated difference, keeps the period
   end, and applies the new limits at once.
4. Downgrading or cancelling changes nothing until the period ends, then the account is Free;
   no refund is created anywhere.
5. An admin is Professional with no entitlement; an ordinary user cannot reach the dummy flow
   while billing is off.
6. A replayed or out-of-order provider event changes nothing.
7. Song picking and the Spotify and Swedebeat connections behave the same on every plan; the
   quality level follows the plan (the job records which route it used).
8. e2e and axe pass at 1440 and 390 px; the strings exist in en, sv and da.

## Waiting for the owner
Nothing blocks M7a. Decided on 2026-11-11: the go for M7a; Free without games and FTP tests,
races everywhere; ids stay, names change; Standard, Better and Best are the only user-facing
quality words and the admin picks the models; no refunds; monthly only; billing stays off.
Open, not blocking: **real prices** (placeholders until the owner sets them in the admin; until
then no plan can be bought, even by a tester) and **publishing the Pricing page** (it ships as
draft). Everything about real Paddle, sign-up, legal text and provider agreements stays M7b.
