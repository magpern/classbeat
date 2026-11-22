import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import { getJob } from './design-helpers'
import { useModelScript } from './model-script'
import { fakePlaylists, sql } from './song-helpers'
import { control as spotifyControl, fakeState, resetSpotify } from './spotify-helpers'
import {
  catalogueWithout,
  ownerId,
  readyForStudio,
  seedTrackMaps,
  startStudioPick,
  swapCatalogue,
  syncState,
  untilJob,
} from './studio-helpers'
import { gatewayRequests, resetSwedebeat } from './swedebeat-helpers'

// The SwedeBeat studio end to end (ADR 0036, SR C8) on the fakes: a studio class from its
// description to its outputs with **no MOWL connection**: the pick through the fake gateway's match,
// the song list with its working playlist synced (ADR 0036 section 9), Approve songs, the design on
// the list's analyses, the music timeline, the output choice and its consent, and the final Spotify
// playlist and the SwedeBeat playlist produced, each with its state. A working-playlist sync that
// fails is shown and "Sync again" writes it. No real Spotify, SwedeBeat, MOWL or model: the fakes
// answer and the worker replays recorded scripts (e2e_pick, e2e_studio_design).

test.describe.configure({ mode: 'serial' })
test.setTimeout(300_000)
const LONG = { timeout: 90_000 }

test.beforeAll(() => {
  sql(
    `INSERT INTO app_settings (key, value) VALUES ('song_picking', '"on"') ` +
      `ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`,
  )
})

test.beforeEach(() => {
  resetSpotify()
  resetSwedebeat()
})

test.afterAll(() => {
  resetSpotify()
  resetSwedebeat()
  sql(`DELETE FROM app_settings WHERE key = 'song_picking'`)
})

async function newStudioUser(page: Parameters<typeof readyForStudio>[0], tag: string): Promise<string> {
  const email = uniqueEmail(tag)
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await readyForStudio(page)
  const owner = ownerId(email)
  // The studio list is whole songs within the plan's length (ADR 0036 B6a): the free plan's 30 minutes
  // cannot hold the recorded eight four-minute songs, so the user is on the instructor plan.
  sql(`INSERT INTO entitlements (owner_id, plan_id, source) VALUES ('${owner}', 'instructor', 'grant')`)
  return owner
}

test('a studio class from the description to its outputs, with no MOWL connection', async ({ page }) => {
  const owner = await newStudioUser(page, 'studio')
  expect(sql(`SELECT count(*) FROM connections WHERE owner_id = '${owner}' AND provider = 'mowl'`)).toBe('0')
  catalogueWithout()

  // Describe: the pick runs on the fakes and waits for the approval of its list.
  const pickId = await startStudioPick(page, owner, 'Studio e2e climb')
  await untilJob(page, pickId, 'waiting_for_approval')
  await page.goto(`/jobs/${pickId}`)
  const list = page.getByRole('region', { name: 'Songs from SwedeBeat' })
  await expect(list).toBeVisible(LONG)
  await expect(list.getByRole('listitem')).toHaveCount(8)
  // The working playlist follows the list, read back.
  await expect(syncState(page, 'working_copy')).toHaveText('up to date')
  const pick = await getJob(page, pickId)
  const listId = (pick as unknown as { approval_revision_id: string }).approval_revision_id
  expect(sql(`SELECT state FROM spotify_syncs WHERE owner_id = '${owner}' AND revision_id = '${listId}'`)).toBe('synced')

  // Approve songs: the design runs on the list's analyses (seeded, see studio-helpers).
  seedTrackMaps(owner, listId)
  useModelScript('e2e_studio_design')
  await list.getByRole('button', { name: 'Approve songs' }).click()
  const classId = pick.class_id
  const designJob = () =>
    sql(`SELECT id FROM jobs WHERE owner_id = '${owner}' AND class_id = '${classId}' AND kind = 'studio_design'`)
  await expect.poll(designJob, LONG).toMatch(/^[0-9a-f-]{36}$/)
  await expect(page).toHaveURL(new RegExp(`/jobs/${designJob()}$`), LONG) // the page follows the design
  await untilJob(page, designJob(), 'completed')

  // The timeline and the output choice on the class page.
  await page.goto(`/classes/${classId}`)
  await expect(page.getByRole('heading', { name: 'Music timeline' })).toBeVisible(LONG)
  const drafts = () => sql(`SELECT count(*) FROM class_revisions WHERE owner_id = '${owner}' AND kind = 'draft'`)
  expect(drafts()).toBe('1')

  // The edit loop: a chat revise cuts the last song; the class is recomposed from the cache.
  useModelScript('e2e_studio_revise')
  await page.getByLabel('What should change?').fill('Cut the last song shorter')
  await page.getByRole('button', { name: 'Change it', exact: true }).click()
  await expect(page.getByText(/Changed, version \d+/)).toBeVisible(LONG)
  expect(drafts()).toBe('2')
  await expect(page.getByRole('heading', { name: 'Music timeline' })).toBeVisible(LONG)
  const outputs = page.getByTestId('studio-outputs')
  await expect(outputs).toBeVisible()
  const spotify = outputs.getByRole('checkbox', { name: /^Spotify playlist/ })
  const playlist = outputs.getByRole('checkbox', { name: /^SwedeBeat playlist/ })
  const mix = outputs.getByRole('checkbox', { name: /^DJ mix/ })
  await expect(spotify).toBeChecked()
  await expect(playlist).toBeChecked()
  await expect(mix).not.toBeChecked() // optional without MOWL (ADR 0036 section 6)
  await mix.check()
  // The DJ mix is a separate output with its own choices (ADR 0036 Amendment B): they are applied to
  // a new draft first, and only that draft is approved.
  await outputs.getByRole('button', { name: 'Apply these choices' }).click()
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/, LONG)
  const applyId = /\/jobs\/([0-9a-f-]{36})$/.exec(page.url())![1]!
  await untilJob(page, applyId, 'completed')
  await page.goto(`/classes/${classId}`)
  await expect(page.getByRole('heading', { name: 'Music timeline' })).toBeVisible(LONG)
  expect(drafts()).toBe('3')
  await expect(mix).toBeChecked(LONG) // the applied draft records the choice
  await expect(page.getByTestId('studio-consent')).toContainText('SwedeBeat playlist, DJ mix')
  await expect(outputs.getByRole('button', { name: 'Approve and create' })).toBeEnabled(LONG)
  await outputs.getByRole('button', { name: 'Approve and create' }).click()
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/, LONG)
  const outputsId = /\/jobs\/([0-9a-f-]{36})$/.exec(page.url())![1]!
  await untilJob(page, outputsId, 'completed')

  // Both outputs are created; the final Spotify playlist is the draft's sync, read back.
  await page.goto(`/classes/${classId}`)
  await expect(syncState(page, 'final')).toHaveText('up to date', LONG)
  expect(
    sql(`SELECT string_agg(output || ':' || state, ',' ORDER BY output) FROM class_outputs WHERE owner_id = '${owner}'`),
  ).toBe('spotify_playlist:done,swedebeat_mix:done,swedebeat_playlist:done')
  const finals = fakePlaylists().filter((p) => p.name === 'Studio e2e climb')
  expect(finals).toHaveLength(1)
  expect(finals[0]!.public).toBe(true) // roadmap Amendment 30
  const writes = gatewayRequests().filter((r) => r.method === 'POST' && r.path.startsWith('/playlists'))
  expect(writes.map((r) => r.path.replace(/\/playlists\/[^/]+\/publish$/, '/playlists/{id}/publish')).sort()).toEqual([
    '/playlists',
    '/playlists/merge-publish',
    '/playlists/{id}/publish',
  ])
  // The mix was rendered from the eight downloads and merge-published once.
  expect(gatewayRequests().filter((r) => r.method === 'GET' && r.path.startsWith('/tracks/'))).toHaveLength(8)
  // No MOWL connection at any point.
  expect(sql(`SELECT count(*) FROM connections WHERE owner_id = '${owner}' AND provider = 'mowl'`)).toBe('0')
})

test('a working playlist that did not sync is shown, and Sync again writes it', async ({ page }) => {
  const owner = await newStudioUser(page, 'studio-sync')
  catalogueWithout()
  // The replace's owner check of the new working playlist is refused: nothing is written. It is the
  // first read of that path (the create is public from the start and read back for nothing, roadmap
  // Amendment 30), so one refusal leaves the owner check refused and Sync again then writes it.
  spotifyControl('POST', 'inject?kind=status&status=403&method=GET&times=1&path=' + encodeURIComponent('/v1/playlists/[0-9A-Za-z]{22}'))
  const pickId = await startStudioPick(page, owner, 'Studio e2e sync')
  await untilJob(page, pickId, 'waiting_for_approval') // the pick still waits for its approval
  await page.goto(`/jobs/${pickId}`)
  const sync = page.getByTestId('studio-sync-working_copy')
  await expect(syncState(page, 'working_copy')).toHaveText('not updated', LONG)
  await expect(sync).toContainText('This never stops the SwedeBeat outputs.')
  // Nothing was written to the working playlist: no item write reached the fake Spotify.
  expect(fakeState().requests.filter((r) => r.method !== 'GET' && /^\/v1\/playlists\/[^/]+\/items$/.test(r.path))).toEqual([])
  await expect(page.getByRole('button', { name: 'Approve songs' })).toBeVisible()
  await sync.getByRole('button', { name: 'Sync again' }).click()
  await expect(syncState(page, 'working_copy')).toHaveText('up to date', LONG)
  await expect(sync.getByRole('button', { name: 'Sync again' })).toHaveCount(0)
  expect(sql(`SELECT count(*) FROM jobs WHERE owner_id = '${owner}' AND kind = 'spotify_sync' AND state = 'completed'`)).toBe('1')
})

test('a swap is shown and undone, and the working playlist follows each list', async ({ page }) => {
  const owner = await newStudioUser(page, 'studio-swap')
  swapCatalogue()
  const pickId = await startStudioPick(page, owner, 'Studio e2e swap', 'e2e_studio_swap')
  await untilJob(page, pickId, 'waiting_for_approval')
  await page.goto(`/jobs/${pickId}`)
  const list = page.getByRole('region', { name: 'Songs from SwedeBeat' })
  await expect(list.getByRole('listitem')).toHaveCount(8, LONG)
  await expect(list.getByText(' → ').first()).toBeVisible()
  await expect(syncState(page, 'working_copy')).toHaveText('up to date')
  const undo = list.getByRole('button', { name: /^Undo the swap of / }).first()
  await undo.click()
  await expect.poll(() => page.url(), LONG).not.toContain(pickId)
  const undoId = /\/jobs\/([0-9a-f-]{36})$/.exec(page.url())![1]!
  await untilJob(page, undoId, 'waiting_for_approval')
  // The undone list has a gap (never approved into a mix), and its own working-playlist state.
  await expect(list.getByText('Gap', { exact: true })).toBeVisible(LONG)
  await expect(list.getByRole('button', { name: 'Approve songs' })).toHaveCount(0)
  await expect(syncState(page, 'working_copy')).toHaveText('up to date')
  const undone = (await getJob(page, undoId)) as unknown as { approval_revision_id: string }
  expect(
    sql(`SELECT state FROM spotify_syncs WHERE owner_id = '${owner}' AND revision_id = '${undone.approval_revision_id}'`),
  ).toBe('synced')
  // The pool playlist holds the undone list, in order: the original back in its slot.
  const wanted = sql(
    `SELECT string_agg('spotify:track:' || (e ->> 'spotify_id'), ',' ORDER BY n) FROM class_revisions r, ` +
      `jsonb_array_elements(r.content -> 'songs') WITH ORDINALITY AS x(e, n) WHERE r.id = '${undone.approval_revision_id}'`,
  )
  const pool = sql(`SELECT playlist_id FROM spotify_pool WHERE owner_id = '${owner}'`)
  expect(fakePlaylists().find((p) => p.id === pool)!.entries.join(',')).toBe(wanted)
})
