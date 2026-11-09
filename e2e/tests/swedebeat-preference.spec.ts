import { type Page } from '@playwright/test'
import { connectMowl } from './design-helpers'
import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import { audit, stackContext, type Findings } from './mu-helpers'
import {
  EMAIL_A,
  PASSWORD_A,
  fakeState,
  fillSwedebeat,
  openSwedebeatDialog,
  resetSwedebeat,
  swedebeatRow,
} from './swedebeat-helpers'
import { ownerIdForEmail, resetSwedebeatMixes, sql, swedebeatMixState } from './swedebeat-mix-helpers'

// The per-user "I use Swedebeat" preference (#525, ADR 0015 amendment of 2026-11-03) behind the
// Connections list (ADR 0028) on the e2e stack's fakes: the row is always listed; without the
// preference nothing Swedebeat shows and the mix listing makes no MOWL call; Connect stores it,
// Disconnect turns it off, a dialog closed without connecting puts it back; the admin flag stored
// `off` wins over it. axe audits the account page. Public test values only.

test.setTimeout(120_000)

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
  const result = await audit(page, name, swedebeatRow(page), 'main')
  findings.push(result)
  expect.soft(result.problems, name).toEqual([])
  await page.setViewportSize({ width: 1280, height: 720 })
}

const preferenceOn = (owner: string) =>
  sql(`SELECT count(*) FROM user_preferences WHERE owner_id = '${owner}' AND uses_swedebeat`)

test('listed from the start; Connect turns the preference on, Disconnect turns it off', async ({ page }) => {
  const email = await newUser(page, 'sb-pref')
  const owner = ownerIdForEmail(email)

  await test.step('not connected: listed, but no mix choice and no listing call', async () => {
    await expect(swedebeatRow(page)).toContainText('Optional')
    await expect(swedebeatRow(page).getByRole('button', { name: 'Connect Swedebeat' })).toBeVisible()
    await check(page, 'Account, Swedebeat not connected')
    const listed = await page.request.get('/api/music/swedebeat-mixes')
    expect(listed.status()).toBe(404)
    expect(await listed.json()).toEqual({ detail: { reason: 'feature_off' } })
    expect(swedebeatMixState().lists).toBe(0) // no MOWL call
    await page.goto('/classes/new')
    const form = page.getByRole('form', { name: 'New class' })
    await expect(form.getByLabel('Class name')).toBeVisible()
    await expect(form.getByRole('button', { name: 'Use my Swedebeat mix' })).toHaveCount(0)
    expect(swedebeatMixState().lists).toBe(0)
    expect(preferenceOn(owner)).toBe('0')
  })

  await test.step('a dialog closed without connecting leaves the preference off', async () => {
    const dialog = await openSwedebeatDialog(page)
    await dialog.getByRole('button', { name: 'Close' }).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    expect(preferenceOn(owner)).toBe('0')
    expect(fakeState().logins).toEqual([])
  })

  await test.step('connected: the preference is stored and the mix choice appears', async () => {
    const dialog = await openSwedebeatDialog(page)
    await fillSwedebeat(dialog, EMAIL_A, PASSWORD_A)
    await dialog.getByRole('button', { name: 'Connect Swedebeat' }).click()
    await expect(swedebeatRow(page).getByTestId('swedebeat-status')).toHaveText('Connected')
    await check(page, 'Account, Swedebeat connected')
    await page.reload()
    await expect(swedebeatRow(page).getByTestId('swedebeat-status')).toHaveText('Connected') // from the server
    expect(sql(`SELECT uses_swedebeat FROM user_preferences WHERE owner_id = '${owner}'`)).toBe('t')
    await page.goto('/classes/new')
    const form = page.getByRole('form', { name: 'New class' })
    await expect(form.getByRole('button', { name: 'Use my Swedebeat mix' })).toBeVisible()
    expect(swedebeatMixState().lists).toBeGreaterThan(0)
  })

  await test.step('disconnected: the preference is off again', async () => {
    await page.goto('/account')
    await swedebeatRow(page).getByRole('button', { name: 'Disconnect Swedebeat' }).click()
    await page
      .getByRole('dialog', { name: 'Disconnect Swedebeat?' })
      .getByRole('button', { name: 'Yes, disconnect Swedebeat' })
      .click()
    await expect(swedebeatRow(page).getByRole('button', { name: 'Connect Swedebeat' })).toBeVisible()
    await expect.poll(() => preferenceOn(owner)).toBe('0')
    expect((await page.request.get('/api/music/swedebeat-mixes')).status()).toBe(404)
  })

  await test.step('the admin flag stored off wins: the row says so and offers nothing', async () => {
    sql(`INSERT INTO app_settings (key, value) VALUES ('swedebeat_enabled', '"off"'::jsonb) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`)
    const before = swedebeatMixState().lists
    expect((await page.request.get('/api/music/swedebeat-mixes')).status()).toBe(404)
    await page.goto('/account')
    await expect(page.getByRole('heading', { name: 'Connections' })).toBeVisible()
    await expect(swedebeatRow(page)).toContainText('Not available right now')
    await expect(swedebeatRow(page).getByRole('button')).toHaveCount(0)
    await page.goto('/classes/new')
    await expect(page.getByRole('form', { name: 'New class' }).getByLabel('Class name')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Use my Swedebeat mix' })).toHaveCount(0)
    expect(swedebeatMixState().lists).toBe(before)
  })
})

test('another user is not affected by one user connecting', async ({ browser, baseURL }) => {
  const first = await stackContext(browser, baseURL!)
  const second = await stackContext(browser, baseURL!)
  try {
    const one = await first.newPage()
    const two = await second.newPage()
    await newUser(one, 'sb-pref-one')
    await newUser(two, 'sb-pref-two')
    const dialog = await openSwedebeatDialog(one)
    await fillSwedebeat(dialog, EMAIL_A, PASSWORD_A)
    await dialog.getByRole('button', { name: 'Connect Swedebeat' }).click()
    await expect(swedebeatRow(one).getByTestId('swedebeat-status')).toHaveText('Connected')
    await two.reload()
    await expect(swedebeatRow(two).getByRole('button', { name: 'Connect Swedebeat' })).toBeVisible()
    expect((await two.request.get('/api/music/swedebeat-mixes')).status()).toBe(404)
    expect((await one.request.get('/api/music/swedebeat-mixes')).status()).toBe(200)
  } finally {
    await first.close()
    await second.close()
  }
})
