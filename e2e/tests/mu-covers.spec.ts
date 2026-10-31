import { test, expect, type BrowserContext, type Page, type Request } from '@playwright/test'
import { signInThroughUi, signUpThroughUi, uniqueEmail } from './helpers'
import { audit, stackContext, watchConsole } from './mu-helpers'

// MU done-when (roadmap Amendment 8, ADR 0013 section 9), part 2: covers load only from
// i.scdn.co, through CoverArt, behind the CSP header, and the Spotify brand images are the only
// other images (from our own bundle). The Songs for approval page is the real SPA on the stack
// with its API answers (one job, one class, one song list) made by the test, because the
// stack's fakes do not run a song pick; the policy under test is the real header of the real web
// process. The cover host is answered by the test (a 1x1 PNG): nothing leaves the machine.
test.describe.configure({ mode: 'serial' })
test.setTimeout(180_000)

const JOB = '11111111-1111-4111-8111-111111111111'
const CLASS = '22222222-2222-4222-8222-222222222222'
const LIST = '55555555-5555-4555-8555-555555555555'
const HASH = 'ab'.repeat(32)
const COVER = (n: number) => `https://i.scdn.co/image/${n.toString(16).padStart(40, 'a')}`
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
)

/** What the page is given as the song list: three songs with a good cover, one whose cover is
 *  on another host, one with a cover that is not an image URL, and one with none. */
const SONGS = [
  { title: 'Hills', cover: COVER(1) },
  { title: 'Rolling', cover: COVER(2) },
  { title: 'Valley', cover: COVER(3) },
  { title: 'Elsewhere', cover: `https://evil.example/image/${'a'.repeat(40)}` },
  { title: 'Not a cover', cover: 'https://i.scdn.co/image/../../x.png' },
  { title: 'Plain', cover: null },
].map((song, index) => ({
  position: index + 1,
  title: song.title,
  artist: 'Ann',
  role: index === 0 ? 'warm_up' : 'climb',
  bpm: 100 + index,
  length_sec: 200,
  new: false,
  cover: song.cover,
}))

let context: BrowserContext
let page: Page
let consoleErrors: string[]
/** Every request that left the page for an origin other than the stack's. */
const foreign: Request[] = []

test.beforeAll(async ({ browser, baseURL }) => {
  context = await stackContext(browser, baseURL!)
  page = await context.newPage()
  consoleErrors = watchConsole(page)
  const stack = new URL(baseURL!).origin
  page.on('request', (request) => {
    if (new URL(request.url()).origin !== stack) foreign.push(request)
  })
  // The cover host answers here (a page route runs before the context's blocking route).
  await page.route('https://i.scdn.co/**', (route) => route.fulfill({ contentType: 'image/png', body: PNG }))

  const json = (body: unknown) => ({ contentType: 'application/json', body: JSON.stringify(body) })
  await page.route(`**/api/jobs/${JOB}/events`, (route) =>
    route.fulfill({ contentType: 'text/event-stream', body: ': keepalive\n\n' }),
  )
  await page.route(`**/api/jobs/${JOB}`, (route) =>
    route.fulfill(
      json({
        id: JOB,
        kind: 'design_with_songs',
        state: 'waiting_for_approval',
        stage: null,
        class_id: CLASS,
        cancel_requested: false,
        approval_revision_id: LIST,
        expires_at: '2026-11-06T09:00:00Z',
        approved_at: null,
        error_category: null,
        created_at: '2026-10-30T10:00:00Z',
        updated_at: '2026-10-30T10:00:00Z',
        finished_at: null,
      }),
    ),
  )
  await page.route(`**/api/classes/${CLASS}/revisions/${LIST}`, (route) =>
    route.fulfill(
      json({
        id: LIST,
        number: 2,
        kind: 'song_list',
        created_at: '2026-10-30T09:00:00Z',
        parent_id: null,
        parent_number: null,
        status: 'waiting',
        job_id: JOB,
        expires_at: '2026-11-06T09:00:00Z',
        approved_at: null,
        content_hash: HASH,
        mode: 'pick',
        target_sec: 1200,
        total_sec: 1200,
        crossfade_sec: 9,
        songs: SONGS,
        omitted_no_bpm: 0,
        note: '',
      }),
    ),
  )

  const email = uniqueEmail('covers')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
})

test.afterAll(async () => {
  await context.close()
})

test('Songs for approval: covers come only from i.scdn.co through CoverArt', async () => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const document = await page.goto(`/jobs/${JOB}`)
  await expect(page.getByRole('heading', { name: 'Songs for your class' })).toBeVisible()
  await expect(page.getByTestId('song')).toHaveCount(6)

  // The policy is the web process's header, and it names the cover host and no other.
  const csp = document!.headers()['content-security-policy'] ?? ''
  expect(csp).toContain("img-src 'self' https://i.scdn.co;")
  expect(document!.headers()['referrer-policy']).toBe('no-referrer')

  // Three songs show their cover; the three others show the neutral tile with initials.
  const images = page.getByTestId('song').locator('img')
  await expect(images).toHaveCount(3)
  const hosts = await images.evaluateAll((list) =>
    list.map((img) => ({
      host: new URL((img as HTMLImageElement).src).origin,
      referrerPolicy: (img as HTMLImageElement).referrerPolicy,
      loaded: (img as HTMLImageElement).complete && (img as HTMLImageElement).naturalWidth > 0,
    })),
  )
  expect(hosts).toEqual(Array(3).fill({ host: 'https://i.scdn.co', referrerPolicy: 'no-referrer', loaded: true }))
  await expect(page.getByTestId('song').getByTestId('cover-tile')).toHaveCount(3)

  // Nothing but the cover host was asked for, and what was asked carries no referrer or cookie.
  expect(new Set(foreign.map((request) => new URL(request.url()).origin))).toEqual(new Set(['https://i.scdn.co']))
  for (const request of foreign) {
    const headers = await request.allHeaders()
    expect(headers['referer'], request.url()).toBeUndefined()
    expect(headers['cookie'], request.url()).toBeUndefined()
  }
  expect(consoleErrors).toEqual([])

  // Every image in the page comes from the one host or from our own bundle (the Spotify logo).
  const stack = new URL(page.url()).origin
  const all = await page.locator('img').evaluateAll((list) => list.map((img) => (img as HTMLImageElement).src))
  for (const src of all) expect([stack, 'https://i.scdn.co'], src).toContain(new URL(src).origin)
  expect(all.filter((src) => src.startsWith(stack)).every((src) => /\/assets\/.*\.svg$/.test(src))).toBe(true)
})

test('the browser itself refuses an image from any other host, and a data: image', async () => {
  const before = foreign.length
  const verdicts = await page.evaluate(async () => {
    const results: Record<string, string> = {}
    const blocked: string[] = []
    document.addEventListener('securitypolicyviolation', (event) => {
      blocked.push(event.blockedURI)
    })
    const load = (name: string, src: string) =>
      new Promise<void>((resolve) => {
        const img = new Image()
        img.onload = () => {
          results[name] = 'loaded'
          resolve()
        }
        img.onerror = () => {
          results[name] = 'failed'
          resolve()
        }
        img.src = src
      })
    await load('other host', 'https://evil.example/pixel.png')
    await load('scdn subdomain', 'https://mosaic.scdn.co/pixel.png')
    await load('data uri', 'data:image/gif;base64,R0lGODlhAQABAAAAACw=')
    await load('cover host', 'https://i.scdn.co/image/' + 'a'.repeat(40))
    return { results, blocked }
  })
  expect(verdicts.results).toEqual({
    'other host': 'failed',
    'scdn subdomain': 'failed',
    'data uri': 'failed',
    'cover host': 'loaded',
  })
  expect(verdicts.blocked.sort()).toEqual(['data', 'https://evil.example/pixel.png', 'https://mosaic.scdn.co/pixel.png'])
  // The refused ones were stopped by the policy itself (not by this test's blocking route), and
  // only the cover host's request went out.
  const seen = foreign.slice(before)
  const failures = Object.fromEntries(
    seen.map((request) => [new URL(request.url()).origin, request.failure()?.errorText ?? 'sent']),
  )
  expect(failures).toEqual({
    'https://evil.example': 'csp',
    'https://mosaic.scdn.co': 'csp',
    'https://i.scdn.co': 'sent',
  })
  consoleErrors.length = 0
})

test('Songs for approval: viewports, accessibility and a quiet console', async () => {
  const result = await audit(page, 'songs for approval', page.getByRole('button', { name: 'Approve songs' }))
  expect(result.problems).toEqual([])
  expect(consoleErrors).toEqual([])
})

// ADR 0013 sections 8 and 9, the review of UI7: style-src stays 'self', so a library that injects
// a <style> element is blocked. The Dialog is the one such component a page uses (Delete my
// account); it has to open, trap and return focus, close on Escape and fit the window at both
// sizes, lock the page behind it with a class (not the inline style the CSP refuses), and leave
// the console empty.
test('a dialog under the CSP opens, fits the window and closes at 390 and 1440 px', async () => {
  await page.goto('/account#data')
  const trigger = page.getByRole('button', { name: /delete my account|delete account/i }).first()
  await expect(trigger).toBeVisible()
  for (const [width, height] of [
    [390, 844],
    [1440, 900],
  ] as const) {
    await page.setViewportSize({ width, height })
    await trigger.click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    expect(await page.evaluate(() => getComputedStyle(document.documentElement).overflow)).toBe('hidden')
    const box = (await dialog.boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(width + 0.5)
    expect(box.y).toBeGreaterThanOrEqual(0)
    expect(box.y + box.height).toBeLessThanOrEqual(height + 0.5)
    expect(await page.evaluate(() => document.activeElement?.closest('[role=dialog]') !== null)).toBe(true)
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
    await expect(trigger).toBeFocused()
    expect(await page.evaluate(() => getComputedStyle(document.documentElement).overflow)).not.toBe('hidden')
  }
  // The page scroll lock is a class, so the CSP refuses nothing: any console error fails.
  expect(consoleErrors).toEqual([])
})
