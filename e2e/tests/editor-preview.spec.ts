import { type Page } from '@playwright/test'
import { expect, test } from './helpers'
import { sql } from './song-helpers'
import { resetSpotify } from './spotify-helpers'
import { resetSpotdown } from './spotdown-helpers'
import { LONG, designOnSpotdown, newStudioUser } from './studio-flow-helpers'
import { resetSwedebeat } from './swedebeat-helpers'

// The audio preview (ADR 0042 section 6, CP-R3) end to end on the fakes: a studio class designed on
// spotdown's maps is opened in the editor, Play asks for a listening session and the first window,
// the playing window is moved to its last seconds, and playback continues into the next window of the
// SAME session (the next window starts where the previous one ended). Pause ends the session and the
// next Play starts a new one. The fake gateway downloads generated silence, the worker renders the
// windows and the web serves them from the preview volume (compose.e2e.yaml). Nothing real is reached.

test.describe.configure({ mode: 'serial' })
test.setTimeout(420_000)

test.beforeAll(() => {
  sql(
    `INSERT INTO app_settings (key, value) VALUES ('song_picking', '"on"') ` +
      `ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`,
  )
})

test.beforeEach(() => {
  resetSpotify()
  resetSwedebeat()
  resetSpotdown()
})

test.afterAll(() => {
  resetSpotify()
  resetSwedebeat()
  resetSpotdown()
  sql(`DELETE FROM app_settings WHERE key = 'song_picking'`)
})

const sessions = (owner: string) => Number(sql(`SELECT count(*) FROM preview_sessions WHERE owner_id = '${owner}'`))
const clips = (owner: string) => Number(sql(`SELECT count(*) FROM preview_clips WHERE owner_id = '${owner}'`))

/** The window a clip request answered (the browser's one-byte probe), from its headers; a 202 (still rendering) is skipped. */
async function windowOf(page: Page, startMs: number): Promise<{ startMs: number; endMs: number }> {
  const response = await page.waitForResponse(
    (r) => r.url().includes('/preview/clip?') && new URL(r.url()).searchParams.get('start_ms') === String(startMs) && (r.status() === 206 || r.status() === 200),
    LONG,
  )
  return {
    startMs: Number(await response.headerValue('X-Preview-Start-Ms')),
    endMs: Number(await response.headerValue('X-Preview-End-Ms')),
  }
}

test('Play continues across a window end in the same session; Pause ends it and Play starts a new one', async ({ page }) => {
  const owner = await newStudioUser(page, 'preview')
  const classId = await designOnSpotdown(page, owner, 'Preview e2e climb')

  await page.goto(`/classes/${classId}/edit`)
  await expect(page.getByTestId('editor-timeline')).toBeVisible(LONG)
  expect(sessions(owner)).toBe(0) // nothing is asked for before Play

  // Play: one session, and the window at the start (the render can take a moment: the page says so).
  const first = windowOf(page, 0)
  await page.getByRole('button', { name: 'Play' }).click()
  const { endMs } = await first
  expect(endMs).toBeGreaterThan(0)
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible(LONG)
  await expect(page.getByTestId('cursor')).toBeVisible(LONG)
  expect(sessions(owner)).toBe(1)

  // Move the playing window to its last seconds: the next window is asked for at its end, and playback
  // goes on there. The page draws its cursor from what the transport observed, never from the seek.
  const next = windowOf(page, endMs)
  await page.evaluate((seconds) => {
    const playing = document.querySelector<HTMLAudioElement>('audio[src]')
    if (playing === null) throw new Error('no audio element is playing')
    playing.currentTime = seconds
  }, endMs / 1000 - 4)
  const following = await next
  expect(following.startMs).toBe(endMs) // the window continues where the previous one ended
  await expect
    .poll(async () => Number(await page.getByTestId('cursor').getAttribute('data-cursor-ms')), LONG)
    .toBeGreaterThanOrEqual(endMs)
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()
  expect(sessions(owner)).toBe(1) // the same session
  expect(clips(owner)).toBeGreaterThanOrEqual(2)
  await expect(page.getByText(/Preview paused after 15 minutes/)).toHaveCount(0)

  // Pause ends the session; the next Play starts a new one.
  await page.getByRole('button', { name: 'Pause' }).click()
  await expect(page.getByRole('button', { name: 'Play' })).toBeVisible()
  await page.getByRole('button', { name: 'Play' }).click()
  await expect.poll(() => sessions(owner), LONG).toBe(2)
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible(LONG)
})
