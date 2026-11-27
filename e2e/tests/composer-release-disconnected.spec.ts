import { expect, test } from './helpers'
import { classId, saveState } from './class-helpers'
import { LONG, holdHandoff } from './ai-first-helpers'
import { useModelScript } from './model-script'
import { fakePlaylists } from './song-helpers'
import { fakeState, withSpotify } from './spotify-helpers'
import {
  REQUEST,
  SEVEN,
  answerMusic,
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
// the edited sketch and the way on to the songs (connect Spotify) are both shown, with no song search
// started (the fake Spotify and the fake MOWL saw no write). The music was answered before the sketch,
// as the reading asked (a typed style, #1538: songs are never picked without a hint). Then Spotify is
// connected, the consent card explains the working playlist and is allowed, and the fill designs the
// songs on the edited ride. The model scripts are
// `backend/tests/agent_scripts/composer_release_*.json` (run through the real pipeline by
// `backend/tests/test_composer_release_scripts.py`).
//
// The worker reads its script link anew for every job, so a script is switched only while no job is
// about to start: the hand-off waits for "Design this class" (`holdHandoff`), and the song search
// cannot start before Spotify and the consent, which come last.

composerStack()

const WORKING_COPY = 'classbeat working copy'
const searches = (): number => fakeState().requests.filter((r) => r.path.startsWith('/v1/search')).length

test('no Spotify: a typed style, the sketch, an edit and a reload; then connect, consent and the fill', async ({
  page,
}) => {
  test.setTimeout(420_000)
  await instructorWithoutSpotify(page, 'disconnected')

  // 1. The request without Spotify: the page says songs come later, and a sketch appears.
  useModelScript('composer_release_read')
  const held = holdHandoff(page)
  await page.goto('/classes/new')
  await page.getByLabel('Describe your class').fill(REQUEST)
  await expect(page.getByText('Songs need Spotify. You can connect it after the sketch.')).toBeVisible()
  await page.getByRole('button', { name: /^Start/ }).click()
  await expect(page).toHaveURL(/\/classes\/[0-9a-f-]{36}$/)
  const id = classId(page)
  await expect(page.getByTestId('reading-card')).toBeVisible(LONG)
  await answerMusic(page)
  useModelScript('composer_release_sketch')
  await held.release()
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

  // 3. Reload: the edited sketch and the way on to the songs (connect Spotify), and no song search.
  await page.reload()
  await expect(page.getByText('Rough: songs not in yet').first()).toBeVisible(LONG)
  const connect = page.getByTestId('music-connect')
  await expect(connect).toBeVisible(LONG)
  expect(effortsOf(await currentDraft(page, id))).toEqual(SEVEN_AND_THREE)
  const asked = await classView(page, id)
  expect(asked.music_decision?.state).toBe('decided') // the typed style answered it
  expect(asked.songs.state).toBe('none')
  expect(asked.fill_songs).toBeNull()
  expect(fakeState().requests).toEqual([]) // the fake Spotify was never asked anything
  expect(saveState().writes).toBe(0) // and the fake MOWL saw no write

  // 4. Connect (fake) Spotify. Nothing starts by itself: the consent comes first.
  await withSpotify(page)
  await page.goto(`/classes/${id}`)
  await expect(page.getByTestId('music-connect')).toHaveCount(0)

  // 5. The consent card explains the working playlist first.
  const consent = page.getByTestId('music-consent')
  await expect(consent).toBeVisible(LONG)
  await expect(consent).toContainText('Before the song search')
  await expect(consent).toContainText('keeps one public working playlist in your Spotify account')
  await expect(consent).toContainText('You have not allowed it yet.')
  // Still nothing written, no working playlist and no song search.
  expect(spotifyWrites()).toEqual([])
  expect(searches()).toBe(0)
  expect(fakePlaylists().map((p) => p.name)).toEqual([])
  expect(saveState().writes).toBe(0)
  expect((await classView(page, id)).fill_songs).toBeNull()

  // 6. Allow it: the fill starts by itself, reads the reference playlist and designs on the edited ride.
  const before = fakeState().requests.length
  useModelScript('composer_release_fill')
  await page.getByRole('switch', { name: 'Allow the working playlist' }).click()
  await expect(page.getByText('Ready to approve').first()).toBeVisible(LONG)
  expect(fakeState().requests.length).toBeGreaterThan(before) // the song search read the fake Spotify
  expect(fakePlaylists().some((p) => p.name === WORKING_COPY)).toBe(true) // the working copy, after consent
  const preview = await previewDraft(page, id)
  expect(preview.songs).toHaveLength(15)
  expect(effortsOf(preview.draft)).toEqual(SEVEN_AND_THREE) // the class with songs keeps the edited ride
  // Nothing final before the approval: no MOWL program, no final playlist.
  expect(saveState().writes).toBe(0)
  expect(fakePlaylists().filter((p) => p.name !== WORKING_COPY)).toHaveLength(0)
})
