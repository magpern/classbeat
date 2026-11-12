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
     `subscription.canceled` and a final `past_due` end map to `Ended`. `subscription.created`,
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
     `billing_links (owner_id, provider, checkout_ref, customer_ref, subscription_ref)` created
     before the buyer pays; a notification resolves its user only through that link (by the
     transaction, subscription or customer id), and `custom_data` is at most a cross-check. No link
     means refused. Our own user id inside a payload is never used.
   - **Order of work.** Read the **raw** body (under a small body cap, with an anonymous rate
     limit), verify the signature and the window **before** parsing, resolve the user, map, then
     call the existing writer on the owner's `billing_session` (the path a user's own checkout
     uses, ADR 0031 §5), never an admin or maint session. PB-2 checks that this works for an
     unauthenticated route and reports if it cannot, rather than widening a role.
   - **Answers (Paddle retries on non-2xx).** Applied, duplicate, ignored type and `stale`: 200.
     `out_of_order` (the writer recorded nothing, so a retry may succeed): 409, so Paddle retries
     and the event lands once the gap is filled. A refusal that no retry can fix
     (`amount_mismatch`, `period_mismatch`, `plan_mismatch`, `cancelled`, `provider_conflict`, no
     link): 422 with a reason code, counted and shown to admins. Bad or missing signature: 401.
   - **Never** logs the body, the header or the secret. The secret comes from a secret file
     (`*_FILE`), not from an environment value. The route is exempted from the CSRF and origin
     checks by an explicit allowlist entry with a test, and exists only when the provider is
     `fake_paddle` or `paddle`.
4. **Flags.** `CLASSBEAT_BILLING_PROVIDER = dummy | fake_paddle | paddle` (default `dummy`) and
   the existing `CLASSBEAT_BILLING_ENABLED` (default false). Config rules: `paddle` is refused
   until the real client exists (the check names why); `fake_paddle` is refused when
   `environment == prod`; `billing_enabled = true` stays refused until `paddle` is accepted, and
   then needs the Paddle secrets. With the flag off, only admins and `billing_test` holders reach
   the flow, with Dummy or the fake, exactly as in ADR 0031.
5. **Storage.** PB-2's migration adds `paddle` and `fake_paddle` to the provider CHECKs of
   `billing_events` and `subscriptions` (migration 0046 allows only `dummy`), adds `billing_links`,
   and events from the fake are stored as `fake_paddle`, never as `paddle`, so a fake can never be
   mistaken for a paid subscription. A `Started` from a Paddle provider for a user whose stored
   subscription is a Dummy one ends the Dummy subscription and starts the new one; it is the only
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
