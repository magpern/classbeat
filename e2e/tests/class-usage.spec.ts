import type { Page } from '@playwright/test'
import { expect, test } from './helpers'
import { PLAYLISTS, fakeMowl } from './design-helpers'
import { useModelScript } from './model-script'
import {
  LONG,
  classId,
  confirmWrite,
  designAndOpen,
  reviseByChat,
  saveArm,
  savedState,
  signedInWithMowl,
} from './class-helpers'

// M3b T2 (ADR 0007 sections 10 and 13): the usage the account page and GET /api/usage show is the
// sum of the agent_runs of the user's jobs, writing to MOWL adds none, and deleting the class
// keeps it. A job's own sum is the `usage` of GET /api/jobs/{id}. The FakeProvider reports fixed
// token counts per turn (backend/src/classbeat/agent/providers/fake.py), at the fake price.
test.setTimeout(180_000)

interface Sums {
  runs: number
  jobs: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  cost_micros: number
}
interface UsageView {
  total: Sums
  by_kind_model: (Sums & { job_kind: string; model: string })[]
  classes_designed: number
  revisions: number
}

const COUNTERS = ['input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'cost_micros'] as const

async function apiUsage(page: Page): Promise<UsageView> {
  const answer = await page.request.get('/api/usage')
  expect(answer.status()).toBe(200)
  return (await answer.json()) as UsageView
}

/** The sums of the jobs' own usage (GET /api/jobs/{id}). */
async function jobsSum(page: Page, ids: string[]): Promise<Sums> {
  const sum = { runs: 0, jobs: 0, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, cost_micros: 0 }
  for (const id of ids) {
    const job = (await (await page.request.get(`/api/jobs/${id}`)).json()) as { usage: Sums | null }
    expect(job.usage, `job ${id} has usage`).not.toBeNull()
    sum.jobs += 1
    sum.runs += job.usage!.runs
    for (const key of COUNTERS) sum[key] += job.usage![key]
  }
  return sum
}

// The account page's words for the figures (frontend/src/components/account/Usage.tsx).
const dollars = (micros: number): string =>
  micros <= 0 ? '$0.00' : micros < 10_000 ? 'under $0.01' : `$${(micros / 1_000_000).toFixed(2)}`
const tokens = (s: Sums): string =>
  new Intl.NumberFormat('en-US').format(s.input_tokens + s.output_tokens + s.cache_read_tokens + s.cache_write_tokens)

async function expectAccountPage(page: Page, view: UsageView): Promise<void> {
  await page.goto('/account#plan')
  await page.getByText('Show usage details').click()
  const facts = page.getByTestId('usage-facts')
  await expect(facts).toBeVisible()
  const fact = (label: string) => facts.locator('div').filter({ has: page.getByText(label, { exact: true }) }).locator('dd')
  await expect(fact('Tokens')).toHaveText(tokens(view.total))
  await expect(fact('Estimated cost')).toHaveText(dollars(view.total.cost_micros))
  await expect(fact('Classes designed')).toHaveText(String(view.classes_designed))
  await expect(fact('Revisions')).toHaveText(String(view.revisions))
}

test.afterAll(() => {
  saveArm('', true)
  useModelScript('good_first_draft')
})

test('usage is the sum of the jobs, saving adds none and deleting the class keeps it', async ({ page }) => {
  fakeMowl('arm', PLAYLISTS.draft.id)
  saveArm('', true)
  await signedInWithMowl(page, 'usage')
  const design = await designAndOpen(page, 'E2E usage', PLAYLISTS.draft.name)
  const id = classId(page)
  await reviseByChat(page, 'revise_good', 'Make the first minutes easier.', 2)
  await reviseByChat(page, 'revise_second', 'Ease the warm-up a little more.', 3)
  await confirmWrite(page, 'Save to MOWL', 'Save to MOWL')
  await expect(savedState(page)).toHaveText('Saved to MOWL as version 3. It is up to date.', LONG)

  // The class's three jobs that used the model: the design and the two changes.
  const messages = (await (await page.request.get(`/api/classes/${id}/messages`)).json()) as {
    messages: { role: string; job_id: string | null }[]
  }
  const revises = messages.messages.filter((m) => m.role === 'user' && m.job_id !== null).map((m) => m.job_id!)
  expect(revises).toHaveLength(2)
  const summed = await jobsSum(page, [design, ...revises])
  expect(summed.cost_micros).toBeGreaterThan(0)

  // The month's figures are those sums: tokens by counter, cost, runs, and the counts of work.
  const usage = await apiUsage(page)
  for (const key of ['runs', 'jobs', ...COUNTERS] as const) expect(usage.total[key], key).toBe(summed[key])
  expect(usage.classes_designed).toBe(1)
  expect(usage.revisions).toBe(2)
  expect(usage.by_kind_model.map((r) => [r.job_kind, r.jobs]).sort()).toEqual([
    ['design_draft', 1],
    ['revise_draft', 2],
  ])
  await expectAccountPage(page, usage)

  // Deleting the class takes the class, its versions and its conversation, and not the usage.
  await page.goto(`/classes/${id}`)
  // Delete class is in the header's ··· menu.
  await page.getByRole('button', { name: 'More actions' }).click()
  await page.getByRole('menuitem', { name: 'Delete class' }).click()
  await page.getByRole('button', { name: 'Yes, delete this class' }).click()
  await expect(page.getByRole('heading', { name: 'Class deleted' })).toBeVisible()
  await expect(page.getByText('The 1 workout classbeat saved to your MOWL account stays there')).toBeVisible()
  expect((await page.request.get(`/api/classes/${id}`)).status()).toBe(404)
  expect(await apiUsage(page)).toEqual(usage)
  await expectAccountPage(page, usage)
})
