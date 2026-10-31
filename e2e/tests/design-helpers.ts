import { execFileSync } from 'node:child_process'
import { expect, type Page } from '@playwright/test'

// Shared by design-draft.spec.ts: connect the fake MOWL account, fill the new-class form, and read
// or steer the fake MOWL (e2e/fake-mowl/server.py). Public test values only.

export const MOWL_EMAIL = 'rider@example.test'
export const MOWL_PASSWORD = 'e2e-fake-mowl-password'

/** The test playlists of the fake MOWL (Spotify id, as MOWL lists them, and name). */
export const PLAYLISTS = {
  draft: { id: '2cDeFgHiJkLmNoPqRsTuVw', name: 'E2E Draft Climb' },
  slow: { id: '3dEfGhIjKlMnOpQrStUvWx', name: 'E2E Slow Mix' },
  flaky: { id: '4eFgHiJkLmNoPqRsTuVwXy', name: 'E2E Flaky Attach' },
} as const

export interface FakeMowlState {
  /** Attach calls received. */
  attaches: number
  /** Reads waiting on the test's release right now. */
  held: number
  /** Reads made of the playlist, by kind. */
  reads: { tracks?: number; availability?: number; sections?: number }
}

const CALL_FAKE = [
  'import sys, urllib.request as u',
  'q = u.Request("https://api.mowl.com/__e2e/" + sys.argv[2], method=sys.argv[1])',
  'print(u.urlopen(q, timeout=15).read().decode())',
].join('\n')

/**
 * One call to the fake MOWL's test control (`/__e2e/<path>`). The fake is only on the compose
 * network, so the call is made from inside the web container, which trusts its certificate.
 */
export function fakeControl<T>(method: 'GET' | 'POST', path: string): T {
  const out = execFileSync(
    'docker',
    ['compose', 'exec', '-T', 'web', 'python', '-c', CALL_FAKE, method, path],
    { cwd: new URL('../..', import.meta.url), encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] },
  )
  return JSON.parse(out) as T
}

/** The fake's per-playlist control (`arm`, `release`, `state`). */
export function fakeMowl(action: 'arm' | 'release' | 'state', playlistId: string): FakeMowlState {
  return fakeControl(action === 'state' ? 'GET' : 'POST', `${action}?playlist=${playlistId}`)
}

/** On the account page, connect the fake MOWL account. Needs a fresh sign-in. */
export async function connectMowl(page: Page): Promise<void> {
  await page.goto('/account')
  const mowl = page.getByRole('region', { name: 'MOWL (Intelligent Cycling)' })
  await mowl.getByLabel('MOWL email').fill(MOWL_EMAIL)
  await mowl.getByLabel('MOWL password').fill(MOWL_PASSWORD)
  await mowl.getByRole('button', { name: 'Connect MOWL' }).click()
  await expect(mowl.getByText('MOWL is connected.')).toBeVisible()
}

/** Fill the new-class form for the brief the recorded model script was written for (30 minutes,
 *  moderate, one climb, two sprints) and start the design. Returns the job's id. */
export async function startDesign(page: Page, title: string, playlistName: string): Promise<string> {
  await page.getByRole('link', { name: 'New class' }).click()
  const form = page.getByRole('form', { name: 'New class' })
  await form.getByLabel('Class name').fill(title)
  await form.getByRole('radio', { name: playlistName }).check({ force: true })
  await form.getByLabel('Length in minutes').fill('30')
  await form.getByLabel('Climbs').fill('1')
  await form.getByLabel('Sprints').fill('2')
  await form.getByRole('button', { name: 'Design my class' }).click()
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/)
  return jobId(page)
}

export function jobId(page: Page): string {
  const id = /\/jobs\/([0-9a-f-]{36})$/.exec(page.url())?.[1]
  if (id === undefined) throw new Error(`not on a job page: ${page.url()}`)
  return id
}

export interface JobView {
  state: string
  error_category: string | null
  cancel_requested: boolean
}

/** The job as the API shows it (signed in as the page's user). */
export async function getJob(page: Page, id: string): Promise<JobView> {
  const answer = await page.request.get(`/api/jobs/${id}`)
  expect(answer.status()).toBe(200)
  return (await answer.json()) as JobView
}
