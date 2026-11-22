import { expect, test } from './helpers'
import { fakePlaylists, sql } from './song-helpers'
import { resetSpotify } from './spotify-helpers'
import { resetSpotdown, spotdownCount } from './spotdown-helpers'
import { syncState, untilJob } from './studio-helpers'
import { LONG, designOnSpotdown, mapAnalysers, mapCount, newStudioUser, outputStates } from './studio-flow-helpers'
import { gatewayRequests, resetSwedebeat } from './swedebeat-helpers'

// The studio and the DJ mix on spotdown's maps (ADR 0040 amendment "spotdown is the only analysis
// path", #1363; the SwedeBeat studio itself is studio.spec.ts, which seeds the analyses): nothing
// is seeded here, so the design job downloads the eight tracks from the fake gateway and analyses
// them through the fake spotdown (lookup, upload, start, poll). The stored maps are spotdown's and
// no other analyser's. Whole songs are the default output; the DJ mix is the user's opt-in on the
// consent screen. The fakes record what was sent. No real spotdown, SwedeBeat, MOWL, YouTube or
// Spotify, and no model: the worker replays a recorded script.

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

const mergePublishes = () => gatewayRequests().filter((r) => r.method === 'POST' && r.path === '/playlists/merge-publish')

test('whole songs by default: the design runs on spotdown maps and no mix is made', async ({ page }) => {
  const owner = await newStudioUser(page, 'studio-sd-songs')
  const classId = await designOnSpotdown(page, owner, 'Studio spotdown songs')

  // Every track was analysed by spotdown (the fake saw the lookup and the analysis) and only its
  // maps are stored: no local analysis.
  expect(spotdownCount('GET', '/v2/analyses/lookup')).toBeGreaterThan(0)
  expect(spotdownCount('POST', '/v2/analyses')).toBeGreaterThan(0)
  expect(mapCount(owner)).toBe(8)
  expect(mapAnalysers(owner)).toBe('spotdown')

  await page.goto(`/classes/${classId}`)
  await expect(page.getByRole('heading', { name: 'Music timeline' })).toBeVisible(LONG)
  const outputs = page.getByTestId('studio-outputs')
  await expect(outputs).toBeVisible()
  await expect(outputs.getByRole('checkbox', { name: /^Spotify playlist/ })).toBeChecked()
  await expect(outputs.getByRole('checkbox', { name: /^SwedeBeat playlist/ })).toBeChecked()
  const mix = outputs.getByRole('checkbox', { name: /^DJ mix/ })
  await expect(mix).not.toBeChecked() // the mix is the user's opt-in, never the default

  // The SwedeBeat playlist binds the whole-songs form of the draft: Apply these choices makes it
  // (a job with no model), then Approve and create.
  await outputs.getByRole('button', { name: 'Apply these choices' }).click()
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/, LONG)
  await untilJob(page, /\/jobs\/([0-9a-f-]{36})$/.exec(page.url())![1]!, 'completed')
  await page.goto(`/classes/${classId}`)
  await expect(page.getByTestId('studio-outputs').getByRole('button', { name: 'Approve and create' })).toBeEnabled(LONG)
  await page.getByTestId('studio-outputs').getByRole('button', { name: 'Approve and create' }).click()
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/, LONG)
  const outputsId = /\/jobs\/([0-9a-f-]{36})$/.exec(page.url())![1]!
  await untilJob(page, outputsId, 'completed')

  await page.goto(`/classes/${classId}`)
  await expect(syncState(page, 'final')).toHaveText('up to date', LONG)
  expect(outputStates(owner)).toBe('spotify_playlist:done,swedebeat_playlist:done')
  expect(fakePlaylists().filter((p) => p.name === 'Studio spotdown songs')).toHaveLength(1)
  // The fake gateway got the playlist and no mix.
  expect(mergePublishes()).toHaveLength(0)
  expect(gatewayRequests().filter((r) => r.method === 'POST' && r.path === '/playlists')).toHaveLength(1)
  // No local analysis happened for the produce either: the maps are still spotdown's alone.
  expect(mapAnalysers(owner)).toBe('spotdown')
})

test('the DJ mix opted in on spotdown maps is rendered and published once', async ({ page }) => {
  const owner = await newStudioUser(page, 'studio-sd-mix')
  const classId = await designOnSpotdown(page, owner, 'Studio spotdown mix')
  expect(mapAnalysers(owner)).toBe('spotdown')

  await page.goto(`/classes/${classId}`)
  const outputs = page.getByTestId('studio-outputs')
  await expect(outputs).toBeVisible(LONG)
  await outputs.getByRole('checkbox', { name: /^DJ mix/ }).check()
  await expect(page.getByTestId('studio-consent')).toContainText('SwedeBeat playlist, DJ mix')
  await expect(outputs.getByRole('button', { name: 'Approve and create' })).toBeEnabled(LONG)
  await outputs.getByRole('button', { name: 'Approve and create' }).click()
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/, LONG)
  const outputsId = /\/jobs\/([0-9a-f-]{36})$/.exec(page.url())![1]!
  await untilJob(page, outputsId, 'completed', 240_000)

  expect(outputStates(owner)).toBe('spotify_playlist:done,swedebeat_mix:done,swedebeat_playlist:done')
  // The mix was rendered from fresh downloads of the analysed tracks (each checked against the
  // sha256 spotdown analysed) and merge-published once.
  expect(mergePublishes()).toHaveLength(1)
  expect(gatewayRequests().filter((r) => r.method === 'GET' && r.path.startsWith('/tracks/')).length).toBeGreaterThanOrEqual(8)
  expect(mapAnalysers(owner)).toBe('spotdown')
})
