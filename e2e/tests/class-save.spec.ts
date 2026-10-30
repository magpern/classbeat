import { expect, test } from './helpers'
import { PLAYLISTS, fakeMowl } from './design-helpers'
import { savedShape, useModelScript, type ModelScript } from './model-script'
import {
  DRAFT_PLAYLIST_MOWL_ID,
  LAST_WRITE,
  LONG,
  confirmWrite,
  designAndOpen,
  zonesOf,
  type FakeProgram,
  reviseByChat,
  saveArm,
  savePanel,
  savePrograms,
  savedState,
  signedInWithMowl,
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
