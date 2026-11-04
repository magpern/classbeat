import { type Page } from '@playwright/test'
import { connectMowl } from './design-helpers'
import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import { audit, stackContext, type Findings } from './mu-helpers'
import { fakeState, resetSwedebeat } from './swedebeat-helpers'
import { ownerIdForEmail, resetSwedebeatMixes, sql, swedebeatMixState } from './swedebeat-mix-helpers'

// The per-user "I use Swedebeat" preference (#525, ADR 0015 amendment of 2026-11-03) on the e2e
// stack's fakes: default off, nothing Swedebeat shows and the mix listing makes no MOWL call; on,
// the card's form and the mix choice appear; the admin flag stored `off` wins over it. axe audits
// the account page in both states. Public test values only.

test.setTimeout(120_000)

const card = (page: Page) => page.getByRole('region', { name: 'Swedebeat', exact: true })
const toggle = (page: Page) => card(page).getByRole('switch', { name: 'I use Swedebeat' })
const findings: Findings[] = []

test.beforeEach(() => {
  resetSwedebeat()
  resetSwedebeatMixes()
  sql("DELETE FROM app_settings WHERE key = 'swedebeat_enabled'")
})

test.afterAll(() => {
  sql("DELETE FROM app_settings WHERE key = 'swedebeat_enabled'")
  resetSwedebeat()
})

async function newUser(page: Page, tag: string): Promise<string> {
  const email = uniqueEmail(tag)
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await connectMowl(page)
  await page.goto('/account')
  return email
}

async function check(page: Page, name: string): Promise<void> {
  const result = await audit(page, name, toggle(page), 'main')
  findings.push(result)
  expect.soft(result.problems, name).toEqual([])
  await page.setViewportSize({ width: 1280, height: 720 })
}

test('off by default: no form, no mix choice, no listing call; on: the form and the mix choice', async ({ page }) => {
  const email = await newUser(page, 'sb-pref')
  const owner = ownerIdForEmail(email)

  await test.step('default off', async () => {
    await expect(toggle(page)).toHaveAttribute('aria-checked', 'false')
    await expect(card(page).getByLabel('Swedebeat email')).toHaveCount(0)
    await check(page, 'Account, Swedebeat off')
    const listed = await page.request.get('/api/music/swedebeat-mixes')
    expect(listed.status()).toBe(404)
    expect(await listed.json()).toEqual({ detail: { reason: 'feature_off' } })
    expect(swedebeatMixState().lists).toBe(0) // no MOWL call
    await page.goto('/classes/new')
    const form = page.getByRole('form', { name: 'New class' })
    await expect(form.getByLabel('Class name')).toBeVisible()
    await expect(form.getByRole('button', { name: 'Use my Swedebeat mix' })).toHaveCount(0)
    expect(swedebeatMixState().lists).toBe(0)
    expect(sql(`SELECT count(*) FROM user_preferences WHERE owner_id = '${owner}'`)).toBe('0')
  })

  await test.step('turned on', async () => {
    await page.goto('/account')
    await toggle(page).click()
    await expect(toggle(page)).toHaveAttribute('aria-checked', 'true')
    await expect(card(page).getByLabel('Swedebeat email')).toBeVisible()
    await check(page, 'Account, Swedebeat on')
    await page.reload()
    await expect(toggle(page)).toHaveAttribute('aria-checked', 'true') // stored on the server
    expect(sql(`SELECT uses_swedebeat FROM user_preferences WHERE owner_id = '${owner}'`)).toBe('t')
    await page.goto('/classes/new')
    const form = page.getByRole('form', { name: 'New class' })
    await expect(form.getByRole('button', { name: 'Use my Swedebeat mix' })).toBeVisible()
    expect(swedebeatMixState().lists).toBeGreaterThan(0)
  })

  await test.step('the admin flag stored off wins over the preference', async () => {
    sql(`INSERT INTO app_settings (key, value) VALUES ('swedebeat_enabled', '"off"'::jsonb) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`)
    const before = swedebeatMixState().lists
    expect((await page.request.get('/api/music/swedebeat-mixes')).status()).toBe(404)
    await page.goto('/account')
    await expect(page.getByRole('heading', { name: 'Your account' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'MOWL (Intelligent Cycling)' })).toBeVisible()
    await expect(card(page)).toHaveCount(0)
    await page.goto('/classes/new')
    await expect(page.getByRole('form', { name: 'New class' }).getByLabel('Class name')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Use my Swedebeat mix' })).toHaveCount(0)
    expect(swedebeatMixState().lists).toBe(before)
    expect(fakeState().logins).toEqual([])
  })
})

test('another user is not affected by one user turning it on', async ({ browser, baseURL }) => {
  const first = await stackContext(browser, baseURL!)
  const second = await stackContext(browser, baseURL!)
  try {
    const one = await first.newPage()
    const two = await second.newPage()
    await newUser(one, 'sb-pref-one')
    await newUser(two, 'sb-pref-two')
    await toggle(one).click()
    await expect(toggle(one)).toHaveAttribute('aria-checked', 'true')
    await two.reload()
    await expect(toggle(two)).toHaveAttribute('aria-checked', 'false')
    expect((await two.request.get('/api/music/swedebeat-mixes')).status()).toBe(404)
    expect((await one.request.get('/api/music/swedebeat-mixes')).status()).toBe(200)
  } finally {
    await first.close()
    await second.close()
  }
})
