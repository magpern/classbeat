import type { Page } from '@playwright/test'
import { expect, test } from './helpers'
import { fakeMowl, PLAYLISTS } from './design-helpers'
import { saveArm, signedInWithMowl } from './class-helpers'
import { LONG } from './ai-first-helpers'
import { useModelScript } from './model-script'
import { sql } from './song-helpers'
import { fakeState, resetSpotify } from './spotify-helpers'

// Shared by the two release specs of the composer (roadmap Amendment 36, #1497): the original
// request, the stack's switches, and what the page and the fakes say about a ride's shape. The
// model scripts are `backend/tests/agent_scripts/composer_release_*.json`; the backend suite
// (`test_composer_release_scripts.py`) runs them through the real pipeline on this same world.

/** The request of the issue, word for word (typos and all). */
export const REQUEST =
  'A 60 min class that starts off slow and builds up the momentum. A hilly section, that turns into a ' +
  'tough intervall section, thinking 5-7 short heavy bursts, back on flats, bulding up for a new hilly ' +
  'section, finishing of with a few bursts of power.'
export const SEVEN = 'Make it seven bursts.'
/** Section 3 of the ride (index 2): the middle efforts; section 6 (index 5): the finishing block. */
export const MIDDLE = 2
export const FINISH = 5
/** What each section holds after "make it seven bursts": the finish keeps its three. */
export const SEVEN_AND_THREE = [7, 3]

/** The stored admin flag `composer_flow` (ADR 0038 section 2): only a stored `on` switches it on. */
export function storeComposer(value: 'on' | null): void {
  if (value === null) {
    sql("DELETE FROM app_settings WHERE key = 'composer_flow'")
    return
  }
  sql(
    `INSERT INTO app_settings (key, value) VALUES ('composer_flow', '"${value}"') ` +
      `ON CONFLICT (key) DO UPDATE SET value = '"${value}"'`,
  )
}

/** The hooks both specs share: the song_picking flag granted to everyone (as song-pick.spec.ts does),
 *  the composer on, the fakes fresh; and everything put back after. */
export function composerStack(): void {
  test.beforeAll(() => {
    sql(
      `INSERT INTO app_settings (key, value) VALUES ('song_picking', '"on"') ` +
        `ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`,
    )
  })
  test.beforeEach(() => {
    resetSpotify()
    fakeMowl('arm', PLAYLISTS.draft.id)
    saveArm('', true) // no programs, no injected failures
    storeComposer('on')
  })
  test.afterEach(() => {
    storeComposer(null)
    resetSpotify()
    // The worker's script link is shared by the whole stack: back on the default the specs that
    // never set a script rely on.
    useModelScript('good_first_draft')
  })
  test.afterAll(() => {
    sql(`DELETE FROM app_settings WHERE key = 'song_picking'`)
  })
}

interface ClassView {
  current_revision_id: string | null
  revision_count: number
  open_job: unknown
  composer: boolean
  fill_songs: unknown
  songs: { state: string; revision_id: string | null }
  music_decision: { state: string; choice: string; playlists: number } | null
}

export async function classView(page: Page, id: string): Promise<ClassView> {
  return (await (await page.request.get(`/api/classes/${id}`)).json()) as ClassView
}

/** Waits until the class holds `revisions` revisions and no job is open: the earlier job is done. */
export async function settled(page: Page, id: string, revisions: number): Promise<void> {
  await expect
    .poll(
      async () => {
        const view = await classView(page, id)
        return view.revision_count === revisions && (view.open_job ?? null) === null
      },
      LONG,
    )
    .toBe(true)
}

interface Interval {
  sec: number
  zone: number
}
export interface Draft {
  segments: { name: string; kind: string; section?: number; intervals: Interval[] }[]
}

/** The efforts of a section: intervals of zone 5 or more and at most 90 s, in the segments tagged with it. */
export function efforts(draft: Draft, section: number): number {
  return draft.segments
    .filter((s) => s.section === section)
    .flatMap((s) => s.intervals)
    .filter((i) => i.zone >= 5 && i.sec <= 90).length
}

export const effortsOf = (draft: Draft): number[] => [efforts(draft, MIDDLE), efforts(draft, FINISH)]

/** The class's current revision (its sketch, or a draft) as the page reads it. */
export async function currentDraft(page: Page, id: string): Promise<Draft> {
  const view = await classView(page, id)
  expect(view.current_revision_id).not.toBeNull()
  const answer = await page.request.get(`/api/classes/${id}/revisions/${view.current_revision_id}`)
  expect(answer.ok()).toBe(true)
  return ((await answer.json()) as { content: Draft }).content
}

/** The design on the waiting song list (its `preview`, the class on its songs before the approval). */
export async function previewDraft(
  page: Page,
  id: string,
): Promise<{ draft: Draft; songs: { source: { kind: string; playlist?: number } | null }[] }> {
  const view = await classView(page, id)
  expect(view.songs.revision_id).not.toBeNull()
  const answer = await page.request.get(`/api/classes/${id}/revisions/${view.songs.revision_id}`)
  expect(answer.ok()).toBe(true)
  const list = (await answer.json()) as {
    preview: { content: Draft } | null
    songs: { source: { kind: string; playlist?: number } | null }[]
  }
  expect(list.preview).not.toBeNull()
  return { draft: list.preview!.content, songs: list.songs }
}

/**
 * The efforts of a saved program's two blocks, as the fake MOWL holds it (no section tags there): a
 * heavy effort is zone 6 at most 90 s; the middle block ends at the first zone 3 interval after it
 * (the flat road), and what follows is the finish.
 */
export function savedEfforts(zones: number[], seconds: number[]): number[] {
  const heavy = (n: number): boolean => zones[n]! >= 5 && seconds[n]! <= 90
  const first = zones.findIndex((_, n) => heavy(n))
  expect(first).toBeGreaterThanOrEqual(0)
  const flat = zones.findIndex((z, n) => n > first && z === 3)
  expect(flat).toBeGreaterThan(first)
  const count = (from: number, to: number): number => zones.slice(from, to).filter((_, n) => heavy(from + n)).length
  return [count(first, flat), count(flat, zones.length)]
}

/** What the fake Spotify has been asked to change: any request that is not a read (the sign-in's
 *  token exchange is not a write to the account). */
export function spotifyWrites(): string[] {
  return fakeState()
    .requests.filter((r) => r.method !== 'GET' && !/token/.test(r.path))
    .map((r) => `${r.method} ${r.path}`)
}

/** A signed-in user with MOWL connected on the instructor plan (75 minutes) and **no** Spotify. */
export async function instructorWithoutSpotify(page: Page, tag: string): Promise<void> {
  await signedInWithMowl(page, tag)
  const me = (await (await page.request.get('/api/auth/session')).json()) as { user?: { id?: string } }
  const id = me.user?.id
  if (id === undefined || !/^[0-9a-f-]{36}$/.test(id)) throw new Error('no signed-in user id')
  sql(`INSERT INTO entitlements (owner_id, plan_id, source) VALUES ('${id}', 'instructor', 'grant')`)
}

/** Answers the web driver's fill `class_busy` until `open()`, so the shape can be changed first. */
export async function holdFill(page: Page): Promise<{ open: () => void }> {
  let closed = true
  await page.route('**/api/classes/*/fill-songs', async (route) => {
    if (!closed) {
      await route.continue()
      return
    }
    await route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({ detail: { reason: 'class_busy' } }),
    })
  })
  return {
    open: () => {
      closed = false
    },
  }
}

/**
 * The confirm step of a reading with no hint of what to play (#1538): the reading asks what music the
 * class should have and "Design this class" stays off until it is answered. The instructor types a style,
 * the request is read again (with the styled script, which finds that taste) and the new reading asks
 * nothing. Returns with the reading ready to be designed.
 */
export async function answerMusic(page: Page): Promise<void> {
  const reading = page.getByTestId('reading-card')
  await expect(reading.getByRole('button', { name: 'Design this class' })).toBeDisabled({ timeout: 90_000 })
  await reading.getByLabel('Or type your own answer').fill('rock')
  useModelScript('composer_release_read_styled')
  await reading.getByRole('button', { name: 'Update the reading' }).click()
  await expect(reading.getByLabel('Or type your own answer')).toHaveCount(0, { timeout: 90_000 })
  await expect(reading.getByRole('button', { name: 'Design this class' })).toBeEnabled()
}

/**
 * The confirm step of a Spotify-connected instructor whose reading has no hint (#1538): the instructor
 * answers "Use one of my playlists", picks the e2e playlist in the picker and updates the reading. The
 * playlist goes with the re-read (`music_sources`), so the new reading has a hint and asks nothing.
 */
export async function answerWithPlaylist(page: Page): Promise<void> {
  const reading = page.getByTestId('reading-card')
  await expect(reading.getByRole('button', { name: 'Design this class' })).toBeDisabled({ timeout: 90_000 })
  await reading.getByRole('radio', { name: 'Use one of my playlists' }).click()
  await reading.getByRole('button', { name: /^Add music/ }).click()
  await page.getByRole('group', { name: 'Add music to your request' }).getByRole('button', { name: PLAYLISTS.draft.name }).click()
  await page.keyboard.press('Escape')
  await reading.getByRole('button', { name: 'Update the reading' }).click()
  await expect(reading.getByRole('radio', { name: 'Use one of my playlists' })).toHaveCount(0, { timeout: 90_000 })
  await expect(reading.getByRole('button', { name: 'Design this class' })).toBeEnabled()
}
