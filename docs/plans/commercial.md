# Plan: milestone M7 "Commercial" (revision 0): DRAFT / NOT APPROVED / NOT STARTED

Design record to come: ADR 0031 (written in M7-0, not before). Roadmap §9 ("Tiers and billing"),
§10 ("External dependencies and track F") and the M7 row of the milestone table are the frozen
starting point. Where this plan and the roadmap differ, the roadmap wins until an amendment
changes it.

This is a draft for the owner to read and amend. It decides nothing, creates no issue and
authorizes no work.

**Execution boundary.** M7 stays deferred. Its gate in the roadmap is unchanged: a commercial
song-picking route that meets A1 to A5 for every subscriber, **and** signed agreements with
MOWL (and with Swedebeat for its features). No M7 PR starts before both exist and the owner
says so in a new explicit instruction. Drafting this plan is not that instruction. Nothing here
turns billing on, opens public sign-up, contacts a provider, or touches a payment account.

## Where we start

| Area | Today (verified in the repository) |
|---|---|
| Phase | Beta: invite-only, free, non-commercial (roadmap "Phases") |
| Sign-up | `signup_mode` (environment, with the stricter of environment and database winning); `compose.prod.yaml` defaults to `invite` |
| Plans | `free`, `instructor`, `pro` in `plans` with limits, class length cap, feature gates and price columns (ADR 0011 amendments 2026-11-03 and 2026-11-04). Prices are unreadable by the app role (migration `0030_plan_price_privilege`) and shown only in the admin. No price is shown to users |
| Entitlements | `grant`, `code`, `trial`, and `subscription`, which **has no writer** (ADR 0011 §2) |
| Billing flag | `CLASSBEAT_BILLING_ENABLED=false`, read-only in the admin, cannot be turned on there. No Paddle code, webhook, price display or subscription writer |
| Song picking | BYOK Spotify (each user's own Client ID, Development Mode), accepted for the beta circle only (ADR 0010 amended 2026-10-28; roadmap Amendment on A5) |
| Legal pages | `PrivacyPage` and `TermsPage` exist for the beta, in en, sv and da (ADR 0023 §8: translation PRs, no human proofreading) |
| GDPR | Export and delete exist (M6, ADR 0014) |
| Public pages | Prerendered landing, How it works, Examples, Features, Help (milestone PP, ADR 0030). CTAs say "Join the beta" |

## What M7 must deliver

Roadmap done-when: **A1 to A5 met for all subscribers; agreements signed.** This plan splits
that into five workstreams. Each is a set of PRs; none starts early.

### W1. Commercial song-picking route (the gate; mostly not code)
Problem: BYOK does not scale to the public. Spotify's terms leave BYOK for a hosted service
unclear, bar sending Spotify content to an AI model, and name business use (ADR 0010; roadmap §10).
Options to research, desk only, in M7-0, with no live call and no integration built:
1. A Spotify-approved route (Extended Quota, a partnership or written permission).
2. A route that needs no Spotify content in a model (for example MOWL- or Swedebeat-side
   analysis supplied under agreement, or the user's own uploaded or licensed list).
3. Soundless and mix-only classes as the paid product until a route exists (already built:
   soundless design, Swedebeat mixes).

Output: ADR 0031 section "Song-picking route" recording which option meets A1 to A5 and why,
and what the song-picking feature code does for a subscriber until then. The owner decides the
route; the Architect records the options and a recommendation.

### W2. Provider agreements (owner only)
MOWL (use of the reverse-engineered API by a public paid service) and Swedebeat (server-side
analysis, mixes). Agents may prepare the question lists (`docs/providers/questions.md` exists
from track F) and the ADR 0010 update. Agents never contact a provider, and never treat an
agreement as given until the owner records it under "Waiting for the owner".

### W3. Billing with Paddle (merchant of record), off by default
- `subscription` entitlement writer: a Paddle webhook handler that verifies the signature,
  is idempotent on the event id, runs in the worker or a maint path (never as the app role
  writing arbitrary entitlements), and maps a subscription to a plan and period.
- Checkout: Paddle-hosted checkout opened from the account page; classbeat stores only Paddle's
  customer and subscription ids, never card data.
- Lifecycle: trial, upgrade, downgrade at period end, cancel, failed payment grace, refund.
  Downgrade follows ADR 0011's stored-draft rule: a program already saved in MOWL is never
  touched; stored drafts are checked against the current plan.
- Price display: read through the one admin-side reader (ADR 0011, migration 0030), or a
  new narrow public read of the price table that the ADR amends deliberately. Prices follow
  measured cost per class; `PRICES_VERSION` bumps with any price change.
- `CLASSBEAT_BILLING_ENABLED` stays `false` by default and still cannot be turned on from the
  admin screen without the deploy-time setting. Tests and CI use a fake Paddle (netguard applies).
- Security-critical (billing, external writes): Opus Developer and Opus Reviewer.

### W4. Public sign-up
- `signup_mode=open` path already exists; M7 adds what open sign-up needs: abuse limits (Turnstile
  exists, ADR 0025), per-IP and per-account limits, email verification before any job, a cost
  cap per free account, and admin tools to disable an account.
- Public pages (PP) change the CTA from "Join the beta" to sign-up, in en, sv and da, in a
  translation PR with Opus review (ADR 0023 §8).
- Roadmap §4 free-tier numbers (3 classes a month) are re-checked against measured cost.

### W5. Legal and operations
- Terms, privacy policy, cookie statement, refund and cancellation terms, a sub-processor list
  (model providers, Paddle, mail relay, host), a data processing description, and the
  retention rules of ADR 0010 for Spotify-derived data.
- **Drafts only.** The owner (or a lawyer the owner names) approves legal text. Agents do not
  present generated legal text as reviewed. This is the one place where the "translation without
  human review" rule (roadmap, owner) does not extend: it covers translating approved text, not
  authoring commercial terms.
- VAT and invoicing are Paddle's as merchant of record; the plan records what remains ours
  (the owner's legal entity, support address, complaints route).
- Runbooks: billing incident (webhook backlog, wrong plan), refund, chargeback, provider
  withdrawal of access (the kill switches from M5 already exist for song picking and Swedebeat).
- Load and cost: a capacity check for the public (jobs, model spend, database), with the model
  budget per plan from ADR 0012 routes.

## Proposed PR split (not issues yet)

| # | PR | Depends on | Developer / Reviewer |
|---|---|---|---|
| M7-0 | ADR 0031 (route decision, billing design, abuse and legal scope), roadmap amendment, status, issues | owner's go + W1/W2 outcome | Architect / **Opus** |
| M7-1 | Paddle client, fake Paddle, webhook receiver with signature and idempotency, no writer yet | M7-0 | **Opus / Opus** |
| M7-2 | `subscription` entitlement writer and lifecycle (grace, downgrade, cancel), admin view | M7-1 | **Opus / Opus** |
| M7-3 | Checkout and plan screens (account, upgrade, cancel, invoices link), price display, en text | M7-2 | Sonnet / Sonnet |
| M7-4 | Abuse and cost controls for open sign-up, email verification gate, admin disable | M7-0 | **Opus / Opus** (auth, isolation) |
| M7-5 | Commercial song-picking route per ADR 0031 | W1 outcome | Opus / Opus |
| M7-6 | Legal pages, sub-processor list, cookie statement (owner-approved text) and en/sv/da translation | owner's approved text | Sonnet / **Opus** |
| M7-7 | Public CTAs, pricing page, sign-up wording (en, then sv/da translation PR) | M7-3, M7-6 | Sonnet / Sonnet |
| M7-8 | Runbooks, capacity check, `e2e/tests/commercial-done-when.spec.ts` on fakes, status | all | Sonnet / Sonnet |

M7-1 to M7-4 can run in parallel after M7-0 where files do not overlap. Each PR obeys CLAUDE.md
rules 1 to 3 (small modules, split on touch, pure policy apart from I/O): for example a pure
`billing/subscription_policy.py` (event to plan and period) apart from the webhook route and the
Paddle client.

## Done when
1. A visitor can sign up without an invite, design a class on the Free plan within its limits,
   and reach a clear upgrade path.
2. A subscriber pays through Paddle, gets the plan within a minute, and keeps it for exactly
   the paid period; cancel, failed payment, refund and downgrade each behave as ADR 0031 says.
3. A replayed, forged or out-of-order webhook changes nothing (tests with the fake Paddle).
4. No price, card or customer data is logged or sent to a model; prices are shown only through
   the reviewed read path.
5. The song-picking route in use meets A1 to A5 for every
   subscriber, as recorded in ADR 0031, and the signed agreements are recorded under "Waiting
   for the owner" with their dates.
6. Legal pages are owner-approved and published in en, sv and da; the sub-processor list is
   complete.
7. The kill switches (song picking, Swedebeat, sign-up mode, billing) work and each has a test.
8. e2e and axe pass on the compose stack with fakes; the Paddle sandbox is not required in CI.

## Waiting for the owner (genuine blockers only)
These decide whether M7 can start. None blocks drafting.
1. **Go/no-go and timing.** M7 starts only on the owner's instruction.
2. **Provider agreements** with MOWL and Swedebeat, and the Spotify decision (W1, W2).
3. **A legal entity, Paddle account and support address** (provider dashboards and paid
   services are owner-only per AGENTS.md "Safety envelope").
4. **Prices and trial rules** (roadmap Q10: decided from measured cost; draft €9 and €19).
5. **Legal text approval.**
Everything else (design, code, tests on fakes) is agent work.

## Out of scope
Club plan and seats; multi-week series; priority queue implementation beyond what M5 holds;
invoicing outside Paddle; live Paddle or live provider calls in CI; deploying anything.

## Risks
- The song-picking gate may stay closed. Then M7 ships a smaller product (soundless and mix-only
  classes) or waits; the plan keeps W3 and W4 independent of W1 so billing work is not wasted.
- Billing code is a new external-write surface; hence Opus on M7-1, M7-2 and M7-4.
- Public sign-up raises model spend; the per-user monthly cost cap (roadmap §4) and the free-tier
  limits are the first defence, and M7-4 adds a global cap.
