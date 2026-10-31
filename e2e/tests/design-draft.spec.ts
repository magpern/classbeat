import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import {
  MOWL_PASSWORD,
  PLAYLISTS,
  connectMowl,
  fakeMowl,
  getJob,
  jobId,
  startDesign,
} from './design-helpers'

// M3a T1 (ADR 0006 section 8): a class drafted from a MOWL playlist, end to end, with the fake
// MOWL (e2e/fake-mowl) and the FakeProvider replaying backend/tests/agent_scripts/
// good_first_draft.json in the worker. Nothing leaves the compose network. Waits are on page
// and API state, never on time; the fake MOWL's control (arm / release / state) makes the
// in-flight and uncertain moments certain.
test.describe.configure({ mode: 'serial' })
test.setTimeout(120_000)
const LONG = { timeout: 60_000 }

async function signedInWithMowl(page: import('@playwright/test').Page, tag: string) {
  const email = uniqueEmail(tag)
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email) // a fresh sign-in counts as recent authentication
  await connectMowl(page)
}

test('a class is drafted from a MOWL playlist', async ({ page }) => {
  fakeMowl('arm', PLAYLISTS.draft.id)
  await signedInWithMowl(page, 'draft')
  const id = await startDesign(page, 'E2E draft', PLAYLISTS.draft.name)

  await expect(page.getByRole('heading', { name: 'Your class is ready' })).toBeVisible(LONG)
  await expect(page.getByRole('heading', { name: 'Invented Tuesday climb' })).toBeVisible()
  await expect(page.getByText('Draft 1, 30:00 in total')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Warm-up' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Sprints' })).toBeVisible()

  const job = await getJob(page, id)
  expect(job.state).toBe('completed')
  expect(job.error_category).toBeNull()

  // The playlist was attached once, then read once (8 songs, one section read each).
  const fake = fakeMowl('state', PLAYLISTS.draft.id)
  expect(fake.attaches).toBe(1)
  expect(fake.reads).toEqual({ tracks: 1, availability: 1, sections: 8 })

  // The page, the job and the draft never carry the MOWL password.
  expect(await page.content()).not.toContain(MOWL_PASSWORD)
  const revision = await page.request.get(`/api/jobs/${id}/revision`)
  expect(revision.status()).toBe(200)
  expect(await revision.text()).not.toContain(MOWL_PASSWORD)
})

test('a design is cancelled while it is running', async ({ page }) => {
  fakeMowl('arm', PLAYLISTS.slow.id)
  await signedInWithMowl(page, 'cancel')
  const id = await startDesign(page, 'E2E cancel', PLAYLISTS.slow.name)

  // The run is certainly in flight: the fake holds its first read of the playlist.
  await expect.poll(() => fakeMowl('state', PLAYLISTS.slow.id).held, LONG).toBe(1)

  // Leaving and coming back shows the same run (the page resumes from the server).
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Designing E2E cancel…' })).toBeVisible()
  await expect(page.getByTestId('progress').getByText('Reading your playlist')).toBeVisible()

  await page.getByRole('button', { name: 'Stop designing' }).click()
  await expect(page.getByText('Stopping. This takes a moment.')).toBeVisible()
  expect((await getJob(page, id)).cancel_requested).toBe(true)

  // The held read ends; the run reaches its next checkpoint and stops.
  fakeMowl('release', PLAYLISTS.slow.id)
  await expect(page.getByRole('heading', { name: 'You stopped this design' })).toBeVisible(LONG)
  await expect(page.getByText('Nothing was saved.')).toBeVisible()

  const job = await getJob(page, id)
  expect(job.state).toBe('cancelled')
  // No read after the held one, and nothing stored.
  const fake = fakeMowl('state', PLAYLISTS.slow.id)
  expect(fake.attaches).toBe(1)
  expect(fake.held).toBe(0)
  expect(fake.reads).toEqual({ tracks: 1 })
  expect((await page.request.get(`/api/jobs/${id}/revision`)).status()).toBe(409)
})

test('an attach MOWL did not answer clearly is left to the user, who then decides', async ({ page }) => {
  fakeMowl('arm', PLAYLISTS.flaky.id)
  await signedInWithMowl(page, 'uncertain')
  const first = await startDesign(page, 'E2E uncertain', PLAYLISTS.flaky.name)

  // MOWL applied the attach but answered with an error: classbeat does not try again on its own.
  await expect(
    page.getByRole('heading', { name: 'classbeat could not tell whether your playlist was added' }),
  ).toBeVisible(LONG)
  const failed = await getJob(page, first)
  expect(failed.state).toBe('failed')
  expect(failed.error_category).toBe('uncertain_write')
  expect(fakeMowl('state', PLAYLISTS.flaky.id)).toMatchObject({ attaches: 1, reads: {} })

  // Check again cannot find out (MOWL has no read for it), and sends nothing.
  await page.getByRole('button', { name: 'Check again' }).click()
  await expect(page.getByText('classbeat still cannot tell whether the playlist was added.')).toBeVisible()
  expect(fakeMowl('state', PLAYLISTS.flaky.id).attaches).toBe(1)
  expect(page.url()).toContain(first)

  // Do it anyway asks to be sure, then lets the user design again.
  await page.getByRole('button', { name: 'Do it anyway' }).click()
  const confirm = page.getByRole('group', { name: 'Confirm' })
  await expect(confirm).toBeVisible()
  expect(fakeMowl('state', PLAYLISTS.flaky.id).attaches).toBe(1)
  await confirm.getByRole('button', { name: 'Yes, add it again' }).click()
  await expect(page.getByText(/The next design adds the playlist to your MOWL music again/)).toBeVisible()

  await page.getByRole('button', { name: 'Design again' }).click()
  await expect(page).toHaveURL(new RegExp(`/jobs/(?!${first})[0-9a-f-]{36}$`))
  const second = jobId(page)
  await expect(page.getByRole('heading', { name: 'Your class is ready' })).toBeVisible(LONG)
  await expect(page.getByRole('heading', { name: 'Invented Tuesday climb' })).toBeVisible()

  // The second attach is the one the user chose; the second job reads the playlist once.
  expect((await getJob(page, second)).state).toBe('completed')
  expect(fakeMowl('state', PLAYLISTS.flaky.id)).toMatchObject({
    attaches: 2,
    reads: { tracks: 1, availability: 1, sections: 8 },
  })
})
