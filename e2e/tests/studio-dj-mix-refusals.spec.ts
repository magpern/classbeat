import { expect, test } from './helpers'
import { fakePlaylists, sql } from './song-helpers'
import { LONG, designOnSpotdown, newStudioUser } from './studio-flow-helpers'
import { forgeOutputs, gatewayWrites, isPlain, untilIdle, useStudioFakes } from './studio-mix-helpers'
import { gatewayRequests } from './swedebeat-helpers'

// What the DJ mix screen and the server refuse, with nothing sent to any fake (ADR 0036 amendment
// B, B6 and B9, #1374). The writes are the fake gateway's playlist and mix writes and the fake
// Spotify's playlists: the design itself only reads from them.

test.describe.configure({ mode: 'serial' })
test.setTimeout(420_000)
useStudioFakes()

// The free plan's class length before this file changes it (read once, put back in afterAll too).
let planMinutes: string | undefined
const restorePlan = () => {
  if (planMinutes) sql(`UPDATE plans SET max_class_minutes = ${planMinutes} WHERE id = 'free'`)
}
test.beforeAll(() => {
  planMinutes = sql(`SELECT max_class_minutes FROM plans WHERE id = 'free'`)
})
test.afterAll(restorePlan)

const count = (owner: string, kind: string) => sql(`SELECT count(*) FROM jobs WHERE owner_id = '${owner}' AND kind = '${kind}'`)

/** Nothing was created for the class: no apply or outputs job, no output row, no write, no playlist. */
function expectNothingSent(owner: string, title: string): void {
  expect(count(owner, 'apply_mix_choices')).toBe('0')
  expect(count(owner, 'produce_outputs')).toBe('0')
  expect(sql(`SELECT count(*) FROM class_outputs WHERE owner_id = '${owner}'`)).toBe('0')
  expect(sql(`SELECT count(*) FROM class_revisions WHERE owner_id = '${owner}' AND kind = 'draft'`)).toBe('1')
  expect(gatewayWrites()).toEqual([])
  expect(fakePlaylists().filter((p) => p.name === title)).toEqual([])
}

test('every song kept whole past the plan’s class length is refused on the screen and nothing is sent', async ({ page }) => {
  const owner = await newStudioUser(page, 'studio-mix-long')
  const classId = await designOnSpotdown(page, owner, 'Studio mix too long')
  await untilIdle(owner)
  // The design was made on the instructor plan. Before the choices are applied the plan is the
  // free one cut to its 10 minute floor (630 s with the tolerance; restored at the end), and the
  // workout the whole songs make is about 31 minutes (ADR 0036 B6: a later downgrade is the one way
  // a whole-songs form can still be too long for the plan).
  try {
    sql(`DELETE FROM entitlements WHERE owner_id = '${owner}'`)
    sql(`UPDATE plans SET max_class_minutes = 10 WHERE id = 'free'`)
    await page.goto(`/classes/${classId}`)
    const outputs = page.getByTestId('studio-outputs')
    await outputs.getByRole('checkbox', { name: /^DJ mix/ }).check()
    const rows = page.getByTestId('mix-song')
    await expect(rows).toHaveCount(8, LONG)
    await outputs.getByRole('switch', { name: /^Blend transitions/ }).click() // every change is a hard cut
    await outputs.getByRole('button', { name: 'All songs' }).click()
    await outputs.getByRole('button', { name: 'Keep selected whole' }).click()
    await expect(rows.getByRole('checkbox', { name: /^Keep whole/, checked: true })).toHaveCount(8)
    await expect(outputs.getByTestId('mix-total')).toContainText('the mix is 32:00. The class is 30:00')
    const before = gatewayRequests().length

    // The refusal does not depend on these choices: any form of this draft is over the plan's length.
    await outputs.getByRole('button', { name: 'Apply these choices' }).click()
    await expect(outputs.getByText(/longer than your plan allows/).first()).toBeVisible(LONG)
    await expect(outputs.getByRole('link', { name: 'Change songs' })).toBeVisible() // nothing left to trim: no re-plan
    await expect(page).toHaveURL(new RegExp(`/classes/${classId}$`)) // no job was made, so the page stays
    expect(gatewayRequests()).toHaveLength(before) // the fake gateway was asked nothing
    expectNothingSent(owner, 'Studio mix too long')
  } finally {
    restorePlan()
  }
})

test('a SwedeBeat playlist from a mixed draft without DJ mix is refused by the server', async ({ page }) => {
  const owner = await newStudioUser(page, 'studio-mix-forged')
  const classId = await designOnSpotdown(page, owner, 'Studio forged playlist')
  await untilIdle(owner)
  expect(await isPlain(page, classId)).toBe(false)
  const before = gatewayRequests().length

  // The screen never sends it (a mixed draft needs Apply these choices first), so the request is
  // forged past it: the draft is the design's own, with 8 s crossfades, and DJ mix is not chosen.
  expect(await forgeOutputs(page, classId, ['swedebeat_playlist'])).toEqual({ status: 409, reason: 'mix_not_chosen' })
  expect(await forgeOutputs(page, classId, ['spotify_playlist', 'swedebeat_playlist'])).toEqual({
    status: 409,
    reason: 'mix_not_chosen',
  })
  expect(gatewayRequests()).toHaveLength(before)
  expectNothingSent(owner, 'Studio forged playlist')
})
