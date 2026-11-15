import { test, expect, type BrowserContext, type Page, type Route } from '@playwright/test'
import { signInThroughUi, signUpThroughUi, uniqueEmail } from './helpers'
import { stackContext } from './mu-helpers'

// classbeat-player in the UI (ADR 0034 section 6, issue #1061): the Connections row and its
// dialog, the account's consent, and Send to player on the class page, in the real SPA served by
// the real web process. The player is a fake in the browser: the stack runs no player, so this
// spec answers the player routes the backend serves (status, start, disconnect, consent, the job
// and its stream) itself, and the class API for one class. The backend side, against a fake
// player over HTTP, is in backend/tests/test_player_*.py. Nothing leaves the machine, and
// nothing is sent before a press: every send is counted.
test.describe.configure({ mode: 'serial' })
test.setTimeout(180_000)

const CLASS = '22222222-2222-4222-8222-222222222222'
const REVISION = 'a1111111-1111-4111-8111-111111111111'
const JOB = 'e2222222-2222-4222-8222-222222222222'
const PLAYER_LINK = 'https://player.example.test/link/source'

const json = (body: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

let context: BrowserContext
let page: Page

// What the fake player's side says now; the tests change it between steps.
const state = {
  status: 'not_connected',
  linkEnabled: true,
  consentAt: null as string | null,
  started: 0,
  disconnected: 0,
  withdrawn: 0,
  sends: [] as unknown[],
  outcome: 'completed' as 'completed' | 'player_ahead' | 'player_conflict',
}

const statusBody = () => ({
  status: state.status,
  last_error: state.status === 'needs_reconnect' ? 'needs_reconnect' : null,
  connected_at: state.status === 'not_connected' ? null : '2026-11-10T10:00:00Z',
  link_enabled: state.linkEnabled,
})

const CONTENT = {
  name: 'Tuesday climb',
  description: 'Two hills',
  segments: [
    { name: 'Warm up', kind: 'warmup', intervals: [{ sec: 300, zone: 2, rpm: [80, 90], standing: false, cue: 'Easy now' }] },
    { name: 'Climb', kind: 'main', intervals: [{ sec: 600, zone: 5, rpm: [60, 70], standing: true, cue: 'Up' }] },
  ],
}

async function fakePlayer(target: Page, origin: string) {
  await target.route('**/api/connections/player', (route: Route) => {
    if (route.request().method() === 'DELETE') {
      state.disconnected += 1
      state.status = 'not_connected'
      return route.fulfill(json({ disconnected: true, link_may_remain: true }))
    }
    return route.fulfill(json(statusBody()))
  })
  await target.route('**/api/connections/player/start', (route) => {
    state.started += 1
    state.status = 'connected' // the player's page would link it; the callback then lands here
    return route.fulfill(json({ link_url: `${origin}/account/player?result=connected` }))
  })
  await target.route('**/api/auth/player-consent', (route) => {
    if (route.request().method() === 'DELETE') {
      state.withdrawn += 1
      state.consentAt = null
      return route.fulfill(json({ withdrawn: true }))
    }
    return route.fulfill(json({ enabled: true, consent_at: state.consentAt }))
  })
}

async function fakeClass(target: Page) {
  const revision = {
    id: REVISION,
    number: 1,
    parent_id: null,
    parent_number: null,
    origin: 'design_draft',
    created_at: '2026-11-14T10:05:00Z',
    is_current: true,
    summary: { segments: 2, intervals: 2, total_sec: 900 },
    cost_micros: 0,
  }
  await target.route(`**/api/classes/${CLASS}`, (route) =>
    route.fulfill(
      json({
        id: CLASS,
        title: 'Tuesday climb',
        created_at: '2026-11-14T10:00:00Z',
        updated_at: '2026-11-14T11:00:00Z',
        current_revision_id: REVISION,
        current_revision_number: 1,
        revision_count: 1,
        saved: { count: 0, latest_revision_number: null, latest_saved_at: null, latest_up_to_date: null },
        profile: [[2, 300], [5, 600]],
        open_job_id: null,
        open_job: null,
      }),
    ),
  )
  await target.route(`**/api/classes/${CLASS}/revisions`, (route) => route.fulfill(json({ revisions: [revision] })))
  await target.route(`**/api/classes/${CLASS}/messages`, (route) => route.fulfill(json({ messages: [] })))
  await target.route(`**/api/classes/${CLASS}/revisions/${REVISION}`, (route) =>
    route.fulfill(
      json({ ...revision, kind: 'draft', content: CONTENT, unknown_bpm: [], provenance: { origin: 'design_draft' } }),
    ),
  )
  const job = (jobState: string, category: string | null) => ({
    id: JOB,
    kind: 'send_to_player',
    state: jobState,
    stage: null,
    class_id: CLASS,
    cancel_requested: false,
    approval_revision_id: null,
    expires_at: null,
    approved_at: null,
    error_category: category,
    created_at: '2026-11-14T12:00:00Z',
    updated_at: '2026-11-14T12:00:00Z',
    finished_at: null,
  })
  await target.route('**/api/jobs', (route) => {
    state.sends.push(JSON.parse(route.request().postData() ?? '{}'))
    return route.fulfill(json(job('queued', null), 201))
  })
  await target.route(`**/api/jobs/${JOB}`, (route) =>
    route.fulfill(
      json(state.outcome === 'completed' ? job('completed', null) : job('failed', state.outcome)),
    ),
  )
  await target.route(`**/api/jobs/${JOB}/events`, (route) =>
    route.fulfill({
      contentType: 'text/event-stream',
      body: `: keepalive\n\nid: 1\nevent: ${state.outcome === 'completed' ? 'completed' : 'failed'}\ndata: {}\n\n`,
    }),
  )
}

test.beforeAll(async ({ browser, baseURL }) => {
  context = await stackContext(browser, baseURL!)
  page = await context.newPage()
  const email = uniqueEmail('player')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await fakePlayer(page, new URL(baseURL!).origin)
  await fakeClass(page)
})

test.afterAll(async () => {
  await context.close()
})

test('Connections: the row is there, and Connect only starts when pressed', async ({}, testInfo) => {
  await page.goto('/account')
  const row = page.getByTestId('connection-player')
  await expect(row).toContainText('Runs your classes on a screen in the room')
  await expect(row).toContainText('Optional')
  await row.getByRole('button', { name: 'Connect classbeat-player' }).click()
  const dialog = page.getByRole('dialog', { name: 'classbeat-player' })
  await expect(dialog).toContainText('Nothing is sent until you press Send to player on a class')
  expect(state.started).toBe(0)
  await testInfo.attach('connections-not-connected', { body: await page.screenshot(), contentType: 'image/png' })

  // Connect leaves for the link's address; the callback returns to /account/player.
  await dialog.getByRole('button', { name: 'Connect classbeat-player' }).click()
  await expect(page).toHaveURL(/\/account\/player\?result=connected$/)
  expect(state.started).toBe(1)
  const back = page.getByRole('dialog', { name: 'classbeat-player' })
  await expect(back).toContainText('classbeat-player is connected.')
  await expect(back.getByRole('button', { name: 'Disconnect classbeat-player' })).toBeVisible()
  await testInfo.attach('connections-connected', { body: await page.screenshot(), contentType: 'image/png' })
})

test('Connections: reconnect state, then disconnect asks first and notes the lingering link', async ({}, testInfo) => {
  state.status = 'needs_reconnect'
  await page.goto('/account')
  await expect(page.getByTestId('player-status')).toHaveText('Needs reconnecting')
  await page.getByRole('button', { name: 'Manage classbeat-player' }).click()
  const dialog = page.getByRole('dialog', { name: 'classbeat-player' })
  await expect(dialog).toContainText('stopped working')
  await expect(dialog.getByRole('button', { name: 'Reconnect classbeat-player' })).toBeVisible()
  await testInfo.attach('connections-reconnect', { body: await page.screenshot(), contentType: 'image/png' })

  await dialog.getByRole('button', { name: 'Disconnect classbeat-player' }).click()
  const ask = page.getByRole('dialog', { name: 'Disconnect classbeat-player?' })
  expect(state.disconnected).toBe(0)
  await ask.getByRole('button', { name: 'Yes, disconnect' }).click()
  await expect(dialog).toContainText('Disconnected from classbeat-player.')
  await expect(dialog).toContainText('may still be listed on the player’s site')
  expect(state.disconnected).toBe(1)
})

test('Account: the consent is shown and withdrawn on the press', async ({}, testInfo) => {
  state.consentAt = '2026-11-12T09:30:00Z'
  await page.goto('/account')
  await expect(page.getByTestId('player-consent')).toContainText('You allowed classbeat-player to sign you in on')
  expect(state.withdrawn).toBe(0)
  await testInfo.attach('account-consent', { body: await page.screenshot(), contentType: 'image/png' })
  await page.getByRole('button', { name: 'Withdraw permission' }).click()
  await expect(page.getByText('Permission withdrawn. classbeat-player asks again')).toBeVisible()
  expect(state.withdrawn).toBe(1)
  await expect(page.getByRole('button', { name: 'Withdraw permission' })).toHaveCount(0)
})

test('Class page: not connected sends to Connections, and nothing is sent', async () => {
  state.status = 'not_connected'
  await page.goto(`/classes/${CLASS}`)
  await expect(page.getByRole('heading', { name: 'Tuesday climb', level: 1 })).toBeVisible()
  const link = page.getByRole('link', { name: 'Connect classbeat-player to send this class' })
  await expect(link).toHaveAttribute('href', /\/account\/player$/)
  await expect(page.getByRole('button', { name: 'Send to player' })).toHaveCount(0)
  expect(state.sends).toHaveLength(0)
})

test('Class page: Send to player asks first, then sends ids and a hash only', async ({}, testInfo) => {
  state.status = 'connected'
  state.outcome = 'completed'
  await page.goto(`/classes/${CLASS}`)
  await page.getByRole('button', { name: 'Send to player' }).click()
  const confirm = page.getByRole('group', { name: 'Send to classbeat-player' })
  await expect(confirm).toContainText('replaces the player’s copy')
  expect(state.sends).toHaveLength(0)
  await testInfo.attach('class-confirm', { body: await page.screenshot(), contentType: 'image/png' })

  await confirm.getByRole('button', { name: 'Send to player' }).click()
  await expect(page.getByText('Sent to classbeat-player: version 1.')).toBeVisible()
  expect(state.sends).toHaveLength(1)
  const sent = state.sends[0] as { kind: string; input: Record<string, string> }
  expect(sent.kind).toBe('send_to_player')
  expect(Object.keys(sent.input).sort()).toEqual(['class_id', 'content_hash', 'revision_id'])
  expect(sent.input.content_hash).toMatch(/^[0-9a-f]{64}$/)
  await testInfo.attach('class-sent', { body: await page.screenshot(), contentType: 'image/png' })
})

for (const outcome of ['player_ahead', 'player_conflict'] as const) {
  test(`Class page: ${outcome} is worded and sends nothing again by itself`, async ({}, testInfo) => {
    state.status = 'connected'
    state.outcome = outcome
    const before = state.sends.length
    await page.goto(`/classes/${CLASS}`)
    await page.getByRole('button', { name: 'Send to player' }).click()
    await page.getByRole('group', { name: 'Send to classbeat-player' }).getByRole('button', { name: 'Send to player' }).click()
    const alert = page.getByRole('alert').filter({ hasText: /player/ })
    await expect(alert).toContainText(
      outcome === 'player_ahead' ? 'The player already had a newer copy.' : 'This class changed on the player.',
    )
    await expect(alert).not.toContainText(outcome)
    await testInfo.attach(`class-${outcome}`, { body: await page.screenshot(), contentType: 'image/png' })
    await page.getByRole('button', { name: 'Send again' }).click()
    await expect(page.getByRole('group', { name: 'Send to classbeat-player' })).toBeVisible()
    expect(state.sends.length).toBe(before + 1)
  })
}
