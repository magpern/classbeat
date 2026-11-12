# ADR 0032: Paddle built on fakes, behind flags (M7b-prep)

Status: Accepted (owner go 2026-11-12: "go ahead with the fake Paddle and the rest"; roadmap
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
   verify Paddle's `Paddle-Signature` header (`ts=<unix>;h1=<HMAC-SHA256 hex>` over
   `<ts>:<raw body>`, constant-time compare, a replay window in seconds, several `h1` values
   allowed for secret rotation), and map Paddle notifications (`subscription.created`,
   `transaction.completed`, `subscription.updated`, `subscription.canceled` and the like) to the
   neutral events. Unknown event types are acknowledged and ignored. Plans, periods and amounts
   still come only from `subscription_policy`; the mapping checks that the notified price and
   period agree with the policy and refuses (no write) when they do not.
2. **`PaddleClient` protocol** (the only network seam): create a checkout for a quote, schedule a
   change, cancel. `PaddleProvider` implements `BillingProvider` over a `PaddleClient`.
   **`FakePaddleClient`** (test and compose only) simulates the lifecycle: it returns a hosted
   fake checkout URL, signs webhook deliveries with a test secret, can retry, duplicate, delay and
   reorder them, and can fail a payment. The real HTTP client is **not written**; it is the
   owner's gated step (M7b-real: keys, webhook URL, price ids in Paddle, the client PR).
3. **Webhook route** `POST /api/billing/paddle/webhook`: reads the **raw** body, verifies the
   signature and the replay window **before** parsing, maps, and calls the existing writer. A
   replayed or out-of-order event changes nothing (writer rule). It answers 2xx for ignored and
   already-seen events, 4xx for a bad signature. It never logs the body, the header or the
   secret. It needs no session; it is not a CSRF surface and is exempted from the CSRF and origin
   checks by an explicit allowlist entry with a test.
4. **Flags.** `CLASSBEAT_BILLING_PROVIDER = dummy | fake_paddle | paddle` (default `dummy`) and
   the existing `CLASSBEAT_BILLING_ENABLED` (default false). Config rules: `paddle` is refused
   until the real client exists (the check names why); `fake_paddle` is refused when
   `environment == prod`; `billing_enabled = true` stays refused until `paddle` is accepted, and
   then needs the Paddle secrets. With the flag off, only admins and `billing_test` holders reach
   the flow, with Dummy or the fake, exactly as in ADR 0031.
5. **Frontend.** Account > Plan handles the deferred completion: after checkout it shows
   "Waiting for confirmation" until the webhook lands (poll the plan), and a failed payment
   leaves the plan unchanged with a stated reason. In test and compose the fake hosted checkout
   page confirms or fails a payment.
6. **Legal page templates** (terms, privacy, refunds, sub-processors) are written as **drafts**
   (`status: draft`, not in the sitemap, not linked) in the public pages system. The text is a
   legal decision: it is a placeholder for the owner and a lawyer, in English first.
7. **Sign-up and cost controls.** A Developer first reports what already exists (Turnstile,
   `admin/abuse.py`, sign-up routes, per-user job and cost caps) and closes the gaps behind one
   flag, `CLASSBEAT_PUBLIC_SIGNUP` (default false). No gap closes by weakening an existing
   guard.
8. **No refund event or method** stays (ADR 0031 §5). A Paddle refund or chargeback notification
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
