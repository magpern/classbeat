import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import { PLAYLISTS, chooseLength, connectMowl, fakeMowl, getJob, jobId, startDesign } from './design-helpers'
import { useModelScript } from './model-script'
import {
  CACHED_MIX_ID,
  CACHED_MIX_NAME,
  ownerIdForEmail,
  resetSwedebeatMixes,
  seedCachedMix,
} from './swedebeat-mix-helpers'
import { useSwedebeat } from './swedebeat-helpers'

// #572 (ADR 0005): a user who closes the page during a design comes back to the job's real state,
// and a design that finished while they were away lands on the finished class. "Away" is a closed
// page and a new one in the same signed-in browser context: the job runs in the worker either way.
// The first test holds the design on the fake MOWL (arm / release), so the in-flight state is
// certain; the second designs on a cached Swedebeat mix and returns after it has finished. The
// hand-off's long absence is a step of swedebeat-match.spec.ts. No real account, no model.

test.describe.configure({ mode: 'serial' })
test.setTimeout(120_000)
const LONG = { timeout: 60_000 }

test.beforeEach(() => {
  resetSwedebeatMixes()
})

test('a design in flight is shown as it is on return, and finishes while the user is away', async ({ page }) => {
  useModelScript('good_first_draft')
  fakeMowl('arm', PLAYLISTS.slow.id)
  const email = uniqueEmail('return-held')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await connectMowl(page)
  const id = await startDesign(page, 'E2E away ride', PLAYLISTS.slow.name)
  // The run is certainly in flight: the fake holds its first read of the playlist.
  await expect.poll(() => fakeMowl('state', PLAYLISTS.slow.id).held, LONG).toBe(1)

  // The user closes the page and comes back with a new one.
  const back = await page.context().newPage()
  await page.close()
  try {
    await back.goto('/classes')
    await expect(back.getByText('Being designed')).toBeVisible(LONG)
    await back.getByRole('link', { name: 'E2E away ride' }).click()
    await expect(back).toHaveURL(/\/classes\/[0-9a-f-]{36}$/)
    await expect(back.getByText('This class is being designed.')).toContainText('you can leave this page')
    await back.getByRole('link', { name: 'See progress' }).click()
    await expect(back).toHaveURL(new RegExp(`/jobs/${id}$`))
    // No browser memory of the form is needed: the page says the job's real state.
    await expect(back.getByTestId('progress').getByText('Reading your playlist')).toBeVisible(LONG)
    expect((await getJob(back, id)).state).toBe('running')

    // It finishes while nobody looks.
    fakeMowl('release', PLAYLISTS.slow.id)
    await expect.poll(async () => (await getJob(back, id)).state, LONG).toBe('completed')

    // Back on the class page: the finished class, no "being designed" notice.
    await back.goto('/classes')
    await expect(back.getByText('Being designed')).toHaveCount(0)
    await back.getByRole('link', { name: 'E2E away ride' }).click()
    await expect(back.getByRole('heading', { name: 'E2E away ride' })).toBeVisible(LONG)
    await expect(back.getByText('This class is being designed.')).toHaveCount(0)
    // The job page of a finished job says so instead of showing a stale progress view.
    await back.goto(`/jobs/${id}`)
    await expect(back.getByRole('heading', { name: 'Your class is ready' })).toBeVisible(LONG)
  } finally {
    fakeMowl('release', PLAYLISTS.slow.id)
    await back.close()
  }
})

test('a design on a Swedebeat mix that finished while the user was away opens as a finished class', async ({
  page,
}) => {
  useModelScript('e2e_swedebeat_mix')
  const email = uniqueEmail('return-mix')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await useSwedebeat(page)
  await connectMowl(page)
  const connections = await page.request.get('/api/connections')
  const mowl = ((await connections.json()) as { connections: { provider: string; id: string }[] }).connections.find(
    (c) => c.provider === 'mowl',
  )
  expect(mowl?.id).toBeTruthy()
  seedCachedMix(ownerIdForEmail(email), mowl!.id, CACHED_MIX_ID)

  await page.goto('/classes/new')
  const form = page.getByRole('form', { name: 'New class' })
  await form.getByLabel('Class name').fill('E2E away mix')
  await form.getByRole('radio', { name: 'Swedebeat mix', exact: true }).click()
  await form.getByRole('radio', { name: new RegExp(CACHED_MIX_NAME) }).check({ force: true })
  await chooseLength(form, 10)
  await form.getByLabel('Climbs', { exact: true }).fill('1')
  await form.getByLabel('Sprints', { exact: true }).fill('1')
  await form.getByRole('button', { name: 'Design my class' }).click()
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/)
  const id = jobId(page)

  // Close the page at once; the worker finishes without it.
  const back = await page.context().newPage()
  await page.close()
  try {
    await expect.poll(async () => (await getJob(back, id)).state, { timeout: 90_000 }).toBe('completed')
    await back.goto('/classes')
    await back.getByRole('link', { name: 'E2E away mix' }).click()
    await expect(back).toHaveURL(/\/classes\/[0-9a-f-]{36}$/)
    await expect(back.getByRole('heading', { name: 'E2E away mix' })).toBeVisible(LONG)
    await expect(back.getByText('This class is being designed.')).toHaveCount(0)
    // The brief's title is the class name; the designed class has its revision (the recorded script's name).
    await expect(back.getByText('E2E Swedebeat mix ride')).toBeVisible()
  } finally {
    await back.close()
  }
})
