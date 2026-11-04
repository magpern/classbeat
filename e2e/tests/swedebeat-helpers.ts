import { expect, type Page } from '@playwright/test'
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

/**
 * Turns on the signed-in user's "I use Swedebeat" switch on the account page (#525: default off, and
 * every Swedebeat feature needs it), the way a user does.
 */
export async function useSwedebeatThroughUi(page: Page): Promise<void> {
  if (!page.url().includes('/account')) await page.goto('/account')
  const toggle = page.getByRole('region', { name: 'Swedebeat', exact: true }).getByRole('switch', { name: 'I use Swedebeat' })
  await expect(toggle).toBeVisible()
  if ((await toggle.getAttribute('aria-checked')) !== 'true') await toggle.click()
  await expect(toggle).toHaveAttribute('aria-checked', 'true')
}
