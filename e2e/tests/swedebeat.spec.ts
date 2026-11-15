import { type Page } from '@playwright/test'
import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import {
  EMAIL_A,
  EMAIL_B,
  PASSWORD_A,
  PASSWORD_B,
  gatewayLogins,
  injectGateway,
  resetSwedebeat,
  fillSwedebeat,
  openSwedebeatDialog,
  signOutConsent,
  swedebeatRow,
  useSwedebeat,
} from './swedebeat-helpers'

// Swedebeat connect end to end (ADR 0015, ADR 0035 sections 3 and 4) against the fake SwedeBeat
// gateway of compose.e2e.yaml (gateway.classbeat-e2e.test, its public test client key). The gateway
// always signs the account out of the Swedebeat app on other devices, so the dialog says so up front
// and sends nothing until "I understand" is ticked. Public test values only: no real Swedebeat
// account, gateway or key.

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

const card = swedebeatRow

async function disconnect(page: Page): Promise<void> {
  await card(page).getByRole('button', { name: 'Disconnect Swedebeat' }).click()
  await page
    .getByRole('dialog', { name: 'Disconnect Swedebeat?' })
    .getByRole('button', { name: 'Yes, disconnect Swedebeat' })
    .click()
}

test('connect, wrong password, status after a reload, and disconnect', async ({ page }) => {
  await newUser(page, 'sb-connect')
  const dialog = await openSwedebeatDialog(page)

  await fillSwedebeat(dialog, EMAIL_A, 'not-the-password')
  await dialog.getByRole('button', { name: 'Connect Swedebeat' }).click()
  await expect(dialog.getByRole('alert')).toHaveText('Swedebeat did not accept that email and password.')
  await expect(dialog.getByLabel('Swedebeat password')).toHaveValue('')

  await dialog.getByLabel('Swedebeat password').fill(PASSWORD_A)
  const connected = page.waitForResponse((r) => r.url().endsWith('/api/connections/swedebeat'))
  await dialog.getByRole('button', { name: 'Connect Swedebeat' }).click()
  const answer = await connected
  expect(answer.status()).toBe(200)
  expect(new URL(answer.url()).search).toBe('') // nothing in the URL
  const text = await answer.text()
  expect(text).not.toContain(PASSWORD_A)
  expect(text).not.toMatch(/token|device|bearer/i) // the answer holds no secret
  await expect(page.getByText('Swedebeat is connected.')).toBeVisible()
  await expect(card(page).getByTestId('swedebeat-status')).toHaveText('Connected')
  await expect(card(page).getByTestId('swedebeat-account')).toHaveText(EMAIL_A)
  await expect(page.getByLabel('Swedebeat password')).toHaveCount(0) // the dialog closed

  // The gateway saw two logins, both with the confirmed force and this user's one device id.
  const logins = gatewayLogins()
  expect(logins.map((l) => [l.email, l.force])).toEqual([
    [EMAIL_A, true],
    [EMAIL_A, true],
  ])
  expect(new Set(logins.map((l) => l.device_id)).size).toBe(1)

  // The status survives a reload: it comes from the server, and the password is nowhere.
  await page.reload()
  await expect(card(page).getByTestId('swedebeat-status')).toHaveText('Connected')
  expect(await page.content()).not.toContain(PASSWORD_A)

  await disconnect(page)
  await expect(page.getByText(/Swedebeat is disconnected/)).toBeVisible()
  await expect(card(page).getByRole('button', { name: 'Connect Swedebeat' })).toBeVisible()
  await expect(card(page).getByTestId('swedebeat-status')).toHaveCount(0)
})

test('the sign-out is said up front, and nothing is sent before it is confirmed', async ({ page }) => {
  await newUser(page, 'sb-consent')
  const dialog = await openSwedebeatDialog(page)
  await expect(
    dialog.getByText('Connecting signs this Swedebeat account out of the Swedebeat app on other devices, such as your phone.'),
  ).toBeVisible()
  await expect(signOutConsent(dialog)).not.toBeChecked()
  await dialog.getByLabel('Swedebeat email').fill(EMAIL_A)
  await dialog.getByLabel('Swedebeat password').fill(PASSWORD_A)
  await expect(dialog.getByRole('button', { name: 'Connect Swedebeat' })).toBeDisabled()
  await expect(dialog.getByRole('button', { name: 'Sign in anyway' })).toHaveCount(0)

  // An API client gets the same rule: a body without "force": true is refused before any login.
  // The preference goes on first, as the dialog stores it, or the route ends feature_off before
  // it reads the body.
  await useSwedebeat(page)
  const session = (await (await page.request.get('/api/auth/session')).json()) as { csrf_token: string }
  const refused = await page.request.post('/api/connections/swedebeat', {
    data: { email: EMAIL_A, password: PASSWORD_A },
    headers: { 'X-CSRF-Token': session.csrf_token, Origin: new URL(page.url()).origin },
  })
  expect(refused.status()).toBe(409)
  expect(await refused.text()).toContain('sign_out_required')
  expect(gatewayLogins()).toEqual([])

  await signOutConsent(dialog).check()
  await dialog.getByRole('button', { name: 'Connect Swedebeat' }).click()
  await expect(card(page).getByTestId('swedebeat-status')).toHaveText('Connected')
  expect(gatewayLogins().map((l) => l.force)).toEqual([true])
})

test('an outage is explained in plain words and stores nothing', async ({ page }) => {
  await newUser(page, 'sb-outage')
  injectGateway('status', '/auth/login', { status: 502 })
  const dialog = await openSwedebeatDialog(page)
  await fillSwedebeat(dialog, EMAIL_A, PASSWORD_A)
  await dialog.getByRole('button', { name: 'Connect Swedebeat' }).click()
  await expect(dialog.getByRole('alert')).toContainText('Swedebeat could not be reached.')
  await dialog.getByRole('button', { name: 'Close' }).click()
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

  const dialogOne = await openSwedebeatDialog(one)
  await fillSwedebeat(dialogOne, EMAIL_A, PASSWORD_A)
  await dialogOne.getByRole('button', { name: 'Connect Swedebeat' }).click()
  await expect(card(one).getByTestId('swedebeat-account')).toHaveText(EMAIL_A)

  // The second user has nothing of the first user's connection.
  await two.reload()
  await expect(card(two).getByTestId('swedebeat-status')).toHaveCount(0)
  await expect(card(two).getByRole('button', { name: 'Connect Swedebeat' })).toBeVisible()
  const dialogTwo = await openSwedebeatDialog(two)
  await fillSwedebeat(dialogTwo, EMAIL_B, PASSWORD_B)
  await dialogTwo.getByRole('button', { name: 'Connect Swedebeat' }).click()
  await expect(card(two).getByTestId('swedebeat-account')).toHaveText(EMAIL_B)

  await one.reload()
  await expect(card(one).getByTestId('swedebeat-account')).toHaveText(EMAIL_A)

  // Each account got its own device id from classbeat, and both confirmed the sign-out.
  const logins = gatewayLogins()
  expect(logins.map((l) => l.email)).toEqual([EMAIL_A, EMAIL_B])
  expect(new Set(logins.map((l) => l.device_id)).size).toBe(2)
  expect(logins.every((l) => l.force)).toBe(true)

  // Disconnecting one leaves the other.
  await disconnect(one)
  await expect(card(one).getByTestId('swedebeat-status')).toHaveCount(0)
  await two.reload()
  await expect(card(two).getByTestId('swedebeat-account')).toHaveText(EMAIL_B)
  await first.close()
  await second.close()
})
