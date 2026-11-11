import { execFileSync } from 'node:child_process'
import { expect, type Locator, type Page } from '@playwright/test'

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
  await page.getByTestId('connection-mowl').getByRole('button', { name: 'Connect MOWL' }).click()
  const dialog = page.getByRole('dialog', { name: 'Connect MOWL' })
  await dialog.getByLabel('MOWL email').fill(MOWL_EMAIL)
  await dialog.getByLabel('MOWL password').fill(MOWL_PASSWORD)
  await dialog.getByRole('button', { name: 'Connect MOWL' }).click()
  await expect(page.getByText('MOWL is connected.')).toBeVisible()
}

/** On the account page, disconnect MOWL through the confirmation dialog. */
export async function disconnectMowl(page: Page): Promise<void> {
  await page.goto('/account')
  await page.getByTestId('connection-mowl').getByRole('button', { name: 'Disconnect MOWL' }).click()
  await page
    .getByRole('dialog', { name: 'Disconnect MOWL?' })
    .getByRole('button', { name: 'Yes, disconnect MOWL' })
    .click()
  await expect(page.getByText(/MOWL is disconnected/)).toBeVisible()
}

/** Fill the new-class form for the brief the recorded model script was written for (30 minutes,
 *  moderate, one climb, two sprints) and start the design. Returns the job's id. */
/** Pick a playlist tile by clicking its visible label. The input itself is a 1px sr-only box, and a
 *  forced click on it skips the stability wait: the plan hint that arrives after the form moves the
 *  tiles, so the click landed elsewhere and the radio stayed unchecked. */
export async function choosePlaylist(form: Locator, playlistName: string): Promise<void> {
  const radio = form.getByRole('radio', { name: playlistName })
  // `has` is matched inside each label, so it takes a locator from the page, not from the form.
  await form.locator('label').filter({ has: form.page().getByRole('radio', { name: playlistName }) }).click()
  await expect(radio).toBeChecked()
}

/** Choose the class length in the form: a common length is one segment of the control, any other
 *  needs "Other", which brings the number field. */
export async function chooseLength(form: Locator, minutes: number): Promise<void> {
  if ([30, 45, 60, 90].includes(minutes)) {
    await form.getByRole('radio', { name: `${minutes} min`, exact: true }).click()
    return
  }
  await form.getByRole('radio', { name: 'Other', exact: true }).click()
  await form.getByLabel('Length in minutes').fill(String(minutes))
}

export async function startDesign(page: Page, title: string, playlistName: string, climbs = '1'): Promise<string> {
  await page.getByRole('link', { name: 'New class' }).click()
  await page.getByRole('link', { name: /^Advanced/ }).click()
  const form = page.getByRole('form', { name: 'New class' })
  await form.getByLabel('Class name').fill(title)
  await choosePlaylist(form, playlistName)
  await chooseLength(form, 30)
  await form.getByLabel('Climbs', { exact: true }).fill(climbs)
  await form.getByLabel('Sprints', { exact: true }).fill('2')
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
  class_id: string
  error_category: string | null
  cancel_requested: boolean
}

/** The job as the API shows it (signed in as the page's user). */
export async function getJob(page: Page, id: string): Promise<JobView> {
  const answer = await page.request.get(`/api/jobs/${id}`)
  expect(answer.status()).toBe(200)
  return (await answer.json()) as JobView
}
