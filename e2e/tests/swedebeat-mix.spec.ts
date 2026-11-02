import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import { connectMowl, getJob, jobId } from './design-helpers'
import { useModelScript } from './model-script'
import {
  CACHED_MIX_ID,
  CACHED_MIX_NAME,
  UNCACHED_MIX_ID,
  UNCACHED_MIX_NAME,
  ownerIdForEmail,
  resetSwedebeatMixes,
  seedCachedMix,
  swedebeatMixState,
  workerLogTail,
} from './swedebeat-mix-helpers'

// ADR 0016 section 3 path (a) on the fakes with the production (empty) MOWL media list (A6).
// The positive media fetch stays in backend/tests/test_swedebeat_fetch.py.

test.describe.configure({ mode: 'serial' })
test.setTimeout(120_000)
const LONG = { timeout: 90_000 }

test.beforeEach(() => {
  resetSwedebeatMixes()
})

async function signedInWithMowl(page: import('@playwright/test').Page, tag: string): Promise<string> {
  const email = uniqueEmail(tag)
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await connectMowl(page)
  return email
}

async function startMixDesign(page: import('@playwright/test').Page, title: string, mixName: string): Promise<string> {
  await page.goto('/classes/new')
  const form = page.getByRole('form', { name: 'New class' })
  await form.getByLabel('Class name').fill(title)
  await form.getByRole('button', { name: 'Use my Swedebeat mix' }).click()
  await form.getByRole('radio', { name: new RegExp(mixName) }).check({ force: true })
  await form.getByLabel('Length in minutes').fill('10')
  await form.getByLabel('Climbs', { exact: true }).fill('1')
  await form.getByLabel('Sprints', { exact: true }).fill('1')
  await form.getByRole('button', { name: 'Design my class' }).click()
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/)
  return jobId(page)
}

test('lists mixes in the picker and designs on a cached map', async ({ page }) => {
  useModelScript('e2e_swedebeat_mix')
  const email = await signedInWithMowl(page, 'sb-mix-cached')
  await page.goto('/classes/new')
  await page.getByRole('button', { name: 'Use my Swedebeat mix' }).click()
  await expect(page.getByRole('radio', { name: new RegExp(CACHED_MIX_NAME) })).toBeVisible()
  await expect(page.getByRole('radio', { name: new RegExp(UNCACHED_MIX_NAME) })).toBeVisible()

  const connections = await page.request.get('/api/connections')
  expect(connections.status()).toBe(200)
  const mowl = ((await connections.json()) as { connections: { provider: string; id: string }[] }).connections.find(
    (c) => c.provider === 'mowl',
  )
  expect(mowl?.id).toBeTruthy()
  seedCachedMix(ownerIdForEmail(email), mowl!.id, CACHED_MIX_ID)

  const before = swedebeatMixState()
  const id = await startMixDesign(page, 'E2E cached mix', CACHED_MIX_NAME)
  await expect(page.getByRole('heading', { name: 'Your class is ready' })).toBeVisible(LONG)
  await expect(page.getByRole('heading', { name: 'E2E Swedebeat mix ride' })).toBeVisible()

  const job = await getJob(page, id)
  expect(job.state).toBe('completed')
  expect(job.error_category).toBeNull()

  const after = swedebeatMixState()
  expect(after.lists).toBe(before.lists)
  expect(after.links).toBe(before.links)
  expect(after.attaches).toBe(before.attaches)
  expect(workerLogTail()).not.toContain('swedebeat media fetched')
})

test('an uncached mix ends media_host_not_allowed with no media fetch', async ({ page }) => {
  useModelScript('e2e_swedebeat_mix')
  await signedInWithMowl(page, 'sb-mix-uncached')
  const before = swedebeatMixState()
  const id = await startMixDesign(page, 'E2E uncached mix', UNCACHED_MIX_NAME)
  await expect.poll(async () => (await getJob(page, id)).state, LONG).toBe('failed')
  await expect(page.getByText('New Swedebeat mixes cannot be analysed here yet')).toBeVisible()

  const job = await getJob(page, id)
  expect(job.state).toBe('failed')
  expect(job.error_category).toBe('media_host_not_allowed')

  const after = swedebeatMixState()
  expect(after.links).toBeGreaterThan(before.links)
  expect(after.attaches).toBeGreaterThan(before.attaches)
  expect(workerLogTail()).not.toContain('swedebeat media fetched')
  expect(await page.content()).not.toContain('media.unlisted.example')
})
