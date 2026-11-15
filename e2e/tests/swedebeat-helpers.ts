import { expect, type Locator, type Page } from '@playwright/test'
import { inContainer } from './spotify-helpers'

// Shared by the Swedebeat specs: the fake SwedeBeat gateway's public test data and test control
// (e2e/fake-gateway/server.py over backend/tests/gatewaycore.py, ADR 0035 section 9). The fake is
// only on the compose network, so every control call is made from inside the web container. Its
// state holds no password, client key or bearer, and nothing here prints one.

export const EMAIL_A = 'rider@example.test'
export const PASSWORD_A = 'e2e-fake-gateway-password'
export const EMAIL_B = 'second@example.test'
export const PASSWORD_B = 'e2e-fake-gateway-second'

const CONTROL = [
  'import sys, urllib.request as u',
  'u.install_opener(u.build_opener(u.ProxyHandler({})))',
  'print(u.urlopen("https://gateway.classbeat-e2e.test/__e2e/" + sys.argv[1], timeout=15).read().decode())',
].join('\n')

function control<T = unknown>(path: string): T {
  return JSON.parse(inContainer('web', CONTROL, path)) as T
}

/** One request the fake gateway saw: header names only, the body without a password. */
export interface GatewayRequest {
  method: string
  path: string
  headers: string[]
  body: unknown
  /** The account of the bearer it carried, if any. */
  email: string | null
}

export const gatewayRequests = (): GatewayRequest[] => control<{ requests: GatewayRequest[] }>('state').requests

export interface GatewayLogin {
  email: string
  device_id: string
  force: boolean
}

/** Every login the gateway was sent (accepted or not). */
export const gatewayLogins = (): GatewayLogin[] =>
  gatewayRequests()
    .filter((r) => r.path === '/auth/login')
    .map((r) => r.body as GatewayLogin)

/** Back to the seeded fake: two accounts, no bearer, no catalogue, no injection. */
export const resetSwedebeat = (): void => {
  control('reset')
}

/**
 * The next answer of `path` (or of any path) is a failure of `kind` (gatewaycore's injections:
 * `status` with `status`, `unknown_client`, `bearer_401`, `rate_limited`, `slow` with `seconds`, ...).
 */
export const injectGateway = (kind: string, path?: string, params: Record<string, number> = {}): void => {
  const query = new URLSearchParams({ kind, ...(path ? { path } : {}) })
  for (const [name, value] of Object.entries(params)) query.set(name, String(value))
  control(`inject?${query.toString()}`)
}

export interface Song {
  title: string
  artist: string
}

/**
 * What the fake's catalogue answers (ADR 0035 section 6): a `found` song answers a hit of the same
 * title, an `unsure` one a different title, any other song `missing`. Cleared by `resetSwedebeat`.
 */
export const setCatalogue = (found: Song[], unsure: Song[] = []): void => {
  const pairs = (songs: Song[]) => encodeURIComponent(songs.map((s) => `${s.title}~${s.artist}`).join('|'))
  control(`catalogue?found=${pairs(found)}&unsure=${pairs(unsure)}`)
}

/** The match calls the gateway has seen: the account each one's bearer belongs to, never a title. */
export const matchCalls = (): (string | null)[] =>
  gatewayRequests()
    .filter((r) => r.path === '/catalog/match')
    .map((r) => r.email)

/** The tracks each match call sent (title and artist only). */
export const matchedTracks = (): Song[][] =>
  gatewayRequests()
    .filter((r) => r.path === '/catalog/match')
    .map((r) => (r.body as { tracks: Song[] }).tracks)

/** The Swedebeat row of the account page's Connections list (ADR 0028). */
export const swedebeatRow = (page: Page): Locator => page.getByTestId('connection-swedebeat')

/** Opens the Connect Swedebeat dialog from the account page's list. */
export async function openSwedebeatDialog(page: Page): Promise<Locator> {
  if (!/\/account(\/|$|\?|#)/.test(new URL(page.url()).pathname)) await page.goto('/account')
  await swedebeatRow(page).getByRole('button', { name: 'Connect Swedebeat' }).click()
  return page.getByRole('dialog', { name: 'Connect Swedebeat' })
}

/** The dialog's required "I understand" box (ADR 0035 section 4: other devices are signed out). */
export const signOutConsent = (dialog: Locator): Locator => dialog.getByRole('checkbox', { name: 'I understand' })

/** Fills the open dialog's email and password and ticks the sign-out consent. */
export async function fillSwedebeat(dialog: Locator, email: string, password: string): Promise<void> {
  await dialog.getByLabel('Swedebeat email').fill(email)
  await dialog.getByLabel('Swedebeat password').fill(password)
  await signOutConsent(dialog).check()
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
    headers: { 'X-CSRF-Token': session.csrf_token, Origin: new URL(page.url()).origin },
  })
  expect(answer.ok()).toBe(true)
}
