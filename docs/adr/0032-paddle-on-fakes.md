# ADR 0032: Paddle built on fakes, behind flags (M7b-prep)

Status: Accepted (owner go 2026-11-12, quoted: "yes, go ahead with the fake Paddle and the rest",
in answer to a proposal listing the fake, the adapter, legal drafts and sign-up controls; roadmap
Amendment 21). Connecting real Paddle is **not** covered and stays the owner's step.

## Context

ADR 0031 built the seam: `BillingProvider` (`checkout`, `schedule_change`), provider-neutral events
(`Started`, `Renewed`, `Upgraded`, `ChangeScheduled`, `Ended`), one idempotent writer
(`billing.writer.apply`, on `(provider, event_id)`) and `DummyProvider`. Paddle is a merchant of
record and **pushes** state by webhook, so a real provider differs from Dummy in three ways: it
may complete later (`Checkout.event is None`), it signs its events, and it retries and reorders
them. The owner asked for a fake Paddle so everything except the live connection can be built and
tested now, with the last step left for later and gated by an environment flag.

## Decision

1. **Pure Paddle layer, no I/O** (`billing/paddle_signature.py`, `billing/paddle_events.py`):
   - **Signature.** Verify `Paddle-Signature` (`ts=<unix>;h1=<hex>`): HMAC-SHA256 of
     `<ts>:<raw body>`, constant-time compare, several `h1` values allowed for secret rotation. The
     header is parsed strictly (exactly one `ts`, at least one `h1`, nothing else; anything else is
     refused). The replay window is **5 minutes in both directions** (a timestamp too old or too
     far in the future is refused).
   - **One source per event.** Paddle sends several notifications for one purchase, so only these
     map to neutral events: `transaction.completed` is the **only** source of paid events
     (`Started` for the first payment, `Renewed` for a recurring one, `Upgraded` for the
     proration charge of a plan change, by the transaction's `origin` and our stored state);
     `subscription.updated` maps to `ChangeScheduled` (scheduled change, cancel or resume);
     `subscription.canceled` alone maps to `Ended`. `ChangeScheduled` is emitted only when the scheduled change differs from the stored one (`subscription.updated` fires on every change). `subscription.created`,
     `subscription.activated` and every other type are acknowledged and ignored. The neutral
     `event_id` is Paddle's own notification `event_id` (`evt_...`), which stays the same across
     Paddle's retries.
   - **Agreement with the policy.** Plans, periods and amounts still come only from
     `subscription_policy`; the mapping checks that the notified price and period agree with it and
     refuses when they do not.
2. **`PaddleClient` protocol** (the only network seam): create a checkout for a quote, schedule a
   change, cancel. `PaddleProvider` implements `BillingProvider` over a `PaddleClient`.
   **`FakePaddleClient`** (test and compose only) simulates the lifecycle: it returns a hosted
   fake checkout URL, signs webhook deliveries with a test secret, can retry, duplicate, delay and
   reorder them, and can fail a payment. The real HTTP client is **not written**; it is the
   owner's gated step (M7b-real: keys, webhook URL, price ids in Paddle, the client PR).
3. **Webhook route** `POST /api/billing/paddle/webhook`:
   - **Who the user is.** The notification is never trusted for it. At checkout we store a link
     `billing_links (owner_id, provider, checkout_ref, customer_ref, subscription_ref,
     activated_at, changed_at)` created before the buyer pays. The signed transaction id (the
     checkout's) or, once the first payment has applied and stored it, the signed subscription
     id selects the link row, and the link names the owner; the customer id and `custom_data`
     are only cross-checked. The protection is the signature plus our own link made at checkout:
     a signed id we never linked selects nothing (refused, `no_link`). Our own user id inside a
     payload is never used to choose a row. *(Amended in PB-2, #973.)*
   - **Which subscription.** A returning buyer has one link per checkout, all with the same
     Paddle customer id; the newest activated link is the owner's **current** subscription. A
     `subscription.updated` or `subscription.canceled` of another (older) subscription is
     acknowledged and ignored (`other_subscription`: a cancel of a different subscription never
     ends the current one), and a paid transaction of one is refused (422). A customer id maps
     to **at most one user**: a first payment whose customer id another user's link holds is
     refused (`customer_conflict`).
   - **Order of work.** Read the **raw** body (under a small body cap, with an anonymous rate
     limit), verify the signature and the window **before** parsing **and before any database
     access** (a bad signature never touches the database), then resolve the user, map, and call
     the existing writer.
   - **Which database role.** `billing_session` runs on the **maint** role (`classbeat_maint`,
     `BYPASSRLS`, ADR 0011 §5; the writer on it: ADR 0031 §3) and the writer needs it to insert
     entitlements. The webhook cannot
     use the app role, so it **does** use the maint role without a user session; the safeguards
     are: only a signature-verified, link-resolved, policy-checked event reaches it, and the only
     rows the payload selects are through the signed ids we linked ourselves (above). It gets its own entry point, `webhook_session` in
     `admin/db.py`, a sibling of `billing_session` that takes a link-resolved `owner_id` and logs
     a `billing_log` row with a new action `webhook`; guard tests allow it only from
     `classbeat.billing.webhook` (the existing guard for `billing_session` stays unchanged). The
     link lookup is a read-only maint transaction on `billing_links` before the user is known,
     and writes no log row. The maint pool (2 connections per web process, shared with admins and
     testers) is protected by a semaphore of 1 **per web process** for webhook work and by the
     rate limit (both in memory, so an unverified delivery never reaches the database); a
     delivery that finds it busy answers 503 so Paddle retries. With several web processes each
     has its own. PB-2 proves it with a burst test.
   - **Change times.** The link's `changed_at` is when its scheduled change last changed:
     Paddle's `occurred_at` of an applied `subscription.updated` (and of a newer one that changes
     nothing), and the request's time when classbeat records a change itself (a cancel,
     downgrade or resume through `PaddleProvider`). A `subscription.updated` older than it is
     `stale_change`. It only ever moves forward. The first payment stores the subscription and
     customer ids on the link (`activated_at`), so renewals and upgrades find it.
   - **Answers (Paddle retries on non-2xx).** Applied, duplicate, ignored type and `stale`: 200.
     `out_of_order` (the writer recorded nothing, so a retry may succeed): 409, so Paddle retries
     and the event lands once the gap is filled. A refusal that no retry can fix
     (`amount_mismatch`, `period_mismatch`, `plan_mismatch`, `cancelled`, `provider_conflict`, no
     link): 422 with a reason code, counted **once per `event_id`** in
     `billing_webhook_refusals` (a system table: a refusal may name no user; Paddle's retries of
     the same event do not inflate the count) and shown to admins (Admin > Billing, by reason). Bad or missing signature: 401.
   - **Never** logs the body, the header or the secret. The secret comes from a secret file
     (`*_FILE`), not from an environment value. The route is exempted from the CSRF and origin
     checks by an explicit allowlist entry with a test, and exists only when the provider is
     `fake_paddle` or `paddle`.
4. **Flags.** `CLASSBEAT_BILLING_PROVIDER = dummy | fake_paddle | paddle` (default `dummy`) and
   the existing `CLASSBEAT_BILLING_ENABLED` (default false). Config rules: `paddle` is refused
   until the real client exists (the check names why); `fake_paddle` is refused when
   `environment == prod`; a Paddle provider (the fake too) needs the `paddle_webhook_secret`
   file, which the worker refuses to see; `billing_enabled = true` stays refused until `paddle` is accepted, and
   then needs the Paddle secrets. With the flag off, only admins and `billing_test` holders reach
   the flow, with Dummy or the fake, exactly as in ADR 0031.
5. **Storage.** PB-2's migration adds `paddle` and `fake_paddle` to the provider CHECKs of
   `billing_events` and `subscriptions` (migration 0046 allows only `dummy`), adds `billing_links` (unique on `(provider, checkout_ref)` and `(provider, subscription_ref)`; **not** on the customer id, which Paddle keeps per buyer across checkouts: a customer id maps to at most one user, checked by the webhook, section 3; a notification whose subscription id disagrees with its link is refused) and `billing_webhook_refusals`,
   and events from the fake are stored as `fake_paddle`, never as `paddle`, so a fake can never be
   mistaken for a paid subscription. A `Started` from a Paddle provider for a user whose stored
   subscription is a Dummy one ends the Dummy subscription, revokes its entitlement rows as ADR 0031 §6 requires, and starts the new one (a `fake_paddle` `Started` replaces a Dummy subscription the same way); it is the only
   provider replacement the writer allows (`apply_policy` rule 2 otherwise stays).
6. **Proration.** The fake and the writer use ADR 0031 §5 amounts (the writer recomputes and
   refuses a mismatch). Paddle must be configured to charge the same upgrade amount; the runbook
   says how, and the sandbox run checks it.
7. **Frontend.** Account > Plan handles the deferred completion: after checkout it shows
   "Waiting for confirmation" until the webhook lands (poll the plan), and a failed payment
   leaves the plan unchanged with a stated reason. In test and compose the fake hosted checkout
   page confirms or fails a payment.
8. **Legal page templates** (terms, privacy, refunds, sub-processors) are written as **drafts**
   (`status: draft`, not in the sitemap, not linked) in the public pages system. The text is a
   legal decision: it is a placeholder for the owner and a lawyer, in English first.
9. **Sign-up and cost controls.** A Developer first reports what already exists (Turnstile,
   `admin/abuse.py`, sign-up routes, per-user job and cost caps) and closes the gaps behind one
   flag, `CLASSBEAT_PUBLIC_SIGNUP` (default false). No gap closes by weakening an existing
   guard.
10. **No refund event or method** stays (ADR 0031 §5). A Paddle refund or chargeback notification
   is mapped to `Ended` only after the owner decides the policy; until then it is logged as
   ignored with a reason code and listed in "Waiting for the owner".

## Alternatives

- Wait for a Paddle sandbox key: blocks all work on an account only the owner can create. The
  fake follows Paddle's public documentation; details may differ and are checked against the
  sandbox when a key exists.
- Reuse `DummyProvider` for the webhook path: it never exercises signatures, retries or
  reordering, which are exactly the risks.

## Consequences

- Real Paddle later needs: a Paddle account and keys (secrets), a webhook URL, price ids, the
  `PaddleHttpClient` PR, a sandbox run, and flipping the flags. Runbook: M7b-prep-6.
- The writer, plans and UI do not change when the real client arrives.
- Security-critical parts (signature, webhook route, config gates): Opus Developer and Opus
  Reviewer.
