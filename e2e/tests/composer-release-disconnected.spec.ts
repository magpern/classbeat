import { expect, test } from './helpers'
import { PLAYLISTS } from './design-helpers'
import { classId, saveState } from './class-helpers'
import { LONG, holdHandoff } from './ai-first-helpers'
import { useModelScript } from './model-script'
import { fakePlaylists } from './song-helpers'
import { USER_A, control, fakeState, withSpotify } from './spotify-helpers'
import {
  REQUEST,
  SEVEN,
  SEVEN_AND_THREE,
  classView,
  composerStack,
  currentDraft,
  effortsOf,
  instructorWithoutSpotify,
  previewDraft,
  settled,
  spotifyWrites,
} from './composer-release-helpers'

// Roadmap Amendment 36, release criterion A (#1497): the disconnected instructor. No Spotify
// connection: the request is read and sketched, the sketch is edited in the chat, and after a reload
// the edited sketch and the unanswered music question are both shown, with no song search started (the
// fake Spotify and the fake MOWL saw no write). Then Spotify is connected, the answer shares a
// reference playlist, the consent card explains the working playlist and is allowed, and the fill
// reads that playlist and designs the songs on the edited ride. The model scripts are
// `backend/tests/agent_scripts/composer_release_*.json` (run through the real pipeline by
// `backend/tests/test_composer_release_scripts.py`).
//
// The worker reads its script link anew for every job, so a script is switched only while no job is
// about to start: the hand-off is held until the link is switched (`holdHandoff`), and the song search
// cannot start before the music answer and the consent, which come last.

composerStack()

const WORKING_COPY = 'classbeat working copy'
const searches = (): number => fakeState().requests.filter((r) => r.path.startsWith('/v1/search')).length

test('no Spotify: sketch, edit, reload and the open music question; then connect, consent and a reference playlist for the fill', async ({
  page,
}) => {
  test.setTimeout(420_000)
  await instructorWithoutSpotify(page, 'disconnected')

  // 1. The request without Spotify: the page says songs come later, and a sketch appears.
  useModelScript('composer_release_read')
  const held = await holdHandoff(page)
  await page.goto('/classes/new')
  await page.getByLabel('Describe your class').fill(REQUEST)
  await expect(page.getByText('Songs need Spotify. You can connect it after the sketch.')).toBeVisible()
  await page.getByRole('button', { name: /^Start/ }).click()
  await expect(page).toHaveURL(/\/classes\/[0-9a-f-]{36}$/)
  const id = classId(page)
  await expect(page.getByTestId('reading-card')).toBeVisible(LONG)
  useModelScript('composer_release_sketch')
  held.release()
  await expect(page.getByText('Rough: songs not in yet').first()).toBeVisible(LONG)
  await settled(page, id, 1)
  const first = await classView(page, id)
  expect(first.composer).toBe(true) // a sketch, not the older route's refusal
  expect(effortsOf(await currentDraft(page, id))).toEqual([6, 3])

  // 2. Edit the sketch in the chat.
  useModelScript('composer_release_seven')
  await page.getByLabel('Tell classbeat what to change').fill(SEVEN)
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText(SEVEN).first()).toBeVisible(LONG)
  await settled(page, id, 2)

  // 3. Reload: the edited sketch and the unanswered music question, and no song search.
  await page.reload()
  await expect(page.getByText('Rough: songs not in yet').first()).toBeVisible(LONG)
  const question = page.getByTestId('music-question')
  await expect(question).toBeVisible(LONG)
  await expect(question.getByTestId('music-needs-spotify')).toBeVisible()
  expect(effortsOf(await currentDraft(page, id))).toEqual(SEVEN_AND_THREE)
  const asked = await classView(page, id)
  expect(asked.music_decision?.state).toBe('undecided')
  expect(asked.songs.state).toBe('none')
  expect(asked.fill_songs).toBeNull()
  expect(fakeState().requests).toEqual([]) // the fake Spotify was never asked anything
  expect(saveState().writes).toBe(0) // and the fake MOWL saw no write

  // 4. Connect (fake) Spotify. The question is still open; nothing starts by itself.
  control(
    'POST',
    `playlist?owner=${USER_A}&name=${encodeURIComponent(PLAYLISTS.draft.name)}&id=${PLAYLISTS.draft.id}&tracks=1&first=31`,
  )
  await withSpotify(page)
  await page.goto(`/classes/${id}`)
  await expect(question).toBeVisible(LONG)
  await expect(question.getByTestId('music-needs-spotify')).toHaveCount(0)

  // 5. Answer with a reference playlist: the consent card explains the working playlist first.
  await question.getByRole('button', { name: 'Share playlists' }).click()
  await question.getByRole('button', { name: /^Add music/ }).click()
  await page.getByRole('button', { name: PLAYLISTS.draft.name }).click()
  await page.keyboard.press('Escape')
  await question.getByRole('button', { name: 'Share these playlists' }).click()
  const consent = page.getByTestId('music-consent')
  await expect(consent).toBeVisible(LONG)
  await expect(consent).toContainText('Before the song search')
  await expect(consent).toContainText('keeps one public working playlist in your Spotify account')
  await expect(consent).toContainText('You have not allowed it yet.')
  const decided = await classView(page, id)
  expect(decided.music_decision).toEqual({ state: 'decided', choice: 'playlists', playlists: 1 })
  // Still nothing written, no working playlist and no song search.
  expect(spotifyWrites()).toEqual([])
  expect(searches()).toBe(0)
  expect(fakePlaylists().map((p) => p.name)).toEqual([PLAYLISTS.draft.name])
  expect(saveState().writes).toBe(0)
  expect((await classView(page, id)).fill_songs).toBeNull()

  // 6. Allow it: the fill starts by itself, reads the reference playlist and designs on the edited ride.
  const before = fakeState().requests.length
  useModelScript('composer_release_fill')
  await page.getByRole('switch', { name: 'Allow the working playlist' }).click()
  await expect(page.getByText('Ready to approve').first()).toBeVisible(LONG)
  const after = fakeState().requests.slice(before)
  expect(after.some((r) => r.method === 'GET' && r.path === `/v1/playlists/${PLAYLISTS.draft.id}/items`)).toBe(true)
  expect(fakePlaylists().some((p) => p.name === WORKING_COPY)).toBe(true) // the working copy, after consent
  const preview = await previewDraft(page, id)
  expect(preview.songs).toHaveLength(15)
  expect(preview.songs[0]?.source).toEqual({ kind: 'playlist', playlist: 0 }) // the reference playlist's song
  expect(effortsOf(preview.draft)).toEqual(SEVEN_AND_THREE) // the class with songs keeps the edited ride
  // Nothing final before the approval: no MOWL program, no final playlist.
  expect(saveState().writes).toBe(0)
  expect(fakePlaylists().filter((p) => ![PLAYLISTS.draft.name, WORKING_COPY].includes(p.name))).toHaveLength(0)
})
