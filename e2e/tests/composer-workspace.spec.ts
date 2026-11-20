import type { Page } from '@playwright/test'
import { expect, test } from './helpers'
import { PLAYLISTS, fakeMowl } from './design-helpers'
import { saveArm, savePrograms, saveState, classId } from './class-helpers'
import { LONG, holdHandoff, instructorWithMowl } from './ai-first-helpers'
import { useModelScript } from './model-script'
import { fakePlaylists, sql, type FakePlaylist } from './song-helpers'
import { USER_A, control, resetSpotify } from './spotify-helpers'

// ADR 0038 section 10.2 and issue #1191: the composer class workspace end to end on the fakes with
// the flag on. One attached Spotify playlist and one pinned song are described; a question is
// answered, the shape is changed, a song is swapped, and the class is approved and saved. Exactly
// one MOWL save and one final Spotify playlist may result, and nothing is written to MOWL, nor is a
// final playlist made, before Approve (the working playlist is classbeat's own preview copy).
//
// The model scripts are `backend/tests/agent_scripts/composer_*.json`. They are made for this
// world (the fake Spotify's invented catalogue, one attached playlist holding catalogue track 31,
// the pinned "Signal Harbor 18" at "the climb") and `backend/tests/test_composer_e2e_world.py` runs
// them through the real fill, swap, approval and phase 2 on that same world, so a mismatch fails
// there first.
//
// The web's driver starts the sketch, the fill and the pending messages by itself, and the worker
// reads its script link anew for every job. So each switch of the link is made while no job is
// about to start: the sketch's hand-off is held until the link is switched (`holdHandoff`), the
// driver's fill is answered `class_busy` by the browser (the driver tries again when the page is
// loaded again) until the shape is final, and a script is switched only after the earlier job is
// finished, which the class answer shows (`revision_count` and no open job).

const READ = 'A hard climbing ride. Put Signal Harbor 18 by The Invented Band on the climb.'
const PINNED = 'Signal Harbor 18'
const PIN_URI = 'spotify:track:FakeTrack0000000000018'
const FAVOURED_URI = 'spotify:track:FakeTrack0000000000031'
const ANSWER = 'Make the class 60 minutes long.'
const CHANGE = 'Make the last climb longer.'
// The song the swap script replaces: slot 4 (position 3, 0-based). Found by name: the rows need not follow the slots.
const SWAPPED = 'Summit Neon 1'
const WORKING_COPY = 'classbeat working copy'

/** The stored admin flag `composer_flow` (ADR 0038 section 2): only a stored `on` switches it on under the switch. */
function storeComposer(value: 'on' | null): void {
  if (value === null) {
    sql("DELETE FROM app_settings WHERE key = 'composer_flow'")
    return
  }
  sql(
    `INSERT INTO app_settings (key, value) VALUES ('composer_flow', '"${value}"') ` +
      `ON CONFLICT (key) DO UPDATE SET value = '"${value}"'`,
  )
}

// The song_picking flag (ADR 0011 section 7) is "granted" by default: only users whose plan has the
// feature may pick. As song-pick.spec.ts does, admit everyone and put it back after.
test.beforeAll(() => {
  sql(
    `INSERT INTO app_settings (key, value) VALUES ('song_picking', '"on"') ` +
      `ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`,
  )
})

test.beforeEach(() => {
  resetSpotify()
  // The attached playlist: the fake MOWL's "E2E Draft Climb", holding catalogue track 31 (the fill's favoured song).
  control(
    'POST',
    `playlist?owner=${USER_A}&name=${encodeURIComponent(PLAYLISTS.draft.name)}&id=${PLAYLISTS.draft.id}&tracks=1&first=31`,
  )
  fakeMowl('arm', PLAYLISTS.draft.id)
  saveArm('', true) // no programs, no injected failures
  storeComposer('on')
})

test.afterEach(() => {
  storeComposer(null)
  resetSpotify()
  // The worker's script link is shared by the whole stack: put it back on the default that the
  // specs which never set a script (design-draft.spec.ts) rely on.
  useModelScript('good_first_draft')
})

test.afterAll(() => {
  sql(`DELETE FROM app_settings WHERE key = 'song_picking'`)
})

/** The working playlist consent (ADR 0008): the fill writes classbeat's working copy in the user's Spotify. */
async function allowWorkingPlaylist(page: Page): Promise<void> {
  await page.goto('/account/spotify')
  await page.getByRole('switch', { name: 'Allow the working playlist' }).click()
  await expect(page.getByTestId('spotify-consent-state')).toHaveText('You have allowed the working playlist.')
}

/** Answers the web driver's fill `class_busy` until `open()`, so the shape can be changed first. */
async function holdFill(page: Page): Promise<{ open: () => void }> {
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

interface ClassView {
  revision_count: number
  open_job: unknown
}

/** Waits until the class holds `revisions` revisions and no job is open: the earlier job is done. */
async function settled(page: Page, id: string, revisions: number): Promise<void> {
  await expect
    .poll(
      async () => {
        const view = (await (await page.request.get(`/api/classes/${id}`)).json()) as ClassView
        return view.revision_count === revisions && (view.open_job ?? null) === null
      },
      LONG,
    )
    .toBe(true)
}

/** The playlists the fake Spotify holds that are not classbeat's working copy. */
const kept = (): FakePlaylist[] => fakePlaylists().filter((p) => p.name !== WORKING_COPY)
const finals = (): FakePlaylist[] => kept().filter((p) => p.id !== PLAYLISTS.draft.id)
/** Everything classbeat made in the user's Spotify is public and never collaborative (roadmap Amendment 30,
 * AGENTS.md), the working copy too. */
const allMadePublic = (): void => {
  for (const p of fakePlaylists().filter((q) => q.id !== PLAYLISTS.draft.id)) {
    expect(p.public, p.name).toBe(true)
    expect(p.collaborative, p.name).toBe(false)
  }
}

test('describe with a playlist and a pinned song, shape it, swap a song, approve and save: one MOWL save, one Spotify playlist, no write before Approve', async ({
  page,
}) => {
  test.setTimeout(420_000)
  await instructorWithMowl(page, 'composer')
  await allowWorkingPlaylist(page)

  // Describe: the text and the attached playlist. The reading comes first; its hand-off (the sketch)
  // waits until the script is switched.
  useModelScript('composer_read')
  const held = await holdHandoff(page)
  const fill = await holdFill(page)
  await page.goto('/classes/new')
  await page.getByLabel('Describe your class').fill(READ)
  await page.getByRole('button', { name: /^Add music/ }).click()
  await page
    .getByRole('group', { name: 'Add music to your request' })
    .getByRole('button', { name: PLAYLISTS.draft.name })
    .click()
  await page.getByRole('button', { name: /^Start/ }).click()
  await expect(page).toHaveURL(/\/classes\/[0-9a-f-]{36}$/)
  const id = classId(page)

  // The reading with its pinned song and its question; then the sketch.
  const reading = page.getByTestId('reading-card')
  await expect(reading.getByText(PINNED).first()).toBeVisible(LONG)
  useModelScript('composer_sketch')
  held.release()
  await expect(page.getByText('Rough: songs not in yet').first()).toBeVisible(LONG)
  await settled(page, id, 1)

  // Nothing is written before the approval: no MOWL program, no final playlist, the attached one untouched.
  const nothingYet = () => {
    expect(saveState().writes).toBe(0)
    expect(finals()).toHaveLength(0)
    expect(kept().find((p) => p.id === PLAYLISTS.draft.id)?.entries).toEqual([FAVOURED_URI])
  }
  nothingYet()

  // Answer the question with a quick answer: its prepared sentence is sent as a change.
  useModelScript('composer_sketch_revised')
  await reading.getByRole('button', { name: '60 minutes' }).click()
  await expect(page.getByText(ANSWER).first()).toBeVisible(LONG)
  await settled(page, id, 2)

  // Change the shape in the chat (a second change, with its own script).
  useModelScript('composer_sketch_again')
  await page.getByLabel('Tell classbeat what to change').fill(CHANGE)
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText(CHANGE).first()).toBeVisible(LONG)
  await settled(page, id, 3)
  nothingYet()

  // The shape is final: the songs come in. The driver's fill goes through when the page is loaded again.
  useModelScript('composer_fill')
  fill.open()
  await page.reload()
  await expect(page.getByText('Ready to approve').first()).toBeVisible(LONG)
  const list = page.getByTestId('segment-list')
  await expect(list.getByText(PINNED).first()).toBeVisible()
  await expect(list.getByText('Pinned by you')).toBeVisible()
  await expect(list.getByText('From your playlist')).toBeVisible()
  nothingYet()

  // Swap one song: the waiting version is replaced, still not written to MOWL or made a final playlist.
  const swaps = page.getByRole('button', { name: /^Swap / })
  const rows = await swaps.count()
  expect(rows).toBeGreaterThan(0)
  const swap = page.getByRole('button', { name: `Swap ${SWAPPED}`, exact: true })
  await expect(swap).toBeVisible()
  useModelScript('composer_swap')
  await swap.click()
  // The new list is in when the old song is gone and the rows are back (never while the job runs).
  await expect(swap).toHaveCount(0, LONG)
  await expect(swaps).toHaveCount(rows, LONG)
  await expect(page.getByText('Ready to approve').first()).toBeVisible(LONG)
  await expect(list.getByText(PINNED).first()).toBeVisible()
  nothingYet()

  // Approve and save: the bar's copy, then one click. Phase 2 makes the playlist; the web saves.
  await expect(
    page.getByText(
      'Approving saves a private workout to MOWL and creates a playlist in your Spotify in this order (a public playlist, so MOWL can find it).',
    ),
  ).toBeVisible()
  await page.getByRole('button', { name: 'Approve and save' }).click()
  await expect(page.getByText('Saved', { exact: true }).first()).toBeVisible(LONG)

  // One save to MOWL (the fake counts the programs per account) and one final, public playlist
  // that holds the pinned and the favoured song; the attached playlist is as it was.
  expect(saveState().programs.reduce((sum, n) => sum + n, 0)).toBe(1)
  expect(savePrograms()).toHaveLength(1)
  const [made, ...others] = finals()
  expect(others).toHaveLength(0)
  expect(made).toBeDefined()
  allMadePublic()
  expect(made?.entries).toContain(PIN_URI)
  expect(made?.entries).toContain(FAVOURED_URI)
  expect(kept().find((p) => p.id === PLAYLISTS.draft.id)?.entries).toEqual([FAVOURED_URI])
  // The class holds a draft that is saved: no Save button, only Update when it changes.
  await expect(page.getByRole('button', { name: 'Save to MOWL' })).toHaveCount(0)
})
