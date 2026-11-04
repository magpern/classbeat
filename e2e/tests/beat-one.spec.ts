import type { Page } from '@playwright/test'
import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import { PLAYLISTS, connectMowl, fakeMowl, getJob, jobId } from './design-helpers'
import { useModelScript } from './model-script'
import {
  LONG,
  classId,
  confirmWrite,
  designAndOpen,
  reviseByChat,
  saveArm,
  savePrograms,
  savedState,
  type FakeProgram,
} from './class-helpers'
import { audit } from './mu-helpers'
import { useSwedebeatThroughUi } from './swedebeat-helpers'
import {
  CACHED_MIX_ID,
  CACHED_MIX_NAME,
  ownerIdForEmail,
  resetSwedebeatMixes,
  seedCachedMix,
  sql,
} from './swedebeat-mix-helpers'

// Beat-one corrections end to end on the fakes (ADR 0007 amendment "beat-one corrections", BC6,
// #535): on a fake Spotify playlist and on a seeded fake Swedebeat mix, a correction set on the
// class page moves the fitted changes once applied (a refit_draft job, no model), survives a chat
// revision, and is what Save and Update write to the fake MOWL. Another user cannot see the class;
// the account export holds the corrections and deleting the account removes them. The Beat 1
// panel is audited with axe. The fakes' state is shared, so the tests run one after the other.

test.describe.configure({ mode: 'serial' })
test.setTimeout(240_000)

test.beforeEach(() => {
  fakeMowl('arm', PLAYLISTS.draft.id)
  saveArm('', true)
  resetSwedebeatMixes()
})

test.afterAll(() => {
  saveArm('', true)
  useModelScript('good_first_draft')
})

interface Detail {
  current_revision_id: string
  current_revision_number: number
  open_job: { id: string; kind: string } | null
  beat_one: { available: boolean; pending: boolean; songs: { song: number; beats: number }[] }
}

async function detail(page: Page, id: string): Promise<Detail> {
  const answer = await page.request.get(`/api/classes/${id}`)
  expect(answer.status()).toBe(200)
  return (await answer.json()) as Detail
}

/** Every interval's length of a version, in order. */
async function seconds(page: Page, id: string, revisionId: string): Promise<number[]> {
  const answer = await page.request.get(`/api/classes/${id}/revisions/${revisionId}`)
  expect(answer.status()).toBe(200)
  const body = (await answer.json()) as { content: { segments: { intervals: { sec: number }[] }[] } }
  return body.content.segments.flatMap((s) => s.intervals.map((i) => i.sec))
}

const sum = (values: number[]): number => values.reduce((a, b) => a + b, 0)
const programSeconds = (program: FakeProgram): number[] => program.intervals.map((i) => i[0]!)

/** Wait until the class's current version is `number` and no job is open, then reload the page. */
async function untilVersion(page: Page, id: string, number: number): Promise<Detail> {
  await expect
    .poll(async () => {
      const found = await detail(page, id)
      return found.current_revision_number === number && found.open_job === null
    }, LONG)
    .toBe(true)
  await page.reload()
  return detail(page, id)
}

/** Click Later (or Earlier) on a song's row `times` times, waiting for each answer. */
async function move(page: Page, song: number, way: 'later' | 'earlier', times: number): Promise<void> {
  const row = page.getByTestId(`beat-one-song-${String(song)}`)
  for (let n = 1; n <= times; n++) {
    await row.getByRole('button', { name: new RegExp(`^Move beat 1 of .* ${way}$`) }).click()
    await expect(row.locator('output')).toHaveText(`${String(n)} beat${n === 1 ? '' : 's'} ${way}`)
  }
}

async function applyFixes(page: Page, id: string, number: number): Promise<Detail> {
  await expect(page.getByTestId('beat-one-pending')).toBeVisible()
  await page.getByRole('button', { name: 'Apply timing fixes' }).click()
  const after = await untilVersion(page, id, number)
  expect(after.beat_one.pending).toBe(false)
  return after
}

/** The page's CSRF headers, for the account calls a test makes itself. */
async function csrf(page: Page): Promise<Record<string, string>> {
  const session = (await (await page.request.get('/api/auth/session')).json()) as { csrf_token: string }
  return { 'X-CSRF-Token': session.csrf_token, Origin: new URL(page.url()).origin }
}

test('a Spotify class: correct, apply, revise, save, change and update', async ({ page, browser, baseURL }) => {
  const email = uniqueEmail('beat-one')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await connectMowl(page)
  await designAndOpen(page, 'E2E beat one', PLAYLISTS.draft.name)
  const id = classId(page)
  const first = await detail(page, id)
  expect(first.beat_one.available).toBe(true)
  const panel = page.getByTestId('beat-one')
  await expect(panel).toBeVisible()

  // axe on the panel (the rest of the class page is audited by the MU specs).
  const findings = await audit(page, 'beat one', panel.getByRole('heading', { name: 'Beat 1' }), '[data-testid="beat-one"]')
  expect(findings.problems).toEqual([])

  // Every song the class plays, half a bar later; nothing changes until the fixes are applied.
  const v1 = await seconds(page, id, first.current_revision_id)
  for (const song of first.beat_one.songs) await move(page, song.song, 'later', 2)
  expect((await detail(page, id)).current_revision_number).toBe(1)
  const second = await applyFixes(page, id, 2)
  const v2 = await seconds(page, id, second.current_revision_id)
  expect(v2).not.toEqual(v1)
  expect(sum(v2)).toBe(sum(v1))
  const applied = sql(`SELECT music->'beat_one' FROM class_revisions WHERE id = '${second.current_revision_id}'`)
  expect(JSON.parse(applied)).toEqual(first.beat_one.songs.map((s) => ({ song: s.song, beats: 2 })))

  // A chat revision keeps them: the new version is fitted with the same corrections.
  await reviseByChat(page, 'revise_good', 'Make the first minutes easier.', 3)
  const third = await untilVersion(page, id, 3)
  expect(third.beat_one.pending).toBe(false)
  expect(sql(`SELECT music->'beat_one' FROM class_revisions WHERE id = '${third.current_revision_id}'`)).toBe(applied)

  // Save writes the corrected timing to the fake MOWL.
  await confirmWrite(page, 'Save to MOWL', 'Save to MOWL')
  await expect(savedState(page)).toHaveText('Saved to MOWL as version 3. It is up to date.', LONG)
  const [saved] = savePrograms()
  expect(programSeconds(saved!)).toEqual(await seconds(page, id, third.current_revision_id))

  // Reset one song, apply: the saved workout is out of date, and Update writes the new timing.
  const one = first.beat_one.songs[0]!.song
  await page.getByTestId(`beat-one-song-${String(one)}`).getByRole('button', { name: /^Reset beat 1 of / }).click()
  await expect(page.getByTestId(`beat-one-song-${String(one)}`).locator('output')).toHaveText('as analysed')
  const fourth = await applyFixes(page, id, 4)
  const v4 = await seconds(page, id, fourth.current_revision_id)
  if (JSON.stringify(v4) !== JSON.stringify(programSeconds(saved!))) {
    await expect(savedState(page)).toContainText('has changes that are not saved yet')
  }
  await confirmWrite(page, /^Update saved workout/, 'Update workout')
  await expect(page.getByText('Updated. Your MOWL workout now matches this version.')).toBeVisible(LONG)
  const after = savePrograms()
  expect(after).toHaveLength(1)
  expect(after[0]!.program_id).toBe(saved!.program_id)
  expect(programSeconds(after[0]!)).toEqual(v4)

  // Another user sees nothing of the class.
  const other = await browser.newContext({ baseURL })
  const otherPage = await other.newPage()
  const otherEmail = uniqueEmail('beat-one-other')
  await signUpThroughUi(otherPage, otherEmail)
  await signInThroughUi(otherPage, otherEmail)
  expect((await otherPage.request.get(`/api/classes/${id}`)).status()).toBe(404)
  await other.close()

  // The export holds the corrections (no fingerprint); deleting the account removes them.
  const owner = ownerIdForEmail(email)
  const exported = await page.request.post('/api/account/export', { headers: await csrf(page) })
  expect(exported.status()).toBe(200)
  const file = (await exported.json()) as { beat_one_corrections: Record<string, unknown>[] }
  expect(file.beat_one_corrections.length).toBe(first.beat_one.songs.length - 1)
  expect(file.beat_one_corrections.every((r) => r.class_id === id && !('fingerprint' in r))).toBe(true)
  const deleted = await page.request.post('/api/account/delete', {
    headers: await csrf(page),
    data: { confirm_email: email },
  })
  expect(deleted.status()).toBe(204)
  expect(sql(`SELECT count(*) FROM beat_one_corrections WHERE owner_id = '${owner}'`)).toBe('0')
})

test('a Swedebeat mix class: correct one song, apply and save', async ({ page }) => {
  useModelScript('e2e_swedebeat_mix')
  const email = uniqueEmail('beat-one-mix')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await connectMowl(page)
  await useSwedebeatThroughUi(page) // #525: the mix choice needs the user's own switch
  const connections = (await (await page.request.get('/api/connections')).json()) as {
    connections: { provider: string; id: string }[]
  }
  const mowl = connections.connections.find((c) => c.provider === 'mowl')
  seedCachedMix(ownerIdForEmail(email), mowl!.id, CACHED_MIX_ID)

  await page.goto('/classes/new')
  const form = page.getByRole('form', { name: 'New class' })
  await form.getByLabel('Class name').fill('E2E beat one mix')
  await form.getByRole('button', { name: 'Use my Swedebeat mix' }).click()
  await form.getByRole('radio', { name: new RegExp(CACHED_MIX_NAME) }).check({ force: true })
  await form.getByLabel('Length in minutes').fill('10')
  await form.getByLabel('Climbs', { exact: true }).fill('1')
  await form.getByLabel('Sprints', { exact: true }).fill('1')
  await form.getByRole('button', { name: 'Design my class' }).click()
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/)
  const job = jobId(page)
  await expect(page.getByRole('heading', { name: 'Your class is ready' })).toBeVisible(LONG)
  expect((await getJob(page, job)).state).toBe('completed')
  await page.goto('/classes')
  await page.getByRole('link', { name: 'E2E beat one mix' }).click()
  await expect(page).toHaveURL(/\/classes\/[0-9a-f-]{36}$/)
  const id = classId(page)

  const first = await detail(page, id)
  expect(first.beat_one.songs.map((s) => s.song)).toEqual([1])
  await expect(page.getByTestId('beat-one').getByText('Song 1')).toBeVisible()
  const v1 = await seconds(page, id, first.current_revision_id)

  // The seeded song plays at 124 BPM from 0.4 s: half a bar later moves the changes snapped to bars.
  await move(page, 1, 'later', 2)
  const second = await applyFixes(page, id, 2)
  const v2 = await seconds(page, id, second.current_revision_id)
  expect(v2).not.toEqual(v1)
  expect(sum(v2)).toBe(sum(v1))
  // The user's stored analysis is never changed by a correction.
  const shift = sql(
    `SELECT map->'songs'->0->>'first_downbeat' FROM swedebeat_mix_maps WHERE mix_id = '${CACHED_MIX_ID}' ` +
      `AND owner_id = '${ownerIdForEmail(email)}'`,
  )
  expect(Number(shift)).toBe(0.4)

  await confirmWrite(page, 'Save to MOWL', 'Save to MOWL')
  await expect(savedState(page)).toHaveText('Saved to MOWL as version 2. It is up to date.', LONG)
  const [saved] = savePrograms()
  expect(programSeconds(saved!)).toEqual(v2)

  // A correction for earlier music (another fingerprint) is listed, cleared, and Clear all empties
  // the class's corrections.
  const owner = ownerIdForEmail(email)
  sql(
    `INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) ` +
      `VALUES ('${owner}', '${id}', '${'a'.repeat(64)}', 1, -2, 124)`,
  )
  await page.reload()
  const stale = page.getByTestId('beat-one-stale')
  await expect(stale).toContainText('Song 1: 2 beats earlier')
  await stale.getByRole('button', { name: 'Clear the earlier timing fix of song 1' }).click()
  await expect(stale).toHaveCount(0)
  expect(sql(`SELECT count(*) FROM beat_one_corrections WHERE class_id = '${id}'`)).toBe('1')
  sql(
    `INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) ` +
      `VALUES ('${owner}', '${id}', '${'b'.repeat(64)}', 1, 3, 124)`,
  )
  await page.reload()
  await page.getByRole('button', { name: 'Clear all timing fixes' }).click()
  await expect(page.getByTestId('beat-one-stale')).toHaveCount(0)
  expect(sql(`SELECT count(*) FROM beat_one_corrections WHERE class_id = '${id}'`)).toBe('0')
})
