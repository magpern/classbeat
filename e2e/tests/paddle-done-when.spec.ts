import { expect, test } from './helpers'
import {
  PRICE,
  adminWithPrices,
  billingOf,
  buyer,
  choose,
  count,
  iso,
  monthAfter,
  openPlan,
  paddleUse,
  paid,
  planOf,
  post,
  prorated,
  refusalsOf,
  signature,
  tester,
  toCheckoutPage,
  type AdminCall,
  type Buyer,
} from './paddle-helpers'
import { sql } from './song-helpers'

// PB-6 (#977, ADR 0032, docs/plans/paddle-on-fakes.md "Done when", items 1 to 5), on the compose stack with the
// fake Paddle (`web-paddle`, CLASSBEAT_BILLING_PROVIDER=fake_paddle; billing stays off, so a tester buys).
// Pay and Decline are the fake's hosted page, driven through the UI, and its own signed events reach the
// webhook in process. The notifications the fake keeps to itself (a renewal, an upgrade's charge) and the
// ones that must be refused are built and signed here with the stack's public test secret and posted to
// the webhook like Paddle would. The backend suites pin the same rules one layer down
// (test_paddle_webhook.py); this is the whole path. `paddle` with billing on and `fake_paddle` in
// production are refused by the settings (test_paddle_settings.py), so they are not repeated here.
test.use(paddleUse)
test.describe.configure({ mode: 'serial' })
test.setTimeout(300_000)

let admin: AdminCall
let a: Buyer // the replay and the refusals
let b: Buyer // the reordered renewal
let anchor: number // the day of month the periods are anchored to (the first period's start day)

const ledger = (who: Buyer, kind?: string): number =>
  count('billing_events', who.id, kind ? `kind = '${kind}'` : 'true')

test('setup: the admin sets the prices; two testers buy Individual through the fake checkout', async ({ browser, baseURL }) => {
  admin = await adminWithPrices(browser, baseURL)
  a = await buyer(browser, baseURL, admin, 'pd-a')
  b = await buyer(browser, baseURL, admin, 'pd-b')
  anchor = new Date((await billingOf(a.page)).subscription!.period_start).getUTCDate()
})

test('1. a buyer pays on the fake checkout: waiting, then the plan is active', async ({ browser, baseURL }) => {
  const who = await tester(browser, baseURL, admin, 'pd-buy')
  const page = who.page
  expect(await planOf(page)).toBe('free')
  const checkout = await toCheckoutPage(page)
  // Nothing is bought yet: the link waits for the payment, there is no subscription or ledger row.
  expect(count('billing_links', who.id, 'activated_at IS NULL')).toBe(1)
  expect(sql(`SELECT checkout_ref FROM billing_links WHERE owner_id = '${who.id}'`)).toBe(checkout)
  expect((await billingOf(page)).subscription).toBeNull()
  expect(count('billing_events', who.id)).toBe(0)

  await page.getByRole('button', { name: 'Pay', exact: true }).click()
  await expect(page).toHaveURL(/\/account/)
  await expect(page.getByTestId('plan-subscription')).toContainText(/Individual, paid until/, { timeout: 30_000 })
  expect(await planOf(page)).toBe('instructor')
  expect((await billingOf(page)).subscription).toMatchObject({ plan: 'instructor', status: 'active', next_plan: null })
  expect(count('billing_events', who.id, "kind = 'started'")).toBe(1)
  expect(count('billing_links', who.id, 'activated_at IS NOT NULL')).toBe(1)
})

test('2. a replayed or duplicated notification changes nothing', async ({ request }) => {
  const before = (await billingOf(a.page)).subscription!
  const start = new Date(before.period_end)
  const renewal = paid(a, { plan: 'instructor', amount: PRICE.instructor, origin: 'subscription_recurring', start, end: monthAfter(start, anchor) })
  expect(await post(request, renewal.body)).toMatchObject({ status: 200, outcome: 'applied' })
  const after = (await billingOf(a.page)).subscription!
  expect(after.period_start).toBe(before.period_end)
  expect(ledger(a, 'renewed')).toBe(1)

  // The same bytes with the same signature (a replay), and signed afresh (Paddle's retry).
  const replay = signature(renewal.body)
  expect(await post(request, renewal.body, replay)).toMatchObject({ status: 200, outcome: 'duplicate' })
  expect(await post(request, renewal.body)).toMatchObject({ status: 200, outcome: 'duplicate' })
  expect((await billingOf(a.page)).subscription).toEqual(after)
  expect(ledger(a, 'renewed')).toBe(1)
  expect(ledger(a)).toBe(2) // started and renewed, nothing more
})

test('3. a renewal that arrives early answers 409 and applies once when its predecessor has landed', async ({ request }) => {
  const first = new Date((await billingOf(b.page)).subscription!.period_end)
  const second = monthAfter(first, anchor)
  const third = monthAfter(second, anchor)
  const early = paid(b, { plan: 'instructor', amount: PRICE.instructor, origin: 'subscription_recurring', start: second, end: third })
  const due = paid(b, { plan: 'instructor', amount: PRICE.instructor, origin: 'subscription_recurring', start: first, end: second })

  // The later renewal first: not recorded, not counted as a refusal, Paddle retries it.
  expect(await post(request, early.body)).toMatchObject({ status: 409, reason: 'out_of_order' })
  expect(ledger(b)).toBe(1)
  expect(refusalsOf(early.id)).toBe(0)
  expect(await planOf(b.page)).toBe('instructor')

  expect(await post(request, due.body)).toMatchObject({ status: 200, outcome: 'applied' })
  expect(await post(request, early.body)).toMatchObject({ status: 200, outcome: 'applied' }) // the retry
  expect(await post(request, early.body)).toMatchObject({ status: 200, outcome: 'duplicate' })
  expect(await post(request, due.body)).toMatchObject({ status: 200, outcome: 'duplicate' })
  expect(ledger(b, 'renewed')).toBe(2)
  expect((await billingOf(b.page)).subscription).toMatchObject({ period_start: iso(second), period_end: iso(third) })
})

test('4. a bad signature, an old timestamp and a price mismatch are refused and write nothing', async ({ request }) => {
  const sub = (await billingOf(a.page)).subscription!
  const start = new Date(sub.period_end)
  const end = monthAfter(start, anchor)
  const good = paid(a, { plan: 'instructor', amount: PRICE.instructor, origin: 'subscription_recurring', start, end })
  const events = ledger(a)
  const unchanged = async (): Promise<void> => {
    expect(ledger(a)).toBe(events)
    expect((await billingOf(a.page)).subscription).toEqual(sub)
  }

  // Signed with another secret, and a body changed after it was signed: 401, no database access.
  expect(await post(request, good.body, signature(good.body, undefined, 'pdl_ntfset_not_the_secret'))).toMatchObject({
    status: 401,
    reason: 'signature_mismatch',
  })
  expect(await post(request, good.body.replace('"quantity":1', '"quantity":2'), signature(good.body))).toMatchObject({
    status: 401,
    reason: 'signature_mismatch',
  })
  // A correct signature that is six minutes old (the window is five, both ways).
  const old = Math.floor(Date.now() / 1000) - 360
  expect(await post(request, good.body, signature(good.body, old))).toMatchObject({
    status: 401,
    reason: 'signature_outside_window',
  })
  expect(await post(request, good.body, 'garbage')).toMatchObject({ status: 401 })
  expect(refusalsOf(good.id)).toBe(0) // an unsigned request never reaches the refusal count
  await unchanged()

  // Signed correctly, but the price in the notification is not the plan's: a counted 422, once per event.
  const wrong = paid(a, { plan: 'instructor', amount: PRICE.instructor, unit: PRICE.instructor - 100, origin: 'subscription_recurring', start, end })
  expect(await post(request, wrong.body)).toMatchObject({ status: 422, reason: 'price_mismatch' })
  expect(await post(request, wrong.body)).toMatchObject({ status: 422, reason: 'price_mismatch' })
  expect(refusalsOf(wrong.id)).toBe(1)
  await unchanged()

  // None of it blocked the real one: it still applies once.
  expect(await post(request, good.body)).toMatchObject({ status: 200, outcome: 'applied' })
  expect(ledger(a)).toBe(events + 1)
})

test('5. a failed payment leaves the plan unchanged and says why', async ({ browser, baseURL }) => {
  const who = await tester(browser, baseURL, admin, 'pd-fail')
  const page = who.page
  await toCheckoutPage(page)
  await page.getByRole('button', { name: 'Decline the card' }).click()
  await expect(page).toHaveURL(/\/account/)
  const failed = page.getByTestId('checkout-failed')
  await expect(failed).toBeVisible({ timeout: 30_000 })
  await expect(failed).toContainText('The payment did not go through, so nothing was charged. Your plan is unchanged.')
  expect(await planOf(page)).toBe('free')
  expect((await billingOf(page)).subscription).toBeNull()
  expect(count('billing_events', who.id)).toBe(0)
  expect(count('subscriptions', who.id)).toBe(0)
  // The buyer can try again from the plan screen.
  await openPlan(page)
  await expect(page.getByRole('button', { name: 'Buy Individual' })).toBeVisible()
})

test('6. an upgrade waits for its notification, and the plan changes when it lands', async ({ browser, baseURL, request }) => {
  const who = await buyer(browser, baseURL, admin, 'pd-up')
  const page = who.page
  const before = (await billingOf(page)).subscription!
  await openPlan(page)
  await choose(page, 'Upgrade to Professional')
  // The fake only asks Paddle: the charge is Paddle's to notify, so the screen waits and the plan is unchanged.
  await expect(page.getByTestId('checkout-waiting')).toBeVisible()
  expect(await planOf(page)).toBe('instructor')
  expect(count('billing_events', who.id, "kind = 'upgraded'")).toBe(0)

  // Paddle's notification of the proration charge: the rest of the period, the plan's price difference.
  const now = new Date(Math.floor(Date.now() / 1000) * 1000)
  const amount = prorated(PRICE.pro - PRICE.instructor, before, now)
  const quoted = (await billingOf(page)).offers.find((o) => o.plan === 'pro')
  expect(quoted, 'the Professional offer').toBeDefined()
  expect([amount, prorated(PRICE.pro - PRICE.instructor, before, new Date())]).toContain(quoted!.charge_minor)
  const upgrade = paid(who, { plan: 'pro', amount, origin: 'subscription_update', start: now, end: new Date(before.period_end) })
  expect(await post(request, upgrade.body)).toMatchObject({ status: 200, outcome: 'applied' })

  await expect(page.getByTestId('plan-subscription')).toContainText('Professional', { timeout: 30_000 })
  const after = (await billingOf(page)).subscription!
  expect(after).toMatchObject({ plan: 'pro', status: 'active', period_start: before.period_start, period_end: before.period_end })
  expect(await planOf(page)).toBe('pro')
  expect(sql(`SELECT amount_minor FROM billing_events WHERE owner_id = '${who.id}' AND kind = 'upgraded'`)).toBe(String(amount))
  expect(await post(request, upgrade.body)).toMatchObject({ status: 200, outcome: 'duplicate' })
  expect(count('billing_events', who.id, "kind = 'upgraded'")).toBe(1)
})
