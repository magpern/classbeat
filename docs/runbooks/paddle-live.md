# Connecting real Paddle: the owner's runbook

[ADR 0032](../adr/0032-paddle-on-fakes.md), plan [paddle-on-fakes.md](../plans/paddle-on-fakes.md), issue #977 (PB-6).
Everything up to here is built and tested on a fake Paddle (`CLASSBEAT_BILLING_PROVIDER=fake_paddle`,
the compose e2e service `web-paddle`, `e2e/tests/paddle-done-when.spec.ts`). Connecting the real one is
**GitHub milestone 23, "M7b-real Connect real Paddle (later, owner-gated)": not authorized; it starts
only on the owner's instruction.** It has no issues yet. Until then `paddle` with
`CLASSBEAT_BILLING_ENABLED=true` is refused at settings validation, so nothing below can be switched on
by accident.

This page names no secret value. Every key is a file under `secrets/`, mounted like the other secrets.

## 1. What the owner does (nothing here is done by an agent)

1. **Paddle account.** Open a Paddle Billing account and its **sandbox** (a separate login). Finish
   the seller verification for the live account only when the sandbox run (section 4) has passed.
2. **Products and prices.** Create one product and a monthly price per paid plan (Individual and
   Professional) in the sandbox, with the amounts the admin set under Plans. The prices are **net of
   tax** (Paddle adds and remits it). Write the price ids down; they are settings (section 3).
3. **API key.** Create a server-side API key (the sandbox one first). Save it as a file under
   `secrets/` (milestone 23 names the file and the setting that reads it). It is never put in `.env`, a URL, a log or a chat.
4. **Webhook destination.** In Paddle, Developer tools, Notifications: add a destination at
   `https://<domain>/api/billing/paddle/webhook`, subscribed to `transaction.completed`,
   `transaction.payment_failed`, `subscription.updated`, `subscription.canceled`, `subscription.created`
   (the last four are acknowledged; only the first and the cancel/update ones change a plan) and the
   adjustment events (acknowledged and ignored until the refund policy is decided).
5. **Webhook secret.** Paddle shows the destination's secret key once. Save it as
   `secrets/paddle_webhook_secret` (one secret per line, at most four, so a rotation can hold the old and
   the new). It is mounted on `web` only; the worker refuses to start when it can read it.
6. **Settings** (from `.env`, see `compose.prod.yaml`): the two price ids (and the client-side token if
   the real checkout needs it), once milestone 23 has added them. They are ids, not secrets.

## 2. What must be built first (milestone 23, in this order)

Each is its own PR with its own review. None exists yet.

1. **The real `PaddleHttpClient`** behind the `PaddleClient` interface (`billing/paddle_client.py`):
   create a transaction (the hosted checkout), schedule a plan change, cancel at period end. Its outbound calls go to
   `api.paddle.com` and `sandbox-api.paddle.com` only, through the project's egress rules (a
   security-critical area: Opus review). Idempotency keys are sent as Paddle's own header.
2. **Call Paddle only after a successful write, or compensate.** Today `billing/routes.py` tells Paddle
   *before* the writer runs, inside the maint transaction: if the write then fails, Paddle has already
   been asked. With the fake this is harmless; with the real client it can leave a Paddle change that
   classbeat never recorded. Either run the Paddle call after the database commit and record the result,
   or undo it (a compensating call) when the write fails. A test with a client that fails after the call
   must show the two sides agree.
3. **The checkout host allowlist.** `safeCheckoutUrl` (frontend, `lib/checkoutWait`) follows only
   same-origin URLs, which is right for the fake. The real checkout is on a Paddle host: add exactly
   `checkout.paddle.com` and `sandbox-checkout.paddle.com` (not a wildcard) to an explicit allowlist, and
   show a **visible error** (not a silent no-op) when the server returns any other URL.
4. **Price ids as settings.** The fake derives `pri_` plus the plan id's hex. Real ids are settings per
   plan, and the catalogue (`paddle_catalog.py`) maps both ways. A real id and a placeholder id must
   never collide: the placeholder is cut to 64 characters, so two long plan ids with the same first
   32 bytes would map to one price. A test pins that the catalogue refuses a duplicate id.

## 3. The sandbox checks (do them before the live account)

The mapping in `paddle_events.py` and `paddle_paid.py` follows Paddle's documentation, not a real
delivery. Run one sandbox purchase, one upgrade, one downgrade, one cancel and one failed card, and
compare the delivered JSON (kept only in the sandbox dashboard; never paste a live one into the repo)
with these assumptions. Each mismatch is a fix in milestone 23, not a reason to loosen a check.

| Assumption in the code | What to look at in the sandbox delivery |
|---|---|
| The amount compared is `details.totals.subtotal`, **net of tax** | A purchase in a taxed country: `subtotal` is the plan price and `tax` is separate. If Paddle's `subtotal` already includes tax or a discount, the amount check refuses every payment from that country |
| An upgrade's period is the item's `proration.billing_period` | On the upgrade's `transaction.completed` (origin `subscription_update`): the period runs from the change to the period end, and the transaction's own `billing_period` is the whole subscription period |
| One item, quantity 1, no discount | A real upgrade may list a **credit line** for the unused time beside the new price. The mapping refuses more than one item (`items_not_single`), so a real upgrade could be refused; decide whether to read the charge line only |
| The cycle is one month | `billing_cycle` `{interval: month, frequency: 1}` on every price |
| A canceled subscription has no `current_billing_period` | `subscription.canceled` carries `null` there; the end is applied for the stored period |
| Link by signed transaction id, then subscription id | The first `transaction.completed` carries the same `txn_` id the checkout returned, and `custom_data` carries classbeat's owner id back |
| Ids and event ids match the strict patterns | `txn_`, `sub_`, `ctm_`, `pri_` plus lowercase base32; `evt_` plus letters and digits |
| Notifications of one subscription may arrive out of order | A retry after a 409 `out_of_order` or `payment_pending` lands once the earlier one has; Paddle retries on any non-2xx |

Also check, with the sandbox: that a delivery with the wrong secret is answered 401 and Paddle's
dashboard shows it as failed; that a payment that is declined shows the "did not go through" notice and
leaves the plan as it was; and that no log line holds a body, a signature or a key
(`docker compose logs web | grep -c <fragment>` of the secret must be 0).

## 4. Switching on

Only when sections 1 to 3 are done and the sandbox run passed:

1. Put the live account's key, webhook secret and price ids in place (the same steps, the live dashboard).
2. Set `CLASSBEAT_BILLING_PROVIDER=paddle` and `CLASSBEAT_BILLING_ENABLED=true` in the deployment's
   `.env`, then deploy (owner only; [vps-deploy.md](vps-deploy.md)). `paddle` with billing enabled is
   what settings validation accepts only once the real client exists.
3. Buy once yourself with a real card on the cheapest plan, confirm the plan changes when the signed
   notification lands, then cancel and read the refund policy you chose (below) before refunding by hand.
4. The legal pages (terms, privacy, refunds, sub-processors) are drafts. Publish them, replacing the
   beta text, **before** anyone but you pays.

## 5. Decisions that stand in front of this

These are in "Waiting for the owner" in [status.md](../status.md):

- **Site-wide caps** ([signup-controls.md](../plans/signup-controls.md), gap 3): there is no daily cap on
  new accounts and no global model-cost cap. Decide the numbers (and the user-facing refusal in en, sv
  and da) **before** `CLASSBEAT_PUBLIC_SIGNUP` is turned on, which is separate from billing.
- **Refund and chargeback policy.** Until decided, refund and chargeback notifications are logged as
  ignored with a reason and never end a subscription.
- **Legal text** (controller name, address, the four drafts) and, for the privacy text, how long an
  invite request is kept (30 or 90 days).

## 6. Switching off

`CLASSBEAT_BILLING_ENABLED=false` returns to the rule "only admins and holders of the `billing_test`
entitlement can buy"; stored subscriptions and their periods are kept. Rotating the webhook secret: add
the new one on a second line, deploy, and remove the old one after Paddle sends with the new.
