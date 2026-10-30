import type { Page } from '@playwright/test'
import { expect, test } from './helpers'
import { PLAYLISTS, connectMowl, fakeMowl } from './design-helpers'
import { savedShape, useModelScript, type ModelScript } from './model-script'
import {
  DRAFT_PLAYLIST_MOWL_ID,
  LAST_WRITE,
  LONG,
  confirmWrite,
  listing,
  designAndOpen,
  editInMowl,
  zonesOf,
  type FakeProgram,
  reviseByChat,
  saveArm,
  savePanel,
  savePrograms,
  saveState,
  savedState,
  signedInWithMowl,
  undecided,
} from './class-helpers'

// M3b T2 (ADR 0007 section 13): a class designed, changed by chat, saved to MOWL and updated,
// and what happens when MOWL does not answer, changes under us or is another account. Fakes
// only: the fake MOWL (e2e/fake-mowl) holds the programs and drops answers on request, and the
// worker's FakeProvider replays the recorded scripts (e2e/tests/model-script.ts). The tests share
// the fake's program store and the worker's script, so they run one after the other.
test.describe.configure({ mode: 'serial' })
test.setTimeout(180_000)

test.beforeEach(() => {
  fakeMowl('arm', PLAYLISTS.draft.id)
  saveArm('', true) // no programs, no injected failures
})
/** The program holds the content of the script's draft, private, on the linked playlist. */
function expectProgram(program: FakeProgram, script: ModelScript): void {
  const shape = savedShape(script)
  expect(program).toMatchObject({
    name: shape.name,
    description: shape.description,
    segments: shape.segments,
    public: false,
    playlist_id: DRAFT_PLAYLIST_MOWL_ID,
  })
  expect(zonesOf(program)).toEqual(shape.zones)
}

test.afterAll(() => {
  saveArm('', true)
  useModelScript('good_first_draft')
})

test('design, change, pick an older version, save, change and update', async ({ page }) => {
  await signedInWithMowl(page, 'save')
  await designAndOpen(page, 'E2E save and update', PLAYLISTS.draft.name)
  await expect(savedState(page)).toHaveText('Not saved to MOWL yet.')

  // Version 2 by chat, then back to version 1 (the page asks the server to make it current).
  await reviseByChat(page, 'revise_good', 'Make the first minutes easier.', 2)
  await expect(page.getByRole('listitem').filter({ hasText: 'Version 2' }).getByText('Current')).toBeVisible()
  await page.getByRole('button', { name: 'Use version 1' }).click()
  await expect(page.getByRole('listitem').filter({ hasText: 'Version 1' }).getByText('Current')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Use version 1' })).toHaveCount(0)

  // Save version 1: the page says what is written, the click is the approval.
  await savePanel(page).getByRole('button', { name: 'Save to MOWL' }).click()
  const group = savePanel(page).getByRole('group', { name: 'Confirm' })
  await expect(group).toContainText('version 1')
  await expect(group).toContainText('It is private')
  await group.getByRole('button', { name: 'Save to MOWL', exact: true }).click()
  await expect(page.getByText('Saved. The workout is in your MOWL account, private.')).toBeVisible(LONG)
  await expect(savedState(page)).toHaveText('Saved to MOWL as version 1. It is up to date.')

  const first = savePrograms()
  expect(first).toHaveLength(1)
  expectProgram(first[0]!, 'good_first_draft')

  // Version 3 by chat from version 1, then Update replaces the saved program in place.
  await reviseByChat(page, 'revise_second', 'Ease the warm-up a little more.', 3)
  await expect(savedState(page)).toHaveText(
    'Saved to MOWL as version 1. The current version (3) has changes that are not saved yet.',
  )
  await confirmWrite(page, 'Update saved workout', 'Update workout')
  await expect(page.getByText('Updated. Your MOWL workout now matches this version.')).toBeVisible(LONG)
  await expect(savedState(page)).toHaveText('Saved to MOWL as version 3. It is up to date.')

  // One program, the same one, with the content of version 3, private, on the linked playlist.
  const after = savePrograms()
  expect(after).toHaveLength(1)
  expect(after[0]!.program_id).toBe(first[0]!.program_id)
  expectProgram(after[0]!, 'revise_second')
  expect(zonesOf(after[0]!)).not.toEqual(zonesOf(first[0]!))
})

/** Save version 1 of a fresh class (nothing injected) and return the program the fake now holds. */
async function savedClass(page: Page, tag: string): Promise<FakeProgram> {
  await signedInWithMowl(page, tag)
  await designAndOpen(page, `E2E ${tag}`, PLAYLISTS.draft.name)
  await confirmWrite(page, 'Save to MOWL', 'Save to MOWL')
  await expect(savedState(page)).toHaveText('Saved to MOWL as version 1. It is up to date.', LONG)
  const programs = savePrograms()
  expect(programs).toHaveLength(1)
  return programs[0]!
}

test('a save whose answer is lost is found by Check again', async ({ page }) => {
  await signedInWithMowl(page, 'lost-save')
  await designAndOpen(page, 'E2E lost save', PLAYLISTS.draft.name)

  // MOWL applies every write but the answer to the last one never arrives, and My Workouts does
  // not show the new program yet: classbeat cannot tell, and does not send it again.
  saveArm(`${LAST_WRITE}:drop`)
  listing(true)
  await confirmWrite(page, 'Save to MOWL', 'Save to MOWL')
  await expect(
    page.getByText('classbeat could not tell whether MOWL took it. Read the box below, then decide.'),
  ).toBeVisible(LONG)
  const box = undecided(page)
  await expect(box.getByRole('heading', { name: 'classbeat could not tell whether your workout was saved' })).toBeVisible()
  await expect(savedState(page)).toHaveText('Not saved to MOWL yet.')
  expect(saveState()).toMatchObject({ writes: LAST_WRITE, programs: [1] }) // sent once, never again

  // MOWL catches up. Check again reads it, finds the complete program and records the save. It
  // writes nothing.
  listing(false)
  await box.getByRole('button', { name: 'Check again' }).click()
  await expect(savePanel(page).getByText('classbeat found the workout in your MOWL account and recorded it as saved.')).toBeVisible()
  await expect(savedState(page)).toHaveText('Saved to MOWL as version 1. It is up to date.')
  await expect(box).toHaveCount(0)
  expect(saveState()).toMatchObject({ writes: LAST_WRITE, programs: [1] })
  expectProgram(savePrograms()[0]!, 'good_first_draft')
})

test('a second workout is made only when the user decides the first was not saved', async ({ page }) => {
  await signedInWithMowl(page, 'again-save')
  await designAndOpen(page, 'E2E again save', PLAYLISTS.draft.name)
  saveArm(`${LAST_WRITE}:drop`)
  listing(true)
  await confirmWrite(page, 'Save to MOWL', 'Save to MOWL')
  const box = undecided(page)
  await expect(box.getByRole('button', { name: 'Do it anyway' })).toBeVisible(LONG)
  expect(saveState().programs).toEqual([1])

  // Do it anyway asks first, and neither asking nor deciding sends anything.
  await box.getByRole('button', { name: 'Do it anyway' }).click()
  await expect(box.getByRole('group', { name: 'Confirm' })).toContainText('this may add a second workout')
  expect(saveState()).toMatchObject({ writes: LAST_WRITE, programs: [1] })
  await box.getByRole('button', { name: 'Yes, it was not saved' }).click()
  await expect(savePanel(page).getByText('Understood. You can save it again now;')).toBeVisible()
  await expect(box).toHaveCount(0)
  expect(saveState()).toMatchObject({ writes: LAST_WRITE, programs: [1] })

  // Only the next Save, with its own confirmation, adds the second program.
  saveArm('')
  await confirmWrite(page, 'Save again', 'Save to MOWL')
  await expect(page.getByText('Saved. The workout is in your MOWL account, private.')).toBeVisible(LONG)
  expect(saveState().programs).toEqual([2])
  const programs = savePrograms()
  expect(programs.map((p) => p.program_id)).toHaveLength(2)
  for (const program of programs) expectProgram(program, 'good_first_draft')
})

test('an update left partly done stays undecided until the user overwrites', async ({ page }) => {
  const saved = await savedClass(page, 'partial-update')
  await reviseByChat(page, 'revise_description', 'Say in the description that the start is easier.', 2)

  // The program header (with the new description) and the first segment are written; the answer
  // to the next write is lost, so the segment list is still the old one.
  saveArm('5:drop')
  await confirmWrite(page, 'Update saved workout', 'Update workout')
  const box = undecided(page)
  await expect(box.getByRole('heading', { name: 'classbeat could not tell whether your workout was updated' })).toBeVisible(LONG)
  await expect(savedState(page)).toHaveText(
    'Saved to MOWL as version 1. The current version (2) has changes that are not saved yet.',
  )
  const partial = savePrograms()
  expect(partial).toHaveLength(1)
  expect(partial[0]!.description).toBe(savedShape('revise_description').description)
  expect(zonesOf(partial[0]!)).toEqual(savedShape('good_first_draft').zones) // segments not replaced yet

  // Check again cannot tell a half-written program from an untouched one: still undecided.
  await box.getByRole('button', { name: 'Check again' }).click()
  await expect(savePanel(page).getByText('classbeat still cannot tell whether the workout was updated.')).toBeVisible()
  await expect(box.getByRole('button', { name: 'Do it anyway' })).toBeVisible()

  // Do it anyway: the user decides it did not happen; the next update overwrites.
  await box.getByRole('button', { name: 'Do it anyway' }).click()
  await expect(box.getByRole('group', { name: 'Confirm' })).toContainText('the next update replaces it')
  await box.getByRole('button', { name: 'Yes, it was not updated' }).click()
  await expect(savePanel(page).getByText('Understood. The next update replaces your MOWL copy with this version.')).toBeVisible()
  saveArm('')
  await savePanel(page).getByRole('button', { name: 'Update again' }).click()
  const group = savePanel(page).getByRole('group', { name: 'Confirm' })
  await expect(group).toContainText('including any changes you made to it in MOWL, or whatever a write that did not finish left there')
  await group.getByRole('button', { name: 'Yes, replace it' }).click()
  await expect(page.getByText('Updated. Your MOWL workout now matches this version.')).toBeVisible(LONG)
  await expect(savedState(page)).toHaveText('Saved to MOWL as version 2. It is up to date.')

  const repaired = savePrograms()
  expect(repaired).toHaveLength(1)
  expect(repaired[0]!.program_id).toBe(saved.program_id)
  expectProgram(repaired[0]!, 'revise_description')
})

test('a workout changed in MOWL is not updated until the user says so', async ({ page }) => {
  const saved = await savedClass(page, 'changed')
  await reviseByChat(page, 'revise_second', 'Ease the warm-up a little more.', 2)

  editInMowl(saved.program_id) // the user renames it in the MOWL app
  const writesBefore = saveState().writes
  await confirmWrite(page, 'Update saved workout', 'Update workout')
  await expect(page.getByText('This workout was changed in MOWL after classbeat saved it. Nothing was written.')).toBeVisible(LONG)
  expect(saveState().writes).toBe(writesBefore)
  expect(savePrograms()[0]!.name).toBe('Renamed in the MOWL app')

  // Update anyway replaces the user's change, after asking.
  await page.getByRole('button', { name: 'Update anyway (replaces your changes in MOWL)' }).click()
  await savePanel(page).getByRole('group', { name: 'Confirm' }).getByRole('button', { name: 'Yes, replace it' }).click()
  await expect(page.getByText('Updated. Your MOWL workout now matches this version.')).toBeVisible(LONG)
  const after = savePrograms()
  expect(after).toHaveLength(1)
  expect(after[0]!.program_id).toBe(saved.program_id)
  expectProgram(after[0]!, 'revise_second')
})

test('after MOWL is connected as another account, only a new version can be saved', async ({ page }) => {
  const old = await savedClass(page, 'other-account')

  // Disconnect and connect again; the fake issues a new session, a different account to it.
  await page.goto('/account')
  const mowl = page.getByRole('region', { name: 'MOWL (Intelligent Cycling)' })
  await mowl.getByRole('button', { name: 'Disconnect MOWL' }).click()
  await mowl.getByRole('group', { name: 'Confirm disconnect' }).getByRole('button', { name: 'Yes, disconnect MOWL' }).click()
  await expect(page.getByText('MOWL is disconnected.')).toBeVisible()
  await connectMowl(page)
  await page.goto('/classes')
  await page.getByRole('link', { name: 'E2E other-account' }).click()

  // The saved workout is history: no Update, and Save of this version says its music is not linked.
  await expect(savedState(page)).toContainText('saved earlier to a previous MOWL connection')
  await expect(page.getByRole('button', { name: /^Update/ })).toHaveCount(0)
  await confirmWrite(page, 'Save to MOWL', 'Save to MOWL')
  await expect(
    page.getByText('The music of this version is not in your connected MOWL account. Change it and save the new version.'),
  ).toBeVisible()
  expect(saveState().programs).toEqual([1]) // nothing reached the new account
  await savePanel(page).getByRole('group', { name: 'Confirm' }).getByRole('button', { name: 'Cancel' }).click()

  // A change re-attaches the playlist to the new account; the new version saves there.
  await reviseByChat(page, 'revise_good', 'Make the first minutes easier.', 2)
  expect(fakeMowl('state', PLAYLISTS.draft.id).attaches).toBe(2)
  await confirmWrite(page, 'Save to MOWL', 'Save to MOWL')
  await expect(page.getByText('Saved. The workout is in your MOWL account, private.')).toBeVisible(LONG)
  const programs = savePrograms()
  expect(programs).toHaveLength(2)
  expect(programs[0]).toMatchObject({ program_id: old.program_id, account: 0 })
  expect(programs[1]).toMatchObject({ account: 1 })
  expectProgram(programs[1]!, 'revise_good')
  expect(saveState().programs).toEqual([1, 1])
})
