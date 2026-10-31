import { execFileSync } from 'node:child_process'
import { expect, type Page } from '@playwright/test'

// Shared by the Spotify specs: the fake Spotify's public test data and test control
// (e2e/fake-spotify/server.py), and the browser's visit to its authorize page. The fake is only on
// the compose network, so every control call is made from inside the web container. Nothing here
// prints a token, a code or a verifier.

export const CLIENT_A = 'e2e0'.repeat(8)
export const CLIENT_B = 'e2e1'.repeat(8)
export const CLIENT_UNKNOWN = 'e2e9'.repeat(8) // 32 hex characters, no app of this id exists
export const USER_A = 'e2e-rider-one'
export const USER_B = 'e2e-rider-two'

const ROOT = new URL('../..', import.meta.url)

export function compose(args: string[]): string {
  return execFileSync('docker', ['compose', ...args], {
    cwd: ROOT,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  })
}

/** Runs a Python snippet in a stack container (the fake is only reachable from there). */
export function inContainer(service: 'web' | 'worker', script: string, ...args: string[]): string {
  return compose(['exec', '-T', service, 'python', '-c', script, ...args]).trim()
}

const CONTROL = [
  'import sys, urllib.request as u',
  'u.install_opener(u.build_opener(u.ProxyHandler({})))',
  'q = u.Request("https://api.spotify.com/__e2e/" + sys.argv[2], method=sys.argv[1])',
  'print(u.urlopen(q, timeout=15).read().decode())',
].join('\n')

/** One call to the fake Spotify's test control (`/__e2e/<path>`). */
export function control<T = unknown>(method: 'GET' | 'POST', path: string): T {
  return JSON.parse(inContainer('web', CONTROL, method, path)) as T
}

export interface FakeRequest {
  n: number
  method: string
  host: string
  path: string
  client_id: string | null
  token_app: string | null
  token_user: string | null
  client_secret: boolean
  outcome: string
}

export interface FakeState {
  requests: FakeRequest[]
}

export const fakeState = (): FakeState => control<FakeState>('GET', 'state')

/** Back to the seeded fake: CLIENT_A (allows USER_A), CLIENT_B (allows USER_B), USER_A signed in. */
export const resetSpotify = (): void => {
  control('POST', 'reset')
}

/** Who approves at the authorize page next. */
export const signInAtSpotify = (user: string): void => {
  control('POST', `sign-in?user=${encodeURIComponent(user)}`)
}

// The browser's visit to the authorize page, made from the web container without following the
// redirect: prints the status and the Location header.
const AUTHORIZE = String.raw`
import json, sys
import urllib.error as e, urllib.request as u
class Stay(u.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
opener = u.build_opener(u.ProxyHandler({}), Stay)
try:
    answer = opener.open("https://accounts.spotify.com/authorize?" + sys.argv[1], timeout=15)
    print(json.dumps({"status": answer.status, "location": None}))
except e.HTTPError as answer:
    print(json.dumps({"status": answer.code, "location": answer.headers.get("Location")}))
`

export interface Authorize {
  /** The client id the page sent Spotify. */
  clientId: string | null
  /** The status the fake's authorize page answered. */
  status: number
}

/**
 * Plays Spotify's authorize page for the page under test: the redirect to
 * accounts.spotify.com/authorize is answered with what the fake answers (a redirect back to the
 * callback, or its error page), exactly as the browser would see it. `tamper` can change the query
 * the fake sees (a changed `state`, say). The last visit is in `seen`.
 */
export async function playSpotify(
  page: Page,
  options: { tamper?: (query: URLSearchParams) => void } = {},
): Promise<{ seen: Authorize[] }> {
  const seen: Authorize[] = []
  await page.route('https://accounts.spotify.com/**', async (route) => {
    const url = new URL(route.request().url())
    expect(url.pathname).toBe('/authorize')
    const query = new URLSearchParams(url.search)
    // The wizard must never ask for a client secret and uses PKCE.
    expect(query.get('code_challenge_method')).toBe('S256')
    expect(query.has('client_secret')).toBe(false)
    options.tamper?.(query)
    const answer = JSON.parse(inContainer('web', AUTHORIZE, query.toString())) as {
      status: number
      location: string | null
    }
    seen.push({ clientId: url.searchParams.get('client_id'), status: answer.status })
    if (answer.location !== null) {
      await route.fulfill({ status: answer.status, headers: { location: answer.location } })
    } else {
      await route.fulfill({ status: answer.status, contentType: 'text/plain', body: 'Spotify authorize page' })
    }
  })
  return { seen }
}

/** On the Spotify wizard page, enter a Client ID and press the connect button. */
export async function connectSpotify(page: Page, clientId: string): Promise<void> {
  await page.goto('/account/spotify')
  await page.getByLabel('Spotify Client ID').fill(clientId)
  await page.getByRole('button', { name: /^Connect Spotify/ }).click()
}
