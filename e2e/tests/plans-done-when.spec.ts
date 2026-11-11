import type { APIResponse, Browser, BrowserContext, Page } from '@playwright/test'
import { expect, nextTotp, test } from './helpers'
import { PLAYLISTS } from './design-helpers'
import { holdHandoff, pickMusic, say } from './ai-first-helpers'
import { audit } from './mu-helpers'
import { useModelScript } from './model-script'
import { sql } from './song-helpers'
import { LONG, cancelJob, createDesign, endedAs, enrollTotp, heldOf, newUser } from './m5-helpers'

// M7a-7 (#952, ADR 0031, docs/plans/commercial.md "Done when (M7a)", items 1 to 8), on the compose
// stack with the fakes and the Dummy provider (billing stays off: a tester or an admin may buy).
// The admin sets the prices first, through the admin API (placeholders until then, so nothing can be
// bought). The Plan screen is driven through the UI; the admin's tester grant and fast-forward have
// no screen, so they are admin API calls from an admin browser session with its step-up. The
// backend suites pin the same rules one layer down (test_billing_*.py); this is the whole path.
test.describe.configure({ mode: 'serial' })
test.setTimeout(300_000)

const PRICE = { instructor: 1000, pro: 2500 } // cents, USD: placeholders the admin sets for this run

interface Who {
  page: Page
  context: BrowserContext
  email: string
  connectionId: string
  id: string
}

interface Subscription {
  plan: string
  period_start: string
  period_end: string
  next_plan: string | null
  cancel_at_end: boolean
  status: string
}

interface PlanView {
  plan: string
  source: string | null
  features: string[]
  limits: { classes_per_month: number; max_class_minutes: number }
  gates: { games: boolean; races: boolean; ftp_tests: boolean }
}

let origin = ''
let adminPage: Page
let adminCall: (method: 'PATCH' | 'POST', path: string, data?: unknown) => Promise<APIResponse>
let free: Who
let tester: Who
let key = 0

// --- small helpers ------------------------------------------------------------------------

async function who(browser: Browser, baseURL: string | undefined, tag: string): Promise<Who> {
  const made = await newUser(browser, baseURL, tag)
  const session = (await (await made.page.request.get('/api/auth/session')).json()) as { user: { id: string } }
  return { ...made, id: session.user.id }
}

async function csrf(page: Page): Promise<Record<string, string>> {
  const session = (await (await page.request.get('/api/auth/session')).json()) as { csrf_token: string }
  return { Origin: origin, 'X-CSRF-Token': session.csrf_token }
}

function idempotencyKey(tag: string): string {
  key += 1
  return `${tag}-${Date.now()}-${key}-${Math.random().toString(36).slice(2, 8)}`
}

async function planOf(page: Page): Promise<PlanView> {
  const answer = await page.request.get('/api/account/plan')
  expect(answer.status()).toBe(200)
  return (await answer.json()) as PlanView
}

async function billingOf(page: Page): Promise<{ can_buy: boolean; subscription: Subscription | null }> {
  const answer = await page.request.get('/api/account/billing')
  expect(answer.status()).toBe(200)
  return (await answer.json()) as { can_buy: boolean; subscription: Subscription | null }
}

/** `POST /api/jobs` for a design with `brief` fields on top of a plain 30 minute class. */
async function postDesign(who: Who, brief: Record<string, unknown>): Promise<{ status: number; body: Record<string, any> }> {
  const answer = await who.page.request.post('/api/jobs', {
    headers: { ...(await csrf(who.page)), 'Idempotency-Key': idempotencyKey('p7job') },
    data: {
      kind: 'design_draft',
      input: {
        title: 'E2E plans',
        brief: { length_min: 30, intensity: 'moderate', climbs: 1, sprints: 2, races: 0, ftp_test: null, style: '', text: '', ...brief },
        music: { source: 'mowl_spotify_playlist', spotify_playlist_id: PLAYLISTS.draft.id },
        mowl_connection_id: who.connectionId,
      },
    },
  })
  return { status: answer.status(), body: (await answer.json()) as Record<string, any> }
}

/**
 * Design a plain class on the recorded model, wait for it, and return the route the app stored on
 * the job at admission (`jobs.input.route_key`, a server field: the plan's route). `agent_runs.route_key`
 * is only written when an admin has set a model route for it (with the bootstrap model it is null), so
 * the job's own record is what follows the plan here.
 */
async function routeOfADesign(who: Who): Promise<string[]> {
  useModelScript('good_first_draft')
  const made = await createDesign(who.context, who.connectionId)
  expect(made.status).toBe(201)
  expect(await endedAs(who.page, made.body.id!)).toBe('completed')
  const routes = sql(`SELECT DISTINCT input->>'route_key' FROM jobs WHERE id = '${made.body.id}'`)
  return routes.split('\n').filter((line) => line !== '')
}

/** The admin's call, with a step-up (TOTP, good for 5 minutes) taken when the last one is old. */
function adminCaller(page: Page, secret: string): typeof adminCall {
  let stepped = 0
  return async (method, path, data) => {
    if (Date.now() - stepped > 200_000) {
      const stepup = await page.request.post('/api/auth/mfa/stepup', {
        headers: await csrf(page),
        data: { code: await nextTotp(secret) },
      })
      expect(stepup.status()).toBe(200)
      stepped = Date.now()
    }
    return page.request.fetch(path, { method, headers: await csrf(page), data })
  }
}

/** The admin's tester grant (ADR 0031 section 6): `billing_test`, ending in 30 days. */
async function makeTester(user: Who): Promise<void> {
  const grant = await adminCall('POST', `/api/admin/users/${user.id}/entitlements`, {
    features: ['billing_test'],
    ends_at: new Date(Date.now() + 30 * 86_400_000).toISOString(),
    note: 'e2e tester',
  })
  expect(grant.status()).toBe(201)
}

async function fastForward(user: Who): Promise<{ outcome: string; plan: string }> {
  const answer = await adminCall('POST', `/api/admin/users/${user.id}/billing/fast-forward`)
  expect(answer.status()).toBe(200)
  return (await answer.json()) as { outcome: string; plan: string }
}

/** The days the dummy's proration counts (whole UTC days, the day of the change included). */
const utcDay = (when: string | Date): number => {
  const d = new Date(when)
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) / 86_400_000
}

/** `classbeat.billing.subscription_policy.upgrade_amount`, written out so the spec checks the number itself. */
function prorated(diff: number, sub: Subscription, now: Date): number {
  const left = Math.max(1, utcDay(sub.period_end) - utcDay(now))
  const inPeriod = utcDay(sub.period_end) - utcDay(sub.period_start)
  return Math.floor((2 * diff * left + inPeriod) / (2 * inPeriod))
}

const digits = (text: string | null): string => (text ?? '').replace(/\D/g, '')

async function openPlan(page: Page): Promise<void> {
  await page.goto('/account#plan')
  await expect(page.getByTestId('plan-options')).toBeVisible()
}

/** Choose `button` on the plan screen and confirm; returns the note shown. */
async function choose(page: Page, button: string | RegExp): Promise<void> {
  await page.getByRole('button', { name: button }).click()
  await expect(page.getByTestId('offer-confirm')).toBeVisible()
  await page.getByTestId('offer-confirm').getByRole('button', { name: 'Confirm' }).click()
}

// --- the done-when -------------------------------------------------------------------------

test('setup: the admin sets the prices and a tester is made', async ({ browser, baseURL }) => {
  origin = new URL(baseURL!).origin
  const admin = await newUser(browser, baseURL, 'p7-admin', false, true)
  adminPage = admin.page
  adminCall = adminCaller(adminPage, await enrollTotp(adminPage))
  for (const [plan, cents] of Object.entries(PRICE)) {
    const set = await adminCall('PATCH', `/api/admin/plans/${plan}`, { monthly_price_cents: cents, currency: 'USD' })
    expect(set.status(), `price of ${plan}`).toBe(200)
  }
  free = await who(browser, baseURL, 'p7-free')
  tester = await who(browser, baseURL, 'p7-tester')
  await makeTester(tester)
})

test('1. Free is told why before any job, and offered Individual', async () => {
  const plan = await planOf(free.page)
  expect(plan.plan).toBe('free')
  expect(plan.limits.max_class_minutes).toBe(30)
  expect(plan.gates).toMatchObject({ games: false, ftp_tests: false })

  // The server refuses a class over 30 minutes, a game and an FTP test before anything is created.
  const long = await postDesign(free, { length_min: 45 })
  expect(long.status).toBe(409)
  expect(long.body.detail).toMatchObject({ reason: 'quota', quota: 'length', limit: 30, used: 45, plan: 'free' })
  const game = await postDesign(free, { games: [{ type: 'any' }] })
  expect(game.status).toBe(409)
  expect(game.body.detail).toMatchObject({ reason: 'quota', quota: 'feature', feature: 'games', plan: 'free' })
  const ftp = await postDesign(free, { ftp_test: 20 })
  expect(ftp.status).toBe(409)
  expect(ftp.body.detail).toMatchObject({ reason: 'quota', quota: 'feature', feature: 'ftp_tests', plan: 'free' })
  expect(sql(`SELECT count(*) FROM jobs WHERE owner_id = '${free.id}' AND kind = 'design_draft'`)).toBe('0')
  expect(await heldOf(free.page)).toBe(0)

  // In words: a request with a game stops at "Part of your request is over your plan", names the
  // reason, offers Individual, and the design job has not started.
  useModelScript('ai_first_read_pull')
  const held = await holdHandoff(free.page) // the hand-off waits for the choice, so nothing designs
  await say(free.page, 'A 30 minute ride with two climbs and a Power Pull after climb 2.')
  await pickMusic(free.page, PLAYLISTS.draft.name)
  await free.page.getByRole('button', { name: 'Design my class' }).click()
  const choice = free.page.getByTestId('plan-choice')
  await expect(choice).toBeVisible(LONG)
  await expect(choice).toContainText('Part of your request is over your plan.')
  await expect(choice).toContainText('Your plan does not include games.')
  await expect(choice).toContainText('Individual allows all of it.')
  expect(await heldOf(free.page)).toBe(0)
  held.release()
  await choice.getByRole('link', { name: 'See Individual' }).click()
  await expect(free.page).toHaveURL(/\/account#plan$/)
  expect(sql(`SELECT count(*) FROM jobs WHERE owner_id = '${free.id}' AND kind = 'design_draft'`)).toBe('0')
})

test('2. a tester buys Individual: 75 minutes, games and 9 classes work at once; the period end shows', async () => {
  const page = tester.page
  expect((await planOf(page)).plan).toBe('free')
  await openPlan(page)
  await expect(page.getByTestId('plan-options')).toContainText('Test payments only.')
  const offer = page.getByTestId('offer-instructor')
  await expect(offer).toContainText(/10[.,]00/)
  await choose(page, 'Buy Individual')
  await expect(page.getByText('Done. Your plan is now Individual.')).toBeVisible()
  await expect(page.getByTestId('plan-name')).toContainText('Individual')

  const sub = (await billingOf(page)).subscription!
  expect(sub).toMatchObject({ plan: 'instructor', status: 'active', next_plan: null, cancel_at_end: false })
  const end = new Date(sub.period_end).getUTCDate()
  await expect(page.getByTestId('plan-subscription')).toContainText(/Individual, paid until/)
  await expect(page.getByTestId('plan-subscription')).toContainText(String(end))

  const plan = await planOf(page)
  expect(plan).toMatchObject({ plan: 'instructor', source: 'subscription' })
  expect(plan.limits).toMatchObject({ classes_per_month: 9, max_class_minutes: 75 })
  expect(plan.gates).toMatchObject({ games: true, ftp_tests: true })
  // A 75 minute class with a game is admitted now (and cancelled: this step is about the admission).
  const admitted = await postDesign(tester, { length_min: 75, games: [{ type: 'any' }] })
  expect(admitted.status).toBe(201)
  await cancelJob(page, admitted.body.id as string)
  await endedAs(page, admitted.body.id as string)
  expect(await routeOfADesign(tester)).toEqual(['instructor'])
})

test('3. upgrading to Professional charges the prorated difference, keeps the period end, limits apply at once', async () => {
  const page = tester.page
  const before = (await billingOf(page)).subscription!
  await openPlan(page)
  const quote = page.getByTestId('quote-pro')
  await expect(quote).toBeVisible()
  const around = new Set([prorated(PRICE.pro - PRICE.instructor, before, new Date())])
  const shown = digits(await quote.textContent())
  await choose(page, 'Upgrade to Professional')
  await expect(page.getByText('Done. Your plan is now Professional.')).toBeVisible()
  around.add(prorated(PRICE.pro - PRICE.instructor, before, new Date())) // a UTC midnight in between
  expect([...around].map(String)).toContain(shown)

  const charged = sql(`SELECT amount_minor FROM billing_events WHERE owner_id = '${tester.id}' AND kind = 'upgraded'`)
  expect([...around].map(String)).toContain(charged)
  const after = (await billingOf(page)).subscription!
  expect(after).toMatchObject({ plan: 'pro', status: 'active' })
  expect(after.period_end).toBe(before.period_end)
  expect(after.period_start).toBe(before.period_start)
  const plan = await planOf(page)
  expect(plan.plan).toBe('pro')
  expect(plan.limits).toMatchObject({ classes_per_month: 30, max_class_minutes: 180 })
  expect(await routeOfADesign(tester)).toEqual(['pro'])
})

test('4. downgrade and cancel change nothing until the period ends; then Free, and no refund exists', async () => {
  const page = tester.page
  // Downgrade: scheduled, not applied.
  await openPlan(page)
  await choose(page, 'Switch to Individual at period end')
  await expect(page.getByText('Done. The change takes effect when your period ends.')).toBeVisible()
  await expect(page.getByTestId('plan-subscription')).toContainText('then you return to Individual')
  expect((await planOf(page)).plan).toBe('pro')
  expect((await billingOf(page)).subscription).toMatchObject({ plan: 'pro', next_plan: 'instructor' })
  // The admin ends the period: Individual from now on, and the new period is paid at its price.
  expect(await fastForward(tester)).toMatchObject({ outcome: 'renewed', plan: 'instructor' })
  expect((await planOf(page)).plan).toBe('instructor')

  // Cancel: scheduled, not applied.
  await openPlan(page)
  await page.getByRole('button', { name: 'Cancel subscription' }).click()
  await expect(page.getByTestId('confirm-text')).toContainText('Money already paid is not refunded.')
  await page.getByTestId('offer-confirm').getByRole('button', { name: 'Confirm' }).click()
  await expect(page.getByText('Done. The change takes effect when your period ends.')).toBeVisible()
  await expect(page.getByTestId('plan-subscription')).toContainText('After that you are on Free.')
  expect((await planOf(page)).plan).toBe('instructor')
  expect((await billingOf(page)).subscription).toMatchObject({ plan: 'instructor', cancel_at_end: true })
  expect(await fastForward(tester)).toMatchObject({ outcome: 'ended' }) // its `plan` is the plan that ended; the plan screen below shows Free
  expect(await planOf(page)).toMatchObject({ plan: 'free', source: null })
  await page.reload()
  await expect(page.getByTestId('plan-name')).toContainText('Free')

  // No refund exists anywhere: the ledger has no refund kind, no negative amount, and a downgrade or
  // a cancel charged nothing.
  const kinds = sql(`SELECT string_agg(DISTINCT kind, ',' ORDER BY kind) FROM billing_events WHERE owner_id = '${tester.id}'`)
  expect(kinds).toBe('change_scheduled,ended,renewed,started,upgraded')
  expect(sql(`SELECT count(*) FROM billing_events WHERE owner_id = '${tester.id}' AND kind IN ('change_scheduled', 'ended') AND coalesce(amount_minor, 0) <> 0`)).toBe('0')
})

test('5. an admin is Professional with no entitlement', async () => {
  const plan = await planOf(adminPage)
  expect(plan).toMatchObject({ plan: 'pro', source: null })
  const me = (await (await adminPage.request.get('/api/auth/session')).json()) as { user: { id: string } }
  expect(sql(`SELECT count(*) FROM entitlements WHERE owner_id = '${me.user.id}'`)).toBe('0')
  // The plan gives usage limits only: the role grants no song picking feature.
  expect(plan.features).not.toContain('song_picking')
  await openPlan(adminPage)
  await expect(adminPage.getByTestId('plan-name')).toContainText('Professional')
  expect((await billingOf(adminPage)).can_buy).toBe(true)
})

test('5b. an ordinary user gets 403 on the billing routes and no log row, no event, no subscription', async () => {
  const page = free.page
  const get = await page.request.get('/api/account/billing')
  expect(get.status()).toBe(403)
  expect(await get.json()).toEqual({ detail: { reason: 'billing_closed' } })
  const headers = { ...(await csrf(page)), 'Idempotency-Key': idempotencyKey('p7closed') }
  const buy = await page.request.post('/api/account/billing/checkout', { headers, data: { plan: 'instructor', expected_amount_minor: PRICE.instructor } })
  expect(buy.status()).toBe(403)
  expect(await buy.json()).toEqual({ detail: { reason: 'billing_closed' } })
  const change = await page.request.post('/api/account/billing/change', { headers, data: { plan: 'free' } })
  expect(change.status()).toBe(403)
  // The admin routes are the admin's: an ordinary user is refused there too.
  expect((await page.request.get('/api/admin/billing')).status()).toBe(403)
  for (const table of ['billing_events', 'subscriptions', 'billing_log']) {
    expect(sql(`SELECT count(*) FROM ${table} WHERE owner_id = '${free.id}'`), table).toBe('0')
  }
  // The Plan screen shows no price and no buy action.
  await openPlan(page)
  await expect(page.getByTestId('plan-options')).toContainText('Plans are not open yet.')
  await expect(page.getByTestId('plan-options').getByRole('button')).toHaveCount(0)
})

test('6. a replayed checkout and a replayed change are no-ops', async ({ browser, baseURL }) => {
  const other = await who(browser, baseURL, 'p7-replay')
  await makeTester(other)
  const page = other.page
  const buy = { headers: { ...(await csrf(page)), 'Idempotency-Key': idempotencyKey('p7buy') }, data: { plan: 'instructor', expected_amount_minor: PRICE.instructor } }
  const first = await page.request.post('/api/account/billing/checkout', buy)
  expect(first.status()).toBe(200)
  const replay = await page.request.post('/api/account/billing/checkout', buy)
  expect(replay.status()).toBe(200)
  expect(((await replay.json()) as { subscription: Subscription }).subscription).toEqual(((await first.json()) as { subscription: Subscription }).subscription)
  expect(sql(`SELECT count(*) FROM billing_events WHERE owner_id = '${other.id}' AND kind = 'started'`)).toBe('1')

  const cancel = { headers: { ...(await csrf(page)), 'Idempotency-Key': idempotencyKey('p7cancel') }, data: { plan: 'free' } }
  expect((await page.request.post('/api/account/billing/change', cancel)).status()).toBe(200)
  expect((await page.request.post('/api/account/billing/change', cancel)).status()).toBe(200)
  expect(sql(`SELECT count(*) FROM billing_events WHERE owner_id = '${other.id}' AND kind = 'change_scheduled'`)).toBe('1')
  // A price the page did not show is refused, nothing charged (a stale quote).
  const stale = await page.request.post('/api/account/billing/checkout', {
    headers: { ...(await csrf(page)), 'Idempotency-Key': idempotencyKey('p7stale') },
    data: { plan: 'pro', expected_amount_minor: 1 },
  })
  expect(stale.status()).toBe(409)
  expect(sql(`SELECT count(*) FROM billing_events WHERE owner_id = '${other.id}' AND kind = 'upgraded'`)).toBe('0')
})

test('7. song picking and the connections are the same on every plan; the job records its route', async () => {
  // Song picking is no plan feature: the entitlement features a plan shows are the same on Free,
  // Individual and Professional (none, until the owner grants song picking on its own), and the
  // routes the jobs above recorded are the plan ids (steps 2 and 3).
  const features = [
    (await planOf(free.page)).features,
    (await planOf(tester.page)).features,
    (await planOf(adminPage)).features,
  ]
  expect(features[0]).toEqual(features[1])
  expect(features[1]).toEqual(features[2])
  for (const user of [free, tester]) {
    const connections = await user.page.request.get('/api/connections')
    expect(connections.status()).toBe(200)
  }
})

for (const lang of ['sv', 'da'] as const) {
  test(`8. the Plan screen renders in ${lang} for a tester (axe at 1440 and 390)`, async () => {
    const page = tester.page
    await page.goto(`/${lang}/account#plan`)
    await expect(page.locator('html')).toHaveAttribute('lang', lang)
    const options = page.getByTestId('plan-options')
    await expect(options).toBeVisible()
    for (const id of ['free', 'instructor', 'pro']) await expect(page.getByTestId(`plan-card-${id}`)).toBeVisible()
    await expect(page.getByTestId('offer-instructor')).toBeVisible()
    // No placeholder leaks into the words, whatever the catalogue holds for this language.
    expect(await options.textContent()).not.toMatch(/\{\w+\}/)
    const found = await audit(page, `/${lang}/account#plan`, page.getByTestId('plan-card-instructor'))
    expect(found.problems).toEqual([])
  })
}

test('8. axe at 1440 and 390 on the Plan screen: a tester with a purchase open, and a non-tester', async () => {
  const page = tester.page
  await openPlan(page)
  await page.getByRole('button', { name: 'Buy Individual' }).click()
  await expect(page.getByTestId('offer-confirm')).toBeVisible()
  const open = await audit(page, '/account#plan (tester, buy open)', page.getByTestId('offer-confirm'))
  expect(open.problems).toEqual([])
  await openPlan(free.page)
  const closed = await audit(free.page, '/account#plan (not open)', free.page.getByTestId('plan-options'))
  expect(closed.problems).toEqual([])
})

test('teardown: the signed-in contexts close', async () => {
  for (const user of [free, tester]) await user.context.close()
  await adminPage.context().close()
})
