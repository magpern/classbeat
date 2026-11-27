import { expect, test } from './helpers'
import { classId, saveState, savePrograms, zonesOf } from './class-helpers'
import { LONG, holdHandoff, instructorWithMowl } from './ai-first-helpers'
import { useModelScript } from './model-script'
import { PLAYLISTS } from './design-helpers'
import { fakePlaylists } from './song-helpers'
import { USER_A, control, fakeState } from './spotify-helpers'
import {
  FINISH,
  MIDDLE,
  answerWithPlaylist,
  classView,
  holdFill,
  REQUEST,
  SEVEN,
  SEVEN_AND_THREE,
  composerStack,
  currentDraft,
  effortsOf,
  previewDraft,
  savedEfforts,
  settled,
  spotifyWrites,
} from './composer-release-helpers'

// Roadmap Amendment 36, release criterion B (#1497): "exactly seven". The original request is read as
// six parts, the middle efforts as 5 to 7; "Make it seven bursts." gives exactly seven efforts in
// section 3 and leaves the finishing block as it was, in the sketch after a reload, in the song preview
// and in the program saved to MOWL. The model scripts are `backend/tests/agent_scripts/composer_release_*.json`,
// run through the real pipeline by `backend/tests/test_composer_release_scripts.py`.
//
// The worker reads its script link anew for every job, so a script is switched only while no job is
// about to start: the sketch's hand-off waits for "Design this class" (`holdHandoff`), and the song
// search is held (`holdFill`) until this spec has edited the sketch. The reading has no hint of what to
// play, so it asks first (`answerWithPlaylist`, #1538): the instructor shares one of their playlists, which goes with the re-read and
// decides the music; the fill must then read that playlist and use one of its songs.

composerStack()

test('"Make it seven bursts": seven efforts in section 3 and the same finish in the sketch, the song preview and the saved program', async ({
  page,
}) => {
  test.setTimeout(420_000)
  await instructorWithMowl(page, 'seven') // Spotify connected
  await page.goto('/account/spotify')
  await page.getByRole('switch', { name: 'Allow the working playlist' }).click()
  await expect(page.getByTestId('spotify-consent-state')).toHaveText('You have allowed the working playlist.')

  // 1. The request: read as six parts in order, the middle efforts as 5 to 7.
  useModelScript('composer_release_read')
  const held = holdHandoff(page)
  const fill = await holdFill(page)
  // The instructor's playlist as the fake Spotify holds it (one song, the first of the catalogue's 31).
  control(
    'POST',
    `playlist?owner=${USER_A}&name=${encodeURIComponent(PLAYLISTS.draft.name)}&id=${PLAYLISTS.draft.id}&tracks=1&first=31`,
  )
  await page.goto('/classes/new')
  await page.getByLabel('Describe your class').fill(REQUEST)
  await page.getByRole('button', { name: /^Start/ }).click()
  await expect(page).toHaveURL(/\/classes\/[0-9a-f-]{36}$/)
  const id = classId(page)
  const reading = page.getByTestId('reading-card')
  const parts = reading.getByRole('region', { name: 'Your ride, part by part' }).getByRole('listitem')
  await expect(parts).toHaveCount(6, LONG)
  await expect(parts.nth(0)).toContainText('1. Warm-up, building up, easy')
  await expect(parts.nth(1)).toContainText('2. Climb')
  await expect(parts.nth(2)).toContainText('3. 5 to 7 short, heavy efforts, hard')
  await expect(parts.nth(3)).toContainText('4. Flat road')
  await expect(parts.nth(4)).toContainText('5. Climb, building up')
  await expect(parts.nth(5)).toContainText('6. 2 to 4 efforts')
  await answerWithPlaylist(page) // no hint of what to play: the reading asks, the playlist is read in
  await expect(parts).toHaveCount(6)

  // The sketch: six efforts in section 3 (inside 5 to 7) and three in the finish.
  useModelScript('composer_release_sketch')
  await held.release()
  await expect(page.getByText('Rough: songs not in yet').first()).toBeVisible(LONG)
  await settled(page, id, 1)
  expect(effortsOf(await currentDraft(page, id))).toEqual([6, 3])
  const finish = (await currentDraft(page, id)).segments.filter((s) => s.section === FINISH)

  // 2. "Make it seven bursts."
  useModelScript('composer_release_seven')
  await page.getByLabel('Tell classbeat what to change').fill(SEVEN)
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText(SEVEN).first()).toBeVisible(LONG)
  await settled(page, id, 2)

  // 3. In the reloaded sketch: exactly seven efforts in section 3, the finishing block unchanged.
  await page.reload()
  await expect(page.getByText('Rough: songs not in yet').first()).toBeVisible(LONG)
  const sketch = await currentDraft(page, id)
  expect(effortsOf(sketch)).toEqual(SEVEN_AND_THREE)
  expect(sketch.segments.filter((s) => s.section === FINISH)).toEqual(finish)
  // Nothing is written before the approval.
  expect(saveState().writes).toBe(0)
  expect(spotifyWrites()).toEqual([])

  // The music was answered with the style: the songs come in, designed on the same shape.
  useModelScript('composer_release_fill')
  fill.open()
  await page.reload()
  await expect(page.getByText('Ready to approve').first()).toBeVisible(LONG)
  const preview = await previewDraft(page, id)
  // The shared playlist reached the song search: its items were read, the class names it, and a song comes from it.
  expect(fakeState().requests.some((r) => r.method === 'GET' && r.path === `/v1/playlists/${PLAYLISTS.draft.id}/items`)).toBe(true)
  expect((await classView(page, id)).music_decision).toEqual({ state: 'decided', choice: 'request', playlists: 1 })
  expect(preview.songs[0]?.source).toEqual({ kind: 'playlist', playlist: 0 })
  expect(preview.songs).toHaveLength(15)
  expect(effortsOf(preview.draft)).toEqual(SEVEN_AND_THREE)
  expect(preview.draft.segments.filter((s) => s.section === FINISH).map((s) => s.intervals.map((i) => i.zone))).toEqual(
    finish.map((s) => s.intervals.map((i) => i.zone)),
  )
  expect(saveState().writes).toBe(0)
  expect(fakePlaylists().filter((p) => p.name !== 'classbeat working copy' && p.id !== PLAYLISTS.draft.id)).toHaveLength(0)

  // 4. Approve and save: the program in MOWL has seven efforts in its middle block and three at the end.
  await page.getByRole('button', { name: 'Approve and save' }).click()
  await expect(page.getByText('Saved', { exact: true }).first()).toBeVisible(LONG)
  const [program, ...others] = savePrograms()
  expect(others).toHaveLength(0)
  expect(program).toBeDefined()
  const zones = zonesOf(program!)
  expect(savedEfforts(zones, program!.intervals.map((i) => i[0]!))).toEqual(SEVEN_AND_THREE)
  expect(MIDDLE).toBeLessThan(FINISH)
})
