import { execFileSync } from 'node:child_process'
import { expect, type Browser, type BrowserContext, type Page } from '@playwright/test'
import { PLAYLISTS, connectMowl, getJob } from './design-helpers'
import { PASSWORD, signInThroughUi, signUpThroughUi, uniqueEmail } from './helpers'

// Shared by m5-quota.spec.ts and m5-admin.spec.ts (M5 T1, #228): parallel API calls from several
// browser contexts that hold one user's session, and the account page's plan line. Fakes only.

export const LONG = { timeout: 60_000 }

/** What the plan limits of the seeded plans are (migration 0007). */
export const FREE = { classes: 3, concurrent: 1 }
export const INSTRUCTOR = { classes: 30, concurrent: 2 }

export interface Answer {
  status: number
  body: { detail?: Record<string, unknown>; id?: string; state?: string }
}

/** `n` extra browser contexts holding the signed-in user's session (their own client addresses). */
export async function contextsOf(browser: Browser, page: Page, n: number): Promise<BrowserContext[]> {
  const storageState = await page.context().storageState()
  const baseURL = new URL(page.url()).origin
  return Promise.all(
    Array.from({ length: n }, (_, i) =>
      browser.newContext({
        storageState,
        baseURL,
        extraHTTPHeaders: { 'X-Forwarded-For': `198.51.${100 + i}.${1 + Math.floor(Math.random() * 254)}` },
      }),
    ),
  )
}

/** The user's MOWL connection id (what a job input names). */
export async function mowlConnectionId(page: Page): Promise<string> {
  const answer = await page.request.get('/api/connections')
  const body = (await answer.json()) as { connections: { id: string; provider: string }[] }
  const mowl = body.connections.find((c) => c.provider === 'mowl')
  expect(mowl, 'the MOWL account is connected').toBeDefined()
  return mowl!.id
}

let titles = 0

/** `POST /api/jobs` for a design, as the new-class form sends it, from this context's session. */
export async function createDesign(
  context: BrowserContext,
  connectionId: string,
  playlist: { id: string } = PLAYLISTS.draft,
): Promise<Answer> {
  const session = (await (await context.request.get('/api/auth/session')).json()) as { csrf_token: string }
  const origin = new URL((await context.request.get('/api/ready')).url()).origin
  titles += 1
  const answer = await context.request.post('/api/jobs', {
    headers: {
      Origin: origin,
      'X-CSRF-Token': session.csrf_token,
      'Idempotency-Key': `e2e-${Date.now()}-${titles}-${Math.random().toString(36).slice(2)}`,
    },
    data: {
      kind: 'design_draft',
      input: {
        title: `E2E limit ${titles}`,
        brief: { length_min: 30, intensity: 'moderate', climbs: 1, sprints: 2, races: 0, ftp_test: null, style: '', text: '' },
        music: { source: 'mowl_spotify_playlist', spotify_playlist_id: playlist.id },
        mowl_connection_id: connectionId,
      },
    },
  })
  return { status: answer.status(), body: (await answer.json()) as Answer['body'] }
}

/** `total` creates at once, spread over the contexts. */
export async function burst(contexts: BrowserContext[], connectionId: string, total: number): Promise<Answer[]> {
  return Promise.all(Array.from({ length: total }, (_, i) => createDesign(contexts[i % contexts.length]!, connectionId)))
}

export const statusesOf = (answers: Answer[]): number[] => answers.map((a) => a.status).sort((a, b) => a - b)

/** Wait for a job to end and return its state. */
export async function endedAs(page: Page, id: string): Promise<string> {
  let state = ''
  await expect
    .poll(async () => (state = (await getJob(page, id)).state), LONG)
    .toMatch(/^(completed|failed|cancelled|expired)$/)
  return state
}

/** The account page's "3 of 3 classes in October" line, after a fresh load. */
export async function expectClasses(page: Page, used: number, limit: number): Promise<void> {
  await page.goto('/account#plan')
  await page.reload() // a hash-only visit keeps the page and its old figures
  await expect(page.getByTestId('plan-classes')).toHaveText(
    new RegExp(`^${String(used)} of ${String(limit)} ${limit === 1 ? 'class' : 'classes'} in `),
  )
}

/** The operator CLI in the stack: make `email` an admin (`admin grant`, ADR 0003 section 7). */
export function grantAdmin(email: string): void {
  execFileSync('docker', ['compose', 'exec', '-T', 'web', 'python', '-m', 'classbeat', 'admin', 'grant', email], {
    cwd: new URL('../..', import.meta.url),
    stdio: ['ignore', 'ignore', 'ignore'],
  })
}

/**
 * Make the admin's last TOTP check 13 hours old (the 12-hour window lapsed, ADR 0011 section 6),
 * as a database superuser inside the stack's `db` container. Test control only.
 */
export function lapseAdminCheck(email: string): void {
  if (!/^[\w.+-]+@example\.test$/.test(email)) throw new Error('not a test address')
  const sql = `UPDATE sessions SET mfa_at = now() - interval '13 hours' WHERE user_id = (SELECT id FROM users WHERE email = '${email}')`
  execFileSync('docker', ['compose', 'exec', '-T', 'db', 'psql', '-U', 'postgres', '-d', 'classbeat', '-v', 'ON_ERROR_STOP=1', '-c', sql], {
    cwd: new URL('../..', import.meta.url),
    stdio: ['ignore', 'ignore', 'ignore'],
  })
}

/** The month's held credits as `GET /api/account/plan` counts them. */
export async function heldOf(page: Page): Promise<number> {
  const answer = await page.request.get('/api/account/plan')
  expect(answer.status()).toBe(200)
  return ((await answer.json()) as { classes_held: number }).classes_held
}

/** Wait for the held count to reach `n` (a released credit is settled by the worker's pass). */
export async function expectHeld(page: Page, n: number): Promise<void> {
  await expect.poll(() => heldOf(page), LONG).toBe(n)
}

/** A client address of its own: sign-ups are limited per address and hour. */
function randomAddress(): string {
  return `192.0.${1 + Math.floor(Math.random() * 254)}.${1 + Math.floor(Math.random() * 254)}`
}

/** A user's page in a context of its own, signed up and signed in, with the fake MOWL connected. */
export async function newUser(browser: Browser, baseURL: string | undefined, tag: string, mowl = true, admin = false) {
  const context = await browser.newContext({ baseURL, extraHTTPHeaders: { 'X-Forwarded-For': randomAddress() } })
  const page = await context.newPage()
  const email = uniqueEmail(tag)
  await signUpThroughUi(page, email)
  if (admin) grantAdmin(email) // ends the user's sessions: sign in after it
  if (admin) {
    // An admin lands on the admin area, not the account page.
    await page.goto('/signin')
    await page.getByLabel('Email', { exact: true }).fill(email)
    await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
    await page.getByRole('button', { name: 'Sign in', exact: true }).click()
    await expect(page).not.toHaveURL(/\/signin/)
  } else await signInThroughUi(page, email)
  if (mowl) await connectMowl(page)
  return { page, context, email, connectionId: mowl ? await mowlConnectionId(page) : '' }
}

export async function cancelJob(page: Page, id: string): Promise<void> {
  const session = (await (await page.request.get('/api/auth/session')).json()) as { csrf_token: string }
  const answer = await page.request.post(`/api/jobs/${id}/cancel`, {
    headers: { Origin: new URL(page.url()).origin, 'X-CSRF-Token': session.csrf_token },
  })
  expect(answer.status()).toBe(200)
}
