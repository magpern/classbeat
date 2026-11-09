import { expect, test } from './helpers'
import { PLAYLISTS, fakeControl, fakeMowl, getJob } from './design-helpers'
import { savedShape, useModelScript, type ModelScript } from './model-script'
import {
  DRAFT_PLAYLIST_MOWL_ID,
  LONG,
  classId,
  confirmWrite,
  designAndOpen,
  reviseByChat,
  saveArm,
  savePrograms,
  savePanel,
  savedState,
  signedInWithMowl,
  startSoundlessDesign,
  zonesOf,
  type FakeProgram,
} from './class-helpers'
import { audit } from './mu-helpers'
import { fakeState, resetSpotify } from './spotify-helpers'
import { sql } from './song-helpers'

// SL7 (issue #533, ADR 0007 amendment of 2026-11-03 "soundless classes"): a class with no music is
// designed, revised by chat, saved and updated on the fakes. The stack turns the environment switch
// on for web and worker (compose.e2e.yaml) and gives the web the `fake` provider, which counts as
// covering every plan's route outside prod; nothing is stored (the admin flag was retired, #824).
// The worker replays the recorded soundless scripts (model-script.ts). The tests share the fakes'
// state and the worker's script, so they run one after the other.
//
// UNVERIFIED against real MOWL: what MOWL does with `PlaylistID: -1` (accepts it as "no music",
// reads it back as -1 or as null, and whether an update sending -1 clears a playlist the user
// attached in the app). The fake MOWL stores -1 as written and serves it back as written unless a
// test turns its `save-no-music` knobs; nothing here shows how the real service behaves. The owner's
// live check settles it (ADR 0007 amendment, item 8).
test.describe.configure({ mode: 'serial' })
test.setTimeout(240_000)

const ALL_PLAYLISTS = Object.values(PLAYLISTS)

test.beforeEach(() => {
  saveArm('', true) // no programs, no injected failures, the fake's -1 read-back as written
  useModelScript('good_first_draft')
})

test.afterAll(() => {
  saveArm('', true)
  useModelScript('good_first_draft')
})

interface MusicCalls {
  /** Per test playlist: attach calls and reads (tracks, availability, sections) at the fake MOWL. */
  playlists: { attaches: number; reads: Record<string, number> }[]
  /** Calls to the fake MOWL's playlist list. */
  listings: number
  /** The fake MOWL's Swedebeat counts: listing, attach and temporary links. */
  swedebeat: { lists: number; attaches: number; links: number }
  /** Requests the fake Spotify has seen. */
  spotify: number
  /** Rows of classbeat's playlist links (Spotify playlist to MOWL playlist, ADR 0008). */
  links: number
}

/** Zero every counter the fakes keep for music, once the page that lists playlists has loaded. */
function armMusicCounters(): void {
  for (const playlist of ALL_PLAYLISTS) fakeMowl('arm', playlist.id)
  fakeControl('POST', 'list-arm')
  fakeControl('POST', 'swedebeat-arm')
  resetSpotify()
}

function musicCalls(): MusicCalls {
  return {
    playlists: ALL_PLAYLISTS.map((p) => {
      const state = fakeMowl('state', p.id)
      return { attaches: state.attaches, reads: { ...state.reads } }
    }),
    listings: fakeControl<{ calls: number }>('GET', 'list-state').calls,
    swedebeat: fakeControl<{ lists: number; attaches: number; links: number }>('GET', 'swedebeat-state'),
    spotify: fakeState().requests.length,
    links: Number(sql('SELECT count(*) FROM mowl_playlist_links')),
  }
}

/** No attach, no playlist list, no track, availability or section read, no Swedebeat call and no
 *  Spotify request since `armMusicCounters`; the link rows are the `links` count it was given. */
function expectNoMusicCalls(linksBefore: number): void {
  const calls = musicCalls()
  expect(calls.playlists).toEqual(ALL_PLAYLISTS.map(() => ({ attaches: 0, reads: {} })))
  expect(calls.listings).toBe(0)
  expect(calls.swedebeat).toMatchObject({ lists: 0, attaches: 0, links: 0 })
  expect(calls.spotify).toBe(0)
  expect(calls.links).toBe(linksBefore)
}

/** The program is the script's draft, private, with no playlist (`PlaylistID: -1`). */
function expectSoundlessProgram(program: FakeProgram, script: ModelScript): void {
  const shape = savedShape(script)
  expect(program).toMatchObject({
    name: shape.name,
    description: shape.description,
    segments: shape.segments,
    public: false,
    playlist_id: -1,
  })
  expect(zonesOf(program)).toEqual(shape.zones)
}

test('"No music" is offered with nothing stored, and drops the playlist choice', async ({ page }) => {
  await signedInWithMowl(page, 'sl-form')
  await page.getByRole('link', { name: 'New class' }).click()
  const form = page.getByRole('form', { name: 'New class' })
  const noMusic = form.getByRole('button', { name: 'No music', exact: true })
  await expect(noMusic).toBeVisible()
  await expect(form.getByRole('radio', { name: PLAYLISTS.draft.name })).toBeVisible()

  await noMusic.click()
  await expect(noMusic).toHaveAttribute('aria-pressed', 'true')
  await expect(form.getByRole('radio', { name: PLAYLISTS.draft.name })).toHaveCount(0)
  await expect(form.getByText(/without a playlist or songs/)).toBeVisible()

  // Back to a playlist: the choice returns.
  await form.getByRole('button', { name: 'Use my playlist' }).click()
  await expect(form.getByRole('radio', { name: PLAYLISTS.draft.name })).toBeVisible()

  // #824: no admin setting takes part; the key cannot even be stored (migration 0043).
  expect(sql("SELECT count(*) FROM app_settings WHERE key = 'soundless_enabled'")).toBe('0')
})

test('design with no music, change it, save it and update the same program', async ({ page }) => {
  await signedInWithMowl(page, 'sl-save')
  useModelScript('soundless_first_draft')
  const linksBefore = Number(sql('SELECT count(*) FROM mowl_playlist_links'))
  const id = await startSoundlessDesign(page, 'E2E soundless save', armMusicCounters)
  await expect(page.getByRole('heading', { name: 'Your class is ready' })).toBeVisible(LONG)
  expect((await getJob(page, id)).state).toBe('completed')
  // The design made no music call at all (the page's own playlist list was read before the count).
  expectNoMusicCalls(linksBefore)

  await page.goto('/classes')
  await page.getByRole('link', { name: 'E2E soundless save' }).click()
  await expect(page).toHaveURL(/\/classes\/[0-9a-f-]{36}$/)
  await expect(page.getByRole('heading', { name: 'E2E soundless save' })).toBeVisible()
  const cls = classId(page)

  // The class page: the "No music" chip, no song or mix actions, nothing saved yet.
  await expect(page.getByTestId('soundless')).toHaveText('No music')
  await expect(page.getByRole('button', { name: 'Change songs' })).toHaveCount(0)
  await expect(page.getByText(/Swedebeat/)).toHaveCount(0)
  await expect(savedState(page)).toHaveText('Not saved to MOWL yet.')

  // Version 2 by chat: the model sees no music, and the revise makes no music call either.
  await reviseByChat(page, 'soundless_revise_good', 'Make the first minutes easier.', 2)
  await expect(page.getByTestId('soundless')).toHaveText('No music')
  expectNoMusicCalls(linksBefore)

  // Save: a private program, the revision's intervals, and PlaylistID -1 (no playlist).
  await confirmWrite(page, 'Save to MOWL', 'Save to MOWL')
  await expect(page.getByText('Saved to MOWL without music, as a private workout.')).toBeVisible(LONG)
  await expect(savedState(page)).toHaveText('Saved to MOWL as version 2. Up to date.')
  const first = savePrograms()
  expect(first).toHaveLength(1)
  expectSoundlessProgram(first[0]!, 'soundless_revise_good')

  // Version 3 by chat, then Update replaces the same program in place, still with no playlist. The
  // fake reads -1 back as written, so classbeat can verify it: the claim is only that fake's.
  await reviseByChat(page, 'soundless_revise_second', 'Ease the second step.', 3)
  await expect(savedState(page)).toHaveText(
    'Saved to MOWL as version 2. The current version (3) has changes that are not saved yet.',
  )
  await confirmWrite(page, 'Update workout', 'Update workout')
  await expect(page.getByText('Updated. MOWL now matches this version.')).toBeVisible(LONG)
  await expect(savedState(page)).toHaveText('Saved to MOWL as version 3. Up to date.')
  const after = savePrograms()
  expect(after).toHaveLength(1)
  expect(after[0]!.program_id).toBe(first[0]!.program_id)
  expectSoundlessProgram(after[0]!, 'soundless_revise_second')
  expect(zonesOf(after[0]!)).not.toEqual(zonesOf(first[0]!))

  // Design, revise, save and update together: no playlist call, and the saved row is verified.
  expectNoMusicCalls(linksBefore)
  const body = (await (await page.request.get(`/api/classes/${cls}`)).json()) as {
    soundless: boolean
    saved: { latest_verified: boolean | null; latest_up_to_date: boolean | null }
  }
  expect(body.soundless).toBe(true)
  expect(body.saved).toMatchObject({ latest_verified: true, latest_up_to_date: true })
})

test('an update MOWL took but classbeat could not confirm does not claim a match', async ({ page }) => {
  await signedInWithMowl(page, 'sl-keep')
  useModelScript('soundless_first_draft')
  await startSoundlessDesign(page, 'E2E soundless keep')
  await expect(page.getByRole('heading', { name: 'Your class is ready' })).toBeVisible(LONG)
  await page.goto('/classes')
  await page.getByRole('link', { name: 'E2E soundless keep' }).click()
  const cls = classId(page)
  await confirmWrite(page, 'Save to MOWL', 'Save to MOWL')
  await expect(savedState(page)).toHaveText('Saved to MOWL as version 1. Up to date.', LONG)
  const saved = savePrograms()
  expect(saved).toHaveLength(1)

  // The user attaches a playlist to the program in the MOWL app, and (the unverified case, played
  // on the fake) MOWL keeps it when an update sends -1. Whether real MOWL does is not known.
  fakeControl('POST', 'save-no-music?read=-1&keep=1')
  fakeControl('POST', `save-playlist?program=${String(saved[0]!.program_id)}&playlist=${String(DRAFT_PLAYLIST_MOWL_ID)}`)
  await reviseByChat(page, 'soundless_revise_good', 'Make the first minutes easier.', 2)
  await confirmWrite(page, 'Update workout', 'Update workout')
  // The playlist attached in the app is a change in MOWL: nothing is written until the user says so.
  await expect(page.getByText('This workout was changed in MOWL after you saved it. Nothing was written.')).toBeVisible(LONG)
  expect(savePrograms()[0]!.playlist_id).toBe(DRAFT_PLAYLIST_MOWL_ID)
  await page.getByRole('button', { name: 'Update anyway' }).click()
  await savePanel(page).getByRole('group', { name: 'Confirm' }).getByRole('button', { name: 'Yes, replace it' }).click()
  await expect(page.getByText(/^Updated, but we could not confirm MOWL matches/)).toBeVisible(LONG)
  await expect(page.getByText('Updated. MOWL now matches this version.')).toHaveCount(0)
  // MOWL (the fake, in this played case) kept the user's playlist, so the write was not what we sent.
  expect(savePrograms()[0]!.playlist_id).toBe(DRAFT_PLAYLIST_MOWL_ID)
  const body = (await (await page.request.get(`/api/classes/${cls}`)).json()) as {
    saved: { latest_verified: boolean | null }
  }
  expect(body.saved.latest_verified).toBe(false)
})

test('the "No music" form and a soundless class page pass the accessibility audit', async ({ page }) => {
  await signedInWithMowl(page, 'sl-axe')
  await page.getByRole('link', { name: 'New class' }).click()
  const form = page.getByRole('form', { name: 'New class' })
  await form.getByRole('button', { name: 'No music', exact: true }).click()
  await expect(form.getByText(/without a playlist or songs/)).toBeVisible()
  const formFindings = await audit(page, 'New class, No music', form.getByRole('button', { name: 'Design my class' }))
  expect(formFindings.problems).toEqual([])

  // Back to the default size and theme, then design and open the class page.
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.evaluate(() => {
    document.documentElement.setAttribute('data-theme', 'dark')
  })
  useModelScript('soundless_first_draft')
  await page.goto('/account') // a page with the navigation, as signedInWithMowl leaves it
  await startSoundlessDesign(page, 'E2E soundless axe')
  await expect(page.getByRole('heading', { name: 'Your class is ready' })).toBeVisible(LONG)
  await page.goto('/classes')
  await page.getByRole('link', { name: 'E2E soundless axe' }).click()
  await expect(page.getByTestId('soundless')).toHaveText('No music')
  const pageFindings = await audit(page, 'Class, No music', page.getByTestId('class-header').getByRole('button', { name: 'Save to MOWL' }))
  expect(pageFindings.problems).toEqual([])
})

test('a class designed to a playlist still designs, saves and links as before', async ({ page }) => {
  await signedInWithMowl(page, 'sl-playlist')
  fakeMowl('arm', PLAYLISTS.draft.id)
  await designAndOpen(page, 'E2E playlist beside soundless', PLAYLISTS.draft.name)

  // Not a soundless class: no chip, and the design used the playlist (an attach and track reads).
  await expect(page.getByTestId('soundless')).toHaveCount(0)
  const state = fakeMowl('state', PLAYLISTS.draft.id)
  expect(state.attaches).toBeGreaterThanOrEqual(1)
  expect(state.reads.tracks ?? 0).toBeGreaterThanOrEqual(1)

  await confirmWrite(page, 'Save to MOWL', 'Save to MOWL')
  await expect(page.getByText('Saved to MOWL as a private workout.')).toBeVisible(LONG)
  const programs = savePrograms()
  expect(programs).toHaveLength(1)
  expect(programs[0]).toMatchObject({ playlist_id: DRAFT_PLAYLIST_MOWL_ID, public: false })
})
