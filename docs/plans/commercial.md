# Plan: milestone M7 "Commercial" (revision 1): DRAFT / NOT APPROVED / NOT STARTED

Revision 1 folds in the owner's simplification of 2026-11-11: what we sell is **features of our
own app** (workout generation with song picking), not anything bought from Spotify or Swedebeat.
Design record to come: ADR 0031 (written in M7-0). Roadmap §9 is the frozen starting point;
this plan proposes an amendment to it. Where the two differ, the roadmap wins until amended.

This is a draft. It creates no issue and authorizes no work.

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

| Plan | Classes per month | Longest class | Games and the rest | Revisions per class |
|---|---|---|---|---|
| Free | x (3) | 30 min | no games, no races or FTP tests | as M5 (3) |
| Individual | 3x (9) | 75 min | everything | as M5 for Instructor (10) |
| Professional | 10x (30) | no plan limit (the global cap of 180 minutes in ADR 0011 stays) | everything, as Individual | as Individual |

- **Admins are Professional automatically.** The plan resolver treats `users.role = admin` as the
  highest rank, with no entitlement row and nothing to expire. It is checked in the same
  transaction as today's plan read, so a demoted admin loses it at once.
- **Names:** the plan's display name changes. The ids (`free`, `instructor`, `pro`) stay, because
  they key model routes, eval results and entitlements. A rename of the ids is optional later and
  needs its own migration; ADR 0031 records the choice (recommended: names only).
- **Free's "no ???"** from the owner's note: races and FTP tests stay allowed on every plan today
  (ADR 0011 seeds). The owner decides whether Free loses them; the plan assumes it does not.
- **Song picking:** M7-0 checks whether the `song_picking` feature code gates it per plan today
  and, if so, makes it part of every plan. The class allowance is the only meter.

## M7a: what gets built

### 1. Limits that say why
The server already refuses a class over the plan's length with 409 `quota` `length`, and a game
or FTP test with `feature`. M7a adds the **graceful front end**:
- the New class screen knows the plan's length cap and shows it near the prompt;
- a request that clearly asks for more (the interpreter's length is above the cap, or a game on
  Free) is stopped **before the job starts** with a short message and an upgrade button, not a
  failed job. The server refusal stays as the authority;
- the same wording on the workspace when a revision asks for more;
- **a gentle nudge toward Individual** on Free: when the monthly allowance is nearly used, on the
  first refusal, and once on the account page. Never a modal that blocks work, never repeated in
  one session after dismissal, and no countdown or fake urgency.

### 2. Plan and pricing menu
- A public **Pricing** page (prerendered like the other public pages, en/sv/da) and an
  **Account → Plan** screen: the three plans, what each includes, the price, the current plan,
  the period end, and the actions below.
- Prices come from the admin-only price table through one narrow, reviewed public read (ADR 0011
  amendment 2026-11-04 keeps prices from the app role today; M7-0 decides how this read passes
  that privilege rule). Nothing shows a price while billing is off and the viewer is not a tester.

### 3. Subscription rules (monthly only in this step)
- **Buy:** a plan for one month from now.
- **Renewal:** automatic at period end until cancelled.
- **Cancel or downgrade (to a lower plan or to Free):** takes effect **when the paid period
  ends**; until then the user keeps what they paid for. **No refund of money paid.**
- **Upgrade (Individual → Professional):** takes effect at once. The user pays the **difference
  for the rest of the period**: `(price_new − price_old) × days_left / days_in_period`, rounded
  to the minor unit, and the period end does not move. The class count already used this period
  stays used, and the new limits apply from now.
- **Expiry without renewal:** the account returns to Free. A program already saved in MOWL is
  never touched, and stored drafts are checked against the current plan as ADR 0011 already says.
- Annual plans, coupons and trials stay as M5 has them (codes, trials, admin grants are separate
  entitlement sources and keep working; the highest rank wins, ADR 0011).

### 4. Dummy billing provider and live testing
- A `BillingProvider` interface (pure policy for plan, period and proration; the provider only
  creates a checkout and reports events). **Dummy** is the first implementation: a checkout that
  completes on one click and emits the same events a real provider will. Paddle (M7b) is the
  second implementation behind the same interface.
- `CLASSBEAT_BILLING_ENABLED=false` stays the default and the public switch: everyone sees the
  pricing and the limits, and the upgrade button says plans are not open yet.
- **Live testing while it is off:** a user holding a `billing_test` feature entitlement (granted
  by an admin in the admin area, audited, expiring) and every admin get the working purchase flow
  against the dummy provider, in production, with no real money. A dummy subscription is marked
  `provider = dummy` and can never be mistaken for a paid one: it cannot be created when a real
  provider is active for that user, and the admin lists them separately.
- The dummy provider has an admin-only way to fast-forward a period, so renewal, expiry, downgrade
  and proration can be tested without waiting a month.
- The tester flag is the only thing that changes with billing off; no other user can reach it.

### 5. Entitlement writer
`subscription` finally gets a writer: one function in the worker or maint path (never the app
role writing arbitrary entitlements) that takes a provider event, is idempotent on the event id,
and sets plan, start and end. It is the same writer Paddle will use in M7b. Security-critical
(entitlements and external events): Opus Developer and Opus Reviewer.

## M7b: gated, not scheduled
Real Paddle (merchant of record: VAT, invoices, card data never ours), open sign-up with abuse
and cost controls, owner-approved legal pages and sub-processor list, the commercial
song-picking route, and provider agreements. These are as in revision 0 and keep the roadmap
gate: A1 to A5 for every subscriber, agreements signed. M7b starts only on a new instruction.

## Proposed PR split (M7a)

| # | PR | Depends on | Developer / Reviewer |
|---|---|---|---|
| M7a-0 | Plan, ADR 0031, roadmap amendment (tier names and numbers, song picking on every plan, upgrade and downgrade rules, dummy provider, tester access), status, issues | owner's go | Architect / **Opus** |
| M7a-1 | Pure `billing/subscription_policy.py` (period, proration, downgrade at expiry) and the provider interface, with table-driven tests | M7a-0 | Sonnet / Sonnet |
| M7a-2 | Admin is Professional; plan display names; `song_picking` on every plan if it was gated | M7a-0 | **Opus / Opus** (roles, entitlements) |
| M7a-3 | Subscription writer, dummy provider, checkout and cancel/upgrade routes, `billing_test` grant, admin list and fast-forward | M7a-1, M7a-2 | **Opus / Opus** |
| M7a-4 | Limits that say why: pre-job stop, upgrade message, gentle nudge (en) | M7a-0 | Sonnet / Sonnet |
| M7a-5 | Pricing page and Account → Plan screen (en), price read path | M7a-3 | Sonnet / Sonnet (Opus if the price read touches the privilege rule) |
| M7a-6 | sv/da translation of M7a strings | M7a-5 | Sonnet / **Opus** |
| M7a-7 | `e2e/tests/plans-done-when.spec.ts` on the compose stack with the dummy provider, status | all | Sonnet / Sonnet |

M7a-1, M7a-2 and M7a-4 can run in parallel. Each PR follows CLAUDE.md rules 1 to 3.

## Done when (M7a)
1. A Free user cannot start a class over 30 minutes or with a game: they are told why before
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
7. Song picking and the Spotify and Swedebeat connections behave the same on every plan.
8. e2e and axe pass at 1440 and 390 px; the strings exist in en, sv and da.

## Waiting for the owner
Not blocking M7a drafting. Decisions that shape it, with the plan's default so nothing stalls:
1. Go for M7a-0 (this plan is a draft).
2. Free keeps races and FTP tests (default: yes).
3. Ids stay, names change (default: yes).
4. Real prices (placeholders until you set them in the admin).
Everything about real Paddle, sign-up, legal text and provider agreements stays M7b.
