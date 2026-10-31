import { type Page } from '@playwright/test'
import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import {
  EMAIL_A,
  EMAIL_B,
  PASSWORD_A,
  PASSWORD_B,
  PHONE,
  fakeState,
  holdSession,
  resetSwedebeat,
  setOutage,
} from './swedebeat-helpers'

// Swedebeat connect end to end (ADR 0015) against the fake Swedebeat of compose.e2e.yaml, which
// answers as www.swedebeat.se inside the compose network and decodes the library's AES-encoded
// login fields with a TEST key (e2e/fake-swedebeat/app.json, not Swedebeat's). Public test values
// only: no real Swedebeat account and no real key.

test.beforeEach(() => {
  resetSwedebeat()
})

test.afterAll(() => {
  resetSwedebeat()
})

async function newUser(page: Page, tag: string): Promise<void> {
  const email = uniqueEmail(tag)
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email) // a fresh sign-in counts as recent authentication
}

const card = (page: Page) => page.getByRole('region', { name: 'Swedebeat', exact: true })

async function fill(page: Page, email: string, password: string): Promise<void> {
  const swedebeat = card(page)
  await swedebeat.getByLabel('Swedebeat email').fill(email)
  await swedebeat.getByLabel('Swedebeat password').fill(password)
}

test('connect, wrong password, status after a reload, and disconnect', async ({ page }) => {
  await newUser(page, 'sb-connect')
  const swedebeat = card(page)

  await fill(page, EMAIL_A, 'not-the-password')
  await swedebeat.getByRole('button', { name: 'Connect Swedebeat' }).click()
  await expect(swedebeat.getByRole('alert')).toHaveText('Swedebeat did not accept that email and password.')
  await expect(swedebeat.getByLabel('Swedebeat password')).toHaveValue('')

  await swedebeat.getByLabel('Swedebeat password').fill(PASSWORD_A)
  const connected = page.waitForResponse((r) => r.url().endsWith('/api/connections/swedebeat'))
  await swedebeat.getByRole('button', { name: 'Connect Swedebeat' }).click()
  const answer = await connected
  expect(answer.status()).toBe(200)
  expect(new URL(answer.url()).search).toBe('') // nothing in the URL
  const text = await answer.text()
  expect(text).not.toContain(PASSWORD_A)
  expect(text).not.toMatch(/token|device/i) // the answer holds no secret
  await expect(swedebeat.getByText('Swedebeat is connected.')).toBeVisible()
  await expect(swedebeat.getByTestId('swedebeat-status')).toHaveText('Connected')
  await expect(swedebeat.getByTestId('swedebeat-account')).toHaveText(EMAIL_A)
  await expect(swedebeat.getByLabel('Swedebeat password')).toHaveCount(0)

  // The fake saw a decoded, correctly encoded login without force.
  const logins = fakeState().logins
  expect(logins.map((l) => [l.email, l.force, l.ok])).toEqual([
    [EMAIL_A, false, false],
    [EMAIL_A, false, true],
  ])

  // The status survives a reload: it comes from the server, and the password is nowhere.
  await page.reload()
  await expect(card(page).getByTestId('swedebeat-status')).toHaveText('Connected')
  expect(await page.content()).not.toContain(PASSWORD_A)

  await card(page).getByRole('button', { name: 'Disconnect Swedebeat' }).click()
  await card(page).getByRole('button', { name: 'Yes, disconnect Swedebeat' }).click()
  await expect(card(page).getByText(/Swedebeat is disconnected/)).toBeVisible()
  await expect(card(page).getByRole('button', { name: 'Connect Swedebeat' })).toBeVisible()
  await expect(card(page).getByTestId('swedebeat-status')).toHaveCount(0)
})

test('another device holds the session: asked first, then signed in here anyway', async ({ page }) => {
  await newUser(page, 'sb-device')
  holdSession(EMAIL_A)
  const swedebeat = card(page)

  await fill(page, EMAIL_A, PASSWORD_A)
  await swedebeat.getByRole('button', { name: 'Connect Swedebeat' }).click()
  await expect(swedebeat.getByRole('alert')).toContainText('signed in on another device')
  await expect(swedebeat.getByTestId('swedebeat-status')).toHaveCount(0) // nothing stored
  expect(fakeState().logins.map((l) => [l.force, l.ok])).toEqual([[false, false]])

  await swedebeat.getByLabel('Swedebeat password').fill(PASSWORD_A)
  await swedebeat.getByRole('button', { name: 'Sign in here anyway' }).click()
  await expect(swedebeat.getByTestId('swedebeat-status')).toHaveText('Connected')
  expect(fakeState().logins.map((l) => [l.force, l.ok])).toEqual([
    [false, false],
    [true, true],
  ])
  expect(fakeState().logins[1]?.device_id).not.toBe(PHONE)
})

test('an outage is explained in plain words and stores nothing', async ({ page }) => {
  await newUser(page, 'sb-outage')
  setOutage(true)
  await fill(page, EMAIL_A, PASSWORD_A)
  await card(page).getByRole('button', { name: 'Connect Swedebeat' }).click()
  await expect(card(page).getByRole('alert')).toContainText('Swedebeat could not be reached.')
  await expect(card(page).getByTestId('swedebeat-status')).toHaveCount(0)
})

test('two users on different Swedebeat accounts never see or share each other’s sign-in', async ({ browser }) => {
  test.setTimeout(90_000)
  const first = await browser.newContext({ extraHTTPHeaders: { 'X-Forwarded-For': '203.0.113.31' } })
  const second = await browser.newContext({ extraHTTPHeaders: { 'X-Forwarded-For': '203.0.113.32' } })
  const one = await first.newPage()
  const two = await second.newPage()
  await newUser(one, 'sb-one')
  await newUser(two, 'sb-two')

  await fill(one, EMAIL_A, PASSWORD_A)
  await card(one).getByRole('button', { name: 'Connect Swedebeat' }).click()
  await expect(card(one).getByTestId('swedebeat-account')).toHaveText(EMAIL_A)

  // The second user has nothing of the first user's connection.
  await two.reload()
  await expect(card(two).getByTestId('swedebeat-status')).toHaveCount(0)
  await expect(card(two).getByRole('button', { name: 'Connect Swedebeat' })).toBeVisible()
  await fill(two, EMAIL_B, PASSWORD_B)
  await card(two).getByRole('button', { name: 'Connect Swedebeat' }).click()
  await expect(card(two).getByTestId('swedebeat-account')).toHaveText(EMAIL_B)

  await one.reload()
  await expect(card(one).getByTestId('swedebeat-account')).toHaveText(EMAIL_A)

  // Each account got its own device id from classbeat, and none was forced.
  const ok = fakeState().logins.filter((l) => l.ok)
  expect(ok.map((l) => l.email)).toEqual([EMAIL_A, EMAIL_B])
  expect(new Set(ok.map((l) => l.device_id)).size).toBe(2)
  expect(ok.every((l) => !l.force)).toBe(true)

  // Disconnecting one leaves the other.
  await card(one).getByRole('button', { name: 'Disconnect Swedebeat' }).click()
  await card(one).getByRole('button', { name: 'Yes, disconnect Swedebeat' }).click()
  await expect(card(one).getByTestId('swedebeat-status')).toHaveCount(0)
  await two.reload()
  await expect(card(two).getByTestId('swedebeat-account')).toHaveText(EMAIL_B)
  await first.close()
  await second.close()
})
