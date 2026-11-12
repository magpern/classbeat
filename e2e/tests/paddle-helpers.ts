import { createHmac, randomBytes } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import type { APIRequestContext, APIResponse, Browser, Page } from '@playwright/test'
import { expect, nextTotp, randomClientIp } from './helpers'
import { enrollTotp, newUser } from './m5-helpers'
import { sql } from './song-helpers'

// Shared by paddle-done-when.spec.ts (PB-6, #977). The spec runs against `web-paddle` (compose.e2e.yaml):
// the same image and database as `web`, with CLASSBEAT_BILLING_PROVIDER=fake_paddle, so a checkout goes to
// the fake's hosted page and the plan changes only when a signed notification lands (ADR 0032).
// The fake delivers its own events in process (Pay, Decline); the events the fake keeps to itself, and
// the ones that must be wrong, are built and signed here with the public test secret the stack mounts
// (`e2e/fake-paddle/webhook-secret.txt`, never a real one) and posted to the webhook like Paddle would.

export const paddleBaseURL =
  process.env.E2E_PADDLE_BASE_URL ?? `http://127.0.0.1:${process.env.WEB_PADDLE_PORT ?? '8002'}`

/** Context options of the Paddle spec: its own base URL, with the cookie notice dismissed there. */
export const paddleUse = {
  baseURL: paddleBaseURL,
  storageState: {
    cookies: [],
    origins: [
      { origin: new URL(paddleBaseURL).origin, localStorage: [{ name: 'cb-cookie-notice', value: '1' }] },
    ],
  },
}

export const WEBHOOK = '/api/billing/paddle/webhook'
export const PRICE = { instructor: 1000, pro: 2500 } // cents, USD: what the admin sets for this run

const SECRET = readFileSync(fileURLToPath(new URL('../fake-paddle/webhook-secret.txt', import.meta.url)), 'utf8').trim()

// --- signing and posting ----------------------------------------------------------------------

/** `Paddle-Signature`: `ts=<unix>;h1=<hex HMAC-SHA256 over "<ts>:<raw body>">` (ADR 0032 section 1). */
export function signature(body: string, ts: number = Math.floor(Date.now() / 1000), secret: string = SECRET): string {
  return `ts=${ts};h1=${createHmac('sha256', secret).update(`${ts}:${body}`).digest('hex')}`
}

export interface Answer {
  status: number
  outcome: string
  reason: string | null
}

/** Posts `body` as Paddle does: raw, signed, no session. Its own client address keeps the rate limit apart. */
export async function post(
  request: APIRequestContext,
  body: string,
  header: string = signature(body),
): Promise<Answer> {
  const answer = await request.post(WEBHOOK, {
    data: Buffer.from(body),
    headers: { 'Paddle-Signature': header, 'Content-Type': 'application/json', 'X-Forwarded-For': randomClientIp() },
  })
  const json = (await answer.json()) as { outcome: string; reason: string | null }
  return { status: answer.status(), outcome: json.outcome, reason: json.reason }
}

// --- the notifications -------------------------------------------------------------------------

export interface Buyer {
  page: Page
  email: string
  id: string
  customer: string
  subscription: string
}

const alnum = (): string => randomBytes(13).toString('hex') // 26 lowercase characters, as Paddle's ids

export const priceId = (plan: string): string => `pri_${Buffer.from(plan).toString('hex').slice(0, 64)}`

/** Seconds precision, `Z`: how the stored periods read back, so a boundary compares equal. */
export const iso = (when: Date): string => when.toISOString().replace('.000Z', 'Z')

/** `classbeat.billing.periods.period_end`: a month on, from the anchor day, clamped to the month's end. */
export function monthAfter(start: Date, anchorDay: number): Date {
  const year = start.getUTCMonth() === 11 ? start.getUTCFullYear() + 1 : start.getUTCFullYear()
  const month = (start.getUTCMonth() + 1) % 12
  const last = new Date(Date.UTC(year, month + 1, 0)).getUTCDate()
  const end = new Date(start)
  end.setUTCFullYear(year, month, Math.min(anchorDay, last))
  return end
}

interface Paid {
  plan: string
  amount: number
  origin: 'subscription_recurring' | 'subscription_update'
  start: Date
  end: Date
  /** The notification's own price amount (cents); the plan's price unless the test wants it wrong. */
  unit?: number
  eventId?: string
}

/** A `transaction.completed` for the buyer's subscription: a renewal, or an upgrade's proration charge. */
export function paid(buyer: Buyer, p: Paid): { id: string; body: string } {
  const id = p.eventId ?? `evt_${alnum()}`
  const price = {
    id: priceId(p.plan),
    product_id: 'pro_fakeclassbeat',
    description: 'Monthly',
    billing_cycle: { interval: 'month', frequency: 1 },
    trial_period: null,
    unit_price: { amount: String(p.unit ?? PRICE[p.plan as keyof typeof PRICE]), currency_code: 'USD' },
    status: 'active',
  }
  const span = { starts_at: iso(p.start), ends_at: iso(p.end) }
  const item: Record<string, unknown> = { price_id: price.id, price, quantity: 1 }
  // An upgrade's charge covers the rest of the period from the change; the transaction's own billing
  // period stays the subscription's. A renewal's is the new period.
  if (p.origin === 'subscription_update') item.proration = { rate: '1', billing_period: span }
  const doc = {
    event_id: id,
    event_type: 'transaction.completed',
    occurred_at: iso(new Date()),
    notification_id: `ntf_${alnum()}`,
    data: {
      id: `txn_${alnum()}`,
      status: 'completed',
      customer_id: buyer.customer,
      origin: p.origin,
      custom_data: { classbeat_owner_id: buyer.id },
      subscription_id: buyer.subscription,
      currency_code: 'USD',
      billing_period: span,
      items: [item],
      details: {
        totals: { subtotal: String(p.amount), discount: '0', tax: '0', total: String(p.amount), currency_code: 'USD' },
      },
    },
  }
  return { id, body: JSON.stringify(doc) }
}

// --- the database and the billing API ------------------------------------------------------------

export const count = (table: string, owner: string, where = 'true'): number =>
  Number(sql(`SELECT count(*) FROM ${table} WHERE owner_id = '${owner}' AND ${where}`))

export const refusalsOf = (eventId: string): number =>
  Number(sql(`SELECT count(*) FROM billing_webhook_refusals WHERE event_id = '${eventId}'`))

export interface Subscription {
  plan: string
  period_start: string
  period_end: string
  next_plan: string | null
  cancel_at_end: boolean
  status: string
}

export interface Offer {
  plan: string
  action: string | null
  charge_minor: number | null
}

export async function billingOf(page: Page): Promise<{ subscription: Subscription | null; offers: Offer[] }> {
  const answer = await page.request.get('/api/account/billing')
  expect(answer.status()).toBe(200)
  return (await answer.json()) as { subscription: Subscription | null; offers: Offer[] }
}

export async function planOf(page: Page): Promise<string> {
  const answer = await page.request.get('/api/account/plan')
  expect(answer.status()).toBe(200)
  return ((await answer.json()) as { plan: string }).plan
}

/** The buyer's active link to Paddle, as the first payment recorded it (the ids the notifications carry). */
export function linkOf(owner: string): { customer: string; subscription: string } {
  const [customer, subscription] = sql(
    `SELECT customer_ref, subscription_ref FROM billing_links WHERE owner_id = '${owner}' AND activated_at IS NOT NULL`,
  ).split('|')
  expect(customer, 'customer id of the link').toMatch(/^ctm_/)
  expect(subscription, 'subscription id of the link').toMatch(/^sub_/)
  return { customer, subscription }
}

// --- the plan screen -----------------------------------------------------------------------------

export async function openPlan(page: Page): Promise<void> {
  await page.goto('/account#plan')
  await expect(page.getByTestId('plan-options')).toBeVisible()
}

/** Choose `button` on the plan screen and confirm. */
export async function choose(page: Page, button: string | RegExp): Promise<void> {
  await page.getByRole('button', { name: button }).click()
  await expect(page.getByTestId('offer-confirm')).toBeVisible()
  await page.getByTestId('offer-confirm').getByRole('button', { name: 'Confirm' }).click()
}

/** Buy Individual up to the fake's hosted page, which the plan screen sends the buyer to. */
export async function toCheckoutPage(page: Page): Promise<string> {
  await openPlan(page)
  await choose(page, 'Buy Individual')
  await expect(page).toHaveURL(/\/fake-paddle\/checkout\/txn_[a-z0-9]+$/)
  return new URL(page.url()).pathname.split('/').pop()!
}

/** The whole purchase: the hosted page, Pay, back on the plan screen with the plan started. */
export async function buyIndividual(page: Page): Promise<void> {
  await toCheckoutPage(page)
  await page.getByRole('button', { name: 'Pay', exact: true }).click()
  await expect(page).toHaveURL(/\/account/)
  await expect(page.getByTestId('plan-subscription')).toContainText(/Individual, paid until/, { timeout: 30_000 })
}

// --- the people ------------------------------------------------------------------------------------

/** The admin's call, with a step-up (TOTP, good for 5 minutes) taken when the last one is old. */
export type AdminCall = (method: 'PATCH' | 'POST', path: string, data?: unknown) => Promise<APIResponse>

export function adminCaller(page: Page, origin: string, secret: string): AdminCall {
  let stepped = 0
  const headers = async (): Promise<Record<string, string>> => {
    const session = (await (await page.request.get('/api/auth/session')).json()) as { csrf_token: string }
    return { Origin: origin, 'X-CSRF-Token': session.csrf_token }
  }
  return async (method, path, data) => {
    if (Date.now() - stepped > 200_000) {
      const stepup = await page.request.post('/api/auth/mfa/stepup', {
        headers: await headers(),
        data: { code: await nextTotp(secret) },
      })
      expect(stepup.status()).toBe(200)
      stepped = Date.now()
    }
    return page.request.fetch(path, { method, headers: await headers(), data })
  }
}

/** An admin who sets the prices of this run (placeholders until then, so nothing can be bought). */
export async function adminWithPrices(browser: Browser, baseURL: string | undefined): Promise<AdminCall> {
  const admin = await newUser(browser, baseURL, 'pd-admin', false, true)
  const call = adminCaller(admin.page, new URL(baseURL!).origin, await enrollTotp(admin.page))
  for (const [plan, cents] of Object.entries(PRICE)) {
    const set = await call('PATCH', `/api/admin/plans/${plan}`, { monthly_price_cents: cents, currency: 'USD' })
    expect(set.status(), `price of ${plan}`).toBe(200)
  }
  return call
}

/** A signed-in user with the `billing_test` entitlement: billing is off, so only a tester may buy. */
export async function tester(browser: Browser, baseURL: string | undefined, admin: AdminCall, tag: string) {
  const made = await newUser(browser, baseURL, tag, false)
  const session = (await (await made.page.request.get('/api/auth/session')).json()) as { user: { id: string } }
  const grant = await admin('POST', `/api/admin/users/${session.user.id}/entitlements`, {
    features: ['billing_test'],
    ends_at: new Date(Date.now() + 30 * 86_400_000).toISOString(),
    note: 'e2e tester',
  })
  expect(grant.status()).toBe(201)
  return { page: made.page, email: made.email, id: session.user.id }
}

/** A tester who has bought Individual through the checkout, with the ids Paddle's notifications carry. */
export async function buyer(browser: Browser, baseURL: string | undefined, admin: AdminCall, tag: string): Promise<Buyer> {
  const who = await tester(browser, baseURL, admin, tag)
  await buyIndividual(who.page)
  const link = linkOf(who.id)
  return { ...who, customer: link.customer, subscription: link.subscription }
}

// --- upgrade amounts -------------------------------------------------------------------------------

/** The days the proration counts: whole UTC days, the day of the change included. */
const utcDay = (when: string | Date): number => {
  const d = new Date(when)
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) / 86_400_000
}

/** `classbeat.billing.subscription_policy.upgrade_amount`, written out so the spec checks the number itself. */
export function prorated(diff: number, sub: Subscription, now: Date): number {
  const left = Math.max(1, utcDay(sub.period_end) - utcDay(now))
  const inPeriod = utcDay(sub.period_end) - utcDay(sub.period_start)
  return Math.floor((2 * diff * left + inPeriod) / (2 * inPeriod))
}
