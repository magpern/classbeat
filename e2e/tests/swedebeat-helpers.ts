import { expect, type Locator, type Page } from '@playwright/test'
import { inContainer } from './spotify-helpers'

// Shared by the Swedebeat spec: the fake Swedebeat's public test data and test control
// (e2e/fake-swedebeat/server.py). The fake is only on the compose network, so every control call
// is made from inside the web container. Nothing here prints a password or a token.

export const EMAIL_A = 'rider@example.test'
export const PASSWORD_A = 'e2e-fake-swedebeat-password'
export const EMAIL_B = 'second@example.test'
export const PASSWORD_B = 'e2e-fake-swedebeat-second'
export const PHONE = 'phone-device-0001'

const CONTROL = [
  'import sys, urllib.request as u',
  'u.install_opener(u.build_opener(u.ProxyHandler({})))',
  'print(u.urlopen("https://www.swedebeat.se/__e2e/" + sys.argv[1], timeout=15).read().decode())',
].join('\n')

function control<T = unknown>(path: string): T {
  return JSON.parse(inContainer('web', CONTROL, path)) as T
}

export interface FakeLogin {
  email: string
  device_id: string
  force: boolean
  ok: boolean
}

export interface FakeState {
  logins: FakeLogin[]
  calls: { path: string; email: string | null }[]
  outage: boolean
}

export const fakeState = (): FakeState => control<FakeState>('state')

/** Back to the seeded fake: two accounts, nothing held, no outage. */
export const resetSwedebeat = (): void => {
  control('reset')
}

/** Another device (the phone app) takes the account's Swedebeat session. */
export const holdSession = (email: string, device = PHONE): void => {
  control(`hold?email=${encodeURIComponent(email)}&device=${encodeURIComponent(device)}`)
}

export const setOutage = (on: boolean): void => {
  control(`outage?on=${on ? '1' : '0'}`)
}

/**
 * What the fake's catalogue answers (ADR 0016 section 5, B5): a `found` title answers a hit of the
 * same title, an `unsure` title a different one, any other title no hit. Cleared by `resetSwedebeat`.
 */
export const setCatalogue = (found: string[], unsure: string[] = []): void => {
  control(`catalogue?found=${encodeURIComponent(found.join('|'))}&unsure=${encodeURIComponent(unsure.join('|'))}`)
}

/** The catalogue searches the fake has seen: the account each one's token belongs to, never a title. */
export const catalogueCalls = (): (string | null)[] =>
  fakeState()
    .calls.filter((c) => c.path === '/track/extendedsearch')
    .map((c) => c.email)

/** The Swedebeat row of the account page's Connections list (ADR 0028). */
export const swedebeatRow = (page: Page): Locator => page.getByTestId('connection-swedebeat')

/** Opens the Connect Swedebeat dialog from the account page's list. */
export async function openSwedebeatDialog(page: Page): Promise<Locator> {
  if (!/\/account(\/|$|\?|#)/.test(new URL(page.url()).pathname)) await page.goto('/account')
  await swedebeatRow(page).getByRole('button', { name: 'Connect Swedebeat' }).click()
  return page.getByRole('dialog', { name: 'Connect Swedebeat' })
}

/** Fills the open dialog's email and password. */
export async function fillSwedebeat(dialog: Locator, email: string, password: string): Promise<void> {
  await dialog.getByLabel('Swedebeat email').fill(email)
  await dialog.getByLabel('Swedebeat password').fill(password)
}

/**
 * Turns the stored "I use Swedebeat" preference (#525) on through the app's own API, the way the
 * Connect dialog does (ADR 0028: the switch is no longer on the page). For specs that need the
 * mix choice but have no reason to connect a Swedebeat account.
 */
export async function useSwedebeat(page: Page): Promise<void> {
  const session = (await (await page.request.get('/api/auth/session')).json()) as { csrf_token: string }
  const answer = await page.request.put('/api/connections/swedebeat/preference', {
    data: { uses_swedebeat: true },
    headers: { 'X-CSRF-Token': session.csrf_token },
  })
  expect(answer.ok()).toBe(true)
}
