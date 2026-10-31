import { type Page } from '@playwright/test'
import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import { getJob } from './design-helpers'
import { useModelScript } from './model-script'
import {
  fakePlaylists,
  listedTitles,
  readyToPick,
  sql,
  startPick,
  untilState,
  type FakePlaylist,
} from './song-helpers'
import { CLIENT_A, CLIENT_B, USER_B, USER_A, control, fakeState, playSpotify, resetSpotify, signInAtSpotify } from './spotify-helpers'

// Song picking end to end (ADR 0008 sections 5, 6 and 12; roadmap M4 "done when") against the fake
// Spotify, the fake MOWL and the recorded fake model (e2e_pick.json): from the brief to a class with
// one Approve, the approval's refusals, a partial failure and its resume, and Change songs. The fake
// MOWL lists what the fake Spotify's playlist holds now, so a pool or final playlist the worker
// writes is what MOWL analyses. Public test values only: no real Spotify, MOWL or model.

test.beforeEach(() => {
  resetSpotify()
})

test.afterAll(() => {
  resetSpotify()
})

async function newUser(page: Page, tag: string): Promise<void> {
  const email = uniqueEmail(tag)
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
}

/** On the Spotify page of a connected user: Change Client ID, continue, and start the connect. */
async function changeClientId(page: Page, clientId: string): Promise<void> {
  await page.goto('/account/spotify')
  await page.getByRole('button', { name: 'Change Client ID' }).click()
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByLabel('Spotify Client ID').fill(clientId)
  await page.getByRole('button', { name: /^Connect Spotify/ }).click()
}

const ITEMS = '/v1/playlists/[0-9A-Za-z]+/items'
const spotifyWrites = () =>
  fakeState().requests.filter((r) => r.method !== 'GET' && r.host === 'api.spotify.com')
const finalPlaylists = () => fakePlaylists().filter((p) => p.name !== 'classbeat working copy')

/** A user ready to pick, whose pick waits for approval. Returns the job id. */
async function waitingPick(page: Page, tag: string, title: string): Promise<string> {
  await newUser(page, tag)
  await readyToPick(page)
  const id = await startPick(page, title)
  await untilState(page, id, 'waiting_for_approval')
  await expect(page.getByRole('heading', { name: 'Songs for your class' })).toBeVisible()
  return id
}

async function classIdOf(page: Page): Promise<string> {
  const href = await page.getByRole('link', { name: 'Open the class' }).first().getAttribute('href')
  return /\/classes\/([0-9a-f-]{36})/.exec(href ?? '')![1]!
}

/** The titles MOWL lists for a playlist of the fake Spotify, in order. */
function mowlTitles(playlist: FakePlaylist): string[] {
  const view = control<{ tracks: { Title: string }[] }>('GET', `mowl-tracks?playlist=${playlist.id}`)
  return view.tracks.map((t) => t.Title)
}

test('a pick, one Approve, and a private playlist of exactly the approved songs in order', async ({ page }) => {
  const id = await waitingPick(page, 'pick-a1', 'E2E picked ride')

  // Phase 1 wrote only the working playlist (private, the user's own, 14 candidates); no final one.
  const [pool, ...none] = fakePlaylists()
  expect(none).toEqual([])
  expect(pool).toMatchObject({ name: 'classbeat working copy', owner: USER_A, public: false, collaborative: false })
  expect(pool!.entries).toHaveLength(14)
  expect(finalPlaylists()).toEqual([])

  // The list: 8 songs with role, BPM and length, the total against the asked time, the privacy
  // sentence and the expiry date; no id of any kind on the page.
  const titles = await listedTitles(page)
  expect(titles).toHaveLength(8)
  await expect(page.getByText(/8 songs, \d+:\d+ in total\. You asked for 30:00\./)).toBeVisible()
  await expect(page.getByText(/creates a private playlist with these songs in your own Spotify account/)).toBeVisible()
  await expect(page.getByTestId('expiry')).toContainText('Approve by')
  expect(await page.getByRole('main').innerText()).not.toMatch(/FakeTrack|FakeList|spotify:/)
  const writesBefore = spotifyWrites().length

  // One Approve. The next run is the design, so the model script changes first.
  useModelScript('good_first_draft')
  await page.getByRole('button', { name: 'Approve songs' }).click()
  await untilState(page, id, 'completed')

  // A1: the class is designed on the approved songs, in the app.
  await expect(page.getByRole('heading', { name: 'Your class is ready' })).toBeVisible()
  // A3: the playlist is private, non-collaborative, the user's own, with exactly the approved songs in
  // the approved order, and MOWL (which follows the playlist) lists the same titles.
  const [final] = finalPlaylists()
  expect(finalPlaylists()).toHaveLength(1)
  expect(final).toMatchObject({ name: 'E2E picked ride', owner: USER_A, public: false, collaborative: false })
  expect(mowlTitles(final!)).toEqual(titles)
  expect(final!.entries).toHaveLength(8)
  expect(new Set(final!.entries).size).toBe(8)
  // Nothing was written for the final playlist before the Approve (create, fill: after it).
  expect(spotifyWrites().length).toBeGreaterThan(writesBefore)

  // The class page: the playlist's name, the draft, and Change songs.
  await page.getByRole('link', { name: 'Open the class' }).click()
  await expect(page.getByTestId('playlist')).toContainText('E2E picked ride')
  await expect(page.getByRole('region', { name: 'Change songs' })).toBeVisible()

  // A replay of the approval after completion is a no-op: nothing more is written to Spotify.
  const after = spotifyWrites().length
  await page.goto(`/jobs/${id}`)
  await expect(page.getByRole('heading', { name: 'Your class is ready' })).toBeVisible()
  expect(spotifyWrites()).toHaveLength(after)
})

test('a newer pick stops the older list: a stale tab cannot approve it and says why', async ({ page, context }) => {
  const first = await waitingPick(page, 'pick-sup', 'E2E superseded ride')
  const stale = await context.newPage()
  await stale.goto(`/jobs/${first}`)
  await expect(stale.getByRole('button', { name: 'Approve songs' })).toBeVisible()

  // In the first tab: mark a song and ask for changes. That stops the first pick and starts a newer one.
  useModelScript('e2e_change')
  await page.getByRole('button', { name: 'Replace Tempo Cascade 24' }).click()
  await page.getByRole('button', { name: 'Ask for changes' }).click()
  await expect(page).not.toHaveURL(new RegExp(`/jobs/${first}$`))
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/)
  const second = /\/jobs\/([0-9a-f-]{36})$/.exec(page.url())![1]!
  await untilState(page, second, 'waiting_for_approval')

  const before = spotifyWrites().length
  await stale.getByRole('button', { name: 'Approve songs' }).click()
  await expect(stale.getByRole('alert')).toContainText('These songs can no longer be approved. They were approved already, or stopped.')
  await expect(stale.getByRole('button', { name: 'Approve songs' })).toHaveCount(0)
  expect(spotifyWrites()).toHaveLength(before)
  expect(finalPlaylists()).toEqual([])

  // A reload of the old page says the same in words; the newer list is the one to approve.
  await stale.reload()
  await expect(stale.getByText('You stopped this')).toBeVisible()
  await expect(stale.getByText('Nothing was written to your Spotify.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Approve songs' })).toBeVisible()
  await stale.close()
})

test('a list that was not approved in time expires and writes nothing to Spotify', async ({ page }) => {
  test.setTimeout(150_000) // the sweep that marks the job expired runs every 30 seconds
  const id = await waitingPick(page, 'pick-exp', 'E2E expiring ride')
  const writes = spotifyWrites().length
  sql(`UPDATE jobs SET expires_at = now() - interval '1 minute' WHERE id = '${id}'`)

  await page.getByRole('button', { name: 'Approve songs' }).click()
  await expect(page.getByText(/These songs expired before they were approved/).first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Approve songs' })).toHaveCount(0)
  expect(spotifyWrites()).toHaveLength(writes) // an expired job makes no call
  expect(finalPlaylists()).toEqual([])

  // The sweep then marks the job expired and the page offers a new pick.
  await untilState(page, id, 'expired')
  await page.reload()
  await expect(page.getByRole('heading', { name: 'These songs expired' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Pick songs again' })).toBeVisible()
  expect(spotifyWrites()).toHaveLength(writes)
})

test('a Spotify account switch while songs wait makes the approval refuse and write nothing', async ({ page, context }) => {
  const id = await waitingPick(page, 'pick-acct', 'E2E switched ride')
  const writes = spotifyWrites().length
  // In another tab the same classbeat user connects a different Spotify account (a confirmed switch).
  const other = await context.newPage()
  await playSpotify(other)
  signInAtSpotify(USER_B)
  await changeClientId(other, CLIENT_B)
  await expect(other.getByText(/This is a different Spotify account than before/)).toBeVisible()
  await other.getByLabel('Spotify Client ID').fill(CLIENT_B)
  await other.getByRole('button', { name: 'Confirm and switch account' }).click()
  await expect(other.getByText('Spotify is connected.')).toBeVisible()
  await other.close()

  await page.getByRole('button', { name: 'Approve songs' }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page.getByRole('alert')).toContainText('Your Spotify account is not the one these songs were chosen for')
  expect((await getJob(page, id)).state).toBe('waiting_for_approval') // nothing started
  expect(spotifyWrites().filter((r) => r.token_user === USER_A)).toHaveLength(writes)
  expect(finalPlaylists()).toEqual([])
})


test('a playlist that does not match the approved songs fails the job, and Create again resumes it', async ({ page }) => {
  const id = await waitingPick(page, 'pick-part', 'E2E partial ride')
  const titles = await listedTitles(page)
  // Spotify applies only the first three songs of the next write to the final playlist.
  control('POST', `inject?kind=partial&path=${encodeURIComponent(ITEMS)}&keep=3&times=1`)
  useModelScript('good_first_draft')
  await page.getByRole('button', { name: 'Approve songs' }).click()
  await untilState(page, id, 'failed')
  expect((await getJob(page, id)).error_category).toBe('playlist_mismatch')
  await expect(page.getByText('Spotify’s playlist did not match the approved songs; nothing was designed. Create it again.')).toBeVisible()
  const [partial] = finalPlaylists()
  expect(partial!.entries).toHaveLength(3) // what Spotify kept; it is left alone, never reused

  // Create the playlist again: the approval is the receipt, so nothing is approved a second time.
  await page.getByRole('button', { name: 'Create the playlist again' }).click()
  await expect(page).not.toHaveURL(new RegExp(`/jobs/${id}$`))
  const again = /\/jobs\/([0-9a-f-]{36})$/.exec(page.url())![1]!
  await untilState(page, again, 'completed')
  await expect(page.getByRole('heading', { name: 'Your class is ready' })).toBeVisible()
  const made = finalPlaylists()
  expect(made).toHaveLength(2)
  const resumed = made.find((p) => p.id !== partial!.id)!
  expect(resumed).toMatchObject({ owner: USER_A, public: false, collaborative: false })
  expect(mowlTitles(resumed)).toEqual(titles)
})

/** A class designed on picked songs, left on its class page. Returns the first playlist's id. */
async function pickedClass(page: Page, tag: string, title: string): Promise<FakePlaylist> {
  const id = await waitingPick(page, tag, title)
  useModelScript('good_first_draft')
  await page.getByRole('button', { name: 'Approve songs' }).click()
  await untilState(page, id, 'completed')
  await page.getByRole('link', { name: 'Open the class' }).click()
  await expect(page.getByRole('region', { name: 'Change songs' })).toBeVisible()
  return finalPlaylists()[0]!
}

async function askChange(page: Page): Promise<string> {
  useModelScript('e2e_change')
  const change = page.getByRole('region', { name: 'Change songs' })
  await change.getByRole('button', { name: 'Replace Tempo Cascade 24' }).click()
  await change.getByLabel('What should be different?').fill('A harder song for the climb.')
  await change.getByRole('button', { name: 'Change the marked songs' }).click()
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/)
  const id = /\/jobs\/([0-9a-f-]{36})$/.exec(page.url())![1]!
  await untilState(page, id, 'waiting_for_approval')
  return id
}

test('Change songs: one song replaced, a new playlist after one Approve, the old one untouched', async ({ page }) => {
  const first = await pickedClass(page, 'pick-chg', 'E2E changed ride')
  const firstEntries = [...first.entries]
  const job = await askChange(page)
  const titles = await listedTitles(page)
  expect(titles).toHaveLength(8)
  expect(titles[2]).not.toBe('Tempo Cascade 24') // slot 3 is new
  expect(titles.filter((t) => t !== titles[2])).toHaveLength(7)
  await expect(page.getByTestId('song').nth(2)).toContainText('New')
  expect(finalPlaylists()).toHaveLength(1) // nothing written for the change before the Approve

  useModelScript('revise_good')
  await page.getByRole('button', { name: 'Approve songs' }).click()
  await untilState(page, job, 'completed')
  const [oldNow, changed] = finalPlaylists()
  expect(oldNow!.entries).toEqual(firstEntries) // the earlier playlist is history
  expect(changed).toMatchObject({ owner: USER_A, public: false, collaborative: false })
  expect(mowlTitles(changed!)).toEqual(titles)
  await page.getByRole('link', { name: 'Open the class' }).click()
  await expect(page.getByTestId('playlist')).toContainText('E2E changed ride')
})

test('Change songs: a failed write is resumed with Create the playlist again', async ({ page }) => {
  await pickedClass(page, 'pick-chgr', 'E2E resumed change')
  const job = await askChange(page)
  const titles = await listedTitles(page)
  control('POST', `inject?kind=partial&path=${encodeURIComponent(ITEMS)}&keep=2&times=1`)
  useModelScript('revise_good')
  await page.getByRole('button', { name: 'Approve songs' }).click()
  await untilState(page, job, 'failed')
  expect((await getJob(page, job)).error_category).toBe('playlist_mismatch')

  await page.getByRole('button', { name: 'Create the playlist again' }).click()
  await expect(page).not.toHaveURL(new RegExp(`/jobs/${job}$`))
  const again = /\/jobs\/([0-9a-f-]{36})$/.exec(page.url())![1]!
  await untilState(page, again, 'completed')
  const made = finalPlaylists()
  expect(made).toHaveLength(3) // the first, the partial one Spotify kept, and the resumed one
  expect(mowlTitles(made[2]!)).toEqual(titles)
})

test('two users on different Client IDs pick at once and never mix', async ({ browser }) => {
  const one = await (await browser.newContext()).newPage()
  const two = await (await browser.newContext()).newPage()
  try {
    await newUser(one, 'pick-one')
    await newUser(two, 'pick-two')
    signInAtSpotify(USER_A)
    await readyToPick(one, CLIENT_A)
    signInAtSpotify(USER_B)
    await readyToPick(two, CLIENT_B)

    // Both picks run in the worker at the same time, on the same recorded model script.
    const a = await startPick(one, 'E2E rider one')
    const b = await startPick(two, 'E2E rider two')
    await untilState(one, a, 'waiting_for_approval')
    await untilState(two, b, 'waiting_for_approval')

    // The other's job is not there: 404 on every id of the other user.
    expect((await two.request.get(`/api/jobs/${a}`)).status()).toBe(404)
    expect((await one.request.get(`/api/jobs/${b}`)).status()).toBe(404)

    const pools = fakePlaylists()
    expect(pools.map((p) => p.owner).sort()).toEqual([USER_A, USER_B])

    useModelScript('good_first_draft')
    await one.getByRole('button', { name: 'Approve songs' }).click()
    await two.getByRole('button', { name: 'Approve songs' }).click()
    await untilState(one, a, 'completed')
    await untilState(two, b, 'completed')

    const owned = finalPlaylists()
    expect(owned.map((p) => [p.name, p.owner]).sort()).toEqual([
      ['E2E rider one', USER_A],
      ['E2E rider two', USER_B],
    ])
    // Every request that touched a playlist carried the token of that playlist's owner, and every
    // token belonged to the app of its own user: nothing crossed.
    const requests = fakeState().requests
    for (const p of fakePlaylists()) {
      const touching = requests.filter((r) => r.path.includes(p.id))
      expect(touching.length).toBeGreaterThan(0)
      expect(new Set(touching.map((r) => r.token_user))).toEqual(new Set([p.owner]))
    }
    for (const r of requests.filter((r) => r.token_user !== null)) {
      expect(r.token_app).toBe(r.token_user === USER_A ? CLIENT_A : CLIENT_B)
    }
    // Each user's playlist is private and holds eight songs MOWL lists for it.
    for (const p of owned) {
      expect(p).toMatchObject({ public: false, collaborative: false })
      expect(mowlTitles(p)).toHaveLength(8)
    }
  } finally {
    await one.context().close()
    await two.context().close()
  }
})

test("a working playlist that is not the user's own is never written to", async ({ page }) => {
  test.setTimeout(240_000)
  await newUser(page, 'pick-own')
  await readyToPick(page)
  // The user's pool row is changed (as a bug, or someone with the database, could) to point at a
  // playlist of another Spotify user.
  const foreign = control<{ id: string }>('POST', `playlist?owner=${USER_B}&name=Not%20yours&tracks=3`)
  sql(`UPDATE spotify_pool SET playlist_id = '${foreign.id}' WHERE updated_at = (SELECT max(updated_at) FROM spotify_pool)`) // this user's row: the newest
  const before = fakePlaylists().find((p) => p.id === foreign.id)!.entries

  // Spotify hides another user's playlist (404): classbeat treats its pool as gone, writes nothing to
  // it and makes a new working playlist of its own.
  const id = await startPick(page, 'E2E foreign pool')
  await untilState(page, id, 'waiting_for_approval')
  const mine = fakePlaylists().filter((p) => p.owner === USER_A)
  expect(mine).toHaveLength(1)
  expect(mine[0]!.id).not.toBe(foreign.id)
  expect(fakeState().requests.filter((r) => r.method !== 'GET' && r.path.includes(foreign.id))).toEqual([])
  expect(fakePlaylists().find((p) => p.id === foreign.id)!.entries).toEqual(before)
  expect(sql('SELECT playlist_id FROM spotify_pool ORDER BY updated_at DESC LIMIT 1')).toBe(mine[0]!.id)
})

test('a lost answer to the playlist create is reconciled: one playlist, and the class is designed', async ({ page }) => {
  const id = await waitingPick(page, 'pick-lost', 'E2E lost answer')
  const titles = await listedTitles(page)
  // The create reaches Spotify and is applied, but the answer is lost. classbeat finds its own
  // playlist in the user's listing by the reference it put in it, and makes no second one.
  control('POST', `inject?kind=drop&method=POST&path=${encodeURIComponent('/v1/me/playlists')}&times=1`)
  useModelScript('good_first_draft')
  await page.getByRole('button', { name: 'Approve songs' }).click()
  await untilState(page, id, 'completed')
  const made = finalPlaylists()
  expect(made).toHaveLength(1)
  expect(mowlTitles(made[0]!)).toEqual(titles)
})

test('a playlist write that may not have happened is left to the user, who checks and then creates again', async ({ page }) => {
  test.setTimeout(120_000)
  const id = await waitingPick(page, 'pick-unc', 'E2E unsure ride')
  const titles = await listedTitles(page)
  // The create is cut before it reaches Spotify: nothing was written, and classbeat cannot prove it.
  control('POST', `inject?kind=refuse&method=POST&path=${encodeURIComponent('/v1/me/playlists')}&times=1`)
  useModelScript('good_first_draft')
  await page.getByRole('button', { name: 'Approve songs' }).click()
  await untilState(page, id, 'failed')
  expect((await getJob(page, id)).error_category).toBe('uncertain_write')
  await expect(page.getByText('classbeat could not tell whether your playlist was written in Spotify')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Create the playlist again' })).toHaveCount(0) // not before it is decided
  expect(finalPlaylists()).toEqual([])

  await page.getByRole('button', { name: 'Check again' }).click()
  // Spotify's listing shows no playlist of ours and no lag is configured, so classbeat will not guess.
  await expect(page.getByText('Spotify still does not say. Wait a few minutes and check again.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Create the playlist again' })).toHaveCount(0)

  // Do it anyway asks for a second confirmation that names the risk (two playlists of one name).
  await page.getByRole('button', { name: 'Do it anyway' }).click()
  await expect(page.getByText(/you may end up with two playlists of the same name/)).toBeVisible()
  await page.getByRole('button', { name: 'Yes, it was not written' }).click()
  await expect(page.getByText(/You decided to write it again\. Create the playlist again/)).toBeVisible()
  expect(finalPlaylists()).toEqual([]) // still nothing in Spotify: deciding writes nothing

  await page.getByRole('button', { name: 'Create the playlist again' }).click()
  await expect(page).not.toHaveURL(new RegExp(`/jobs/${id}$`))
  const again = /\/jobs\/([0-9a-f-]{36})$/.exec(page.url())![1]!
  await untilState(page, again, 'completed')
  const made = finalPlaylists()
  expect(made).toHaveLength(1)
  expect(mowlTitles(made[0]!)).toEqual(titles)
})
