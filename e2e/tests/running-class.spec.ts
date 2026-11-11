import { expect, test } from './helpers'
import { PLAYLISTS, fakeMowl } from './design-helpers'
import { instructorWithMowl } from './ai-first-helpers'
import { savedShape, useModelScript, type ModelScript } from './model-script'
import {
  DRAFT_PLAYLIST_MOWL_ID,
  LONG,
  confirmWrite,
  designRunningAndOpen,
  reviseByChat,
  saveArm,
  savePanel,
  savePrograms,
  savedState,
  setProgramSport,
  signedInWithMowl,
  storeRunning,
  zonesOf,
  type FakeProgram,
} from './class-helpers'

// M8 F1 (ADR 0018, issue #466): a running class designed, saved and updated on the fakes. The stack
// turns running on for web and worker (compose.e2e.yaml); the worker replays the recorded running
// scripts (model-script.ts) and the fake MOWL stores the program the way the library writes it
// (ActivityTypeID 1, segments 0, an incline per interval). The tests share the fake's program store
// and the worker's script, so they run one after the other.
test.describe.configure({ mode: 'serial' })
test.setTimeout(180_000)

test.beforeAll(() => {
  storeRunning('on') // #509: nothing stored is off
})

test.beforeEach(() => {
  fakeMowl('arm', PLAYLISTS.draft.id)
  saveArm('', true) // no programs, no injected failures
})

test.afterAll(() => {
  saveArm('', true)
  useModelScript('good_first_draft')
  storeRunning(null)
})

/** The program holds the running content of the script's draft, private, on the linked playlist,
 *  with the sport the library writes: the program 1, every segment 0. */
function expectRunningProgram(program: FakeProgram, script: ModelScript): void {
  const shape = savedShape(script)
  expect(program).toMatchObject({
    name: shape.name,
    description: shape.description,
    segments: shape.segments,
    public: false,
    playlist_id: DRAFT_PLAYLIST_MOWL_ID,
    activity_type: 1,
    segment_activity_types: shape.segments.map(() => 0),
  })
  expect(zonesOf(program)).toEqual(shape.zones)
  // Zones 1 to 5 only, and an incline of 0 (flat) or 1 on every interval.
  expect(Math.max(...zonesOf(program))).toBeLessThanOrEqual(5)
  expect(program.gradients).toHaveLength(shape.zones.length)
  for (const gradient of program.gradients ?? []) expect([0, 1]).toContain(gradient)
}

test('the new-class form: Running hides the bike-only knobs, Cycling brings them back', async ({ page }) => {
  // The Individual plan: the Free plan has no FTP tests (M7a-2), and this test shows the knob.
  await instructorWithMowl(page, 'run-form')
  await page.getByRole('link', { name: 'New class' }).click()
  await page.getByRole('link', { name: /^Advanced/ }).click()
  const form = page.getByRole('form', { name: 'New class' })
  const sport = form.getByRole('radiogroup', { name: 'Sport' })
  await expect(sport.getByRole('radio', { name: 'Cycling' })).toBeChecked()
  await expect(form.getByLabel('Climbs', { exact: true })).toBeVisible()
  await expect(form.getByLabel('FTP test')).toBeVisible()

  await sport.getByRole('radio', { name: 'Running' }).click()
  await expect(sport.getByRole('radio', { name: 'Running' })).toBeChecked()
  await expect(form.getByRole('heading', { name: 'The run' })).toBeVisible()
  for (const label of ['Climbs', 'Sprints', 'Races', 'FTP test']) {
    await expect(form.getByLabel(label, { exact: true })).toHaveCount(0)
  }
  await expect(form.getByRole('radio', { name: 'Pick for me', exact: true })).toHaveCount(0)
  await expect(form.getByRole('radio', { name: 'Swedebeat mix', exact: true })).toHaveCount(0)

  await sport.getByRole('radio', { name: 'Cycling' }).click()
  await expect(form.getByLabel('Climbs', { exact: true })).toBeVisible()
  await expect(form.getByRole('heading', { name: 'The ride' })).toBeVisible()
})

test('design a running class, save it, change it and update the same program', async ({ page }) => {
  await signedInWithMowl(page, 'run-save')
  await designRunningAndOpen(page, 'E2E running save', PLAYLISTS.draft.name)

  // The class page: the sport, the incline column instead of cadence and position, five zones.
  await expect(page.getByTestId('sport')).toHaveText('Running')
  await expect(savedState(page)).toHaveText('Not saved to MOWL yet.')
  await expect(page.getByRole('columnheader', { name: 'Incline' }).first()).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Cadence' })).toHaveCount(0)
  await expect(page.getByRole('columnheader', { name: 'Position' })).toHaveCount(0)
  const zoneTime = page.getByRole('region', { name: 'Time in each zone' })
  await expect(zoneTime.getByRole('listitem')).toHaveCount(5)
  await expect(zoneTime.getByText('Z5 VO2max')).toBeVisible()
  await expect(zoneTime.getByText('Z6')).toHaveCount(0)
  // Sanity check only: Change songs needs approved songs, which a running class never has.
  await expect(page.getByRole('button', { name: 'Change songs' })).toHaveCount(0)

  // Save: the fake MOWL holds a running program (ActivityTypeID 1, segments 0).
  await confirmWrite(page, 'Save to MOWL', 'Save to MOWL')
  await expect(page.getByText('Saved to MOWL as a private workout.')).toBeVisible(LONG)
  await expect(savedState(page)).toHaveText('Saved to MOWL as version 1. Up to date.')
  const first = savePrograms()
  expect(first).toHaveLength(1)
  expectRunningProgram(first[0]!, 'running_first_draft')

  // It is in My classes as a saved class.
  await page.goto('/classes')
  await expect(page.getByRole('link', { name: 'E2E running save' })).toBeVisible()
  await page.getByRole('link', { name: 'E2E running save' }).click()

  // Version 2 by chat, then Update replaces the same program in place, still a running one.
  await reviseByChat(page, 'running_revise', 'Make the first jog easier.', 2)
  await expect(page.getByTestId('sport')).toHaveText('Running')
  await expect(savedState(page)).toHaveText(
    'Saved to MOWL as version 1. Version 2 has unsaved changes.',
  )
  await confirmWrite(page, 'Update workout', 'Update workout')
  await expect(page.getByText('Updated. MOWL now matches this version.')).toBeVisible(LONG)
  await expect(savedState(page)).toHaveText('Saved to MOWL as version 2. Up to date.')
  const after = savePrograms()
  expect(after).toHaveLength(1)
  expect(after[0]!.program_id).toBe(first[0]!.program_id)
  expectRunningProgram(after[0]!, 'running_revise')
  expect(zonesOf(after[0]!)).not.toEqual(zonesOf(first[0]!))
})

test('a running class is not updated onto a program of another sport', async ({ page }) => {
  await signedInWithMowl(page, 'run-sport')
  await designRunningAndOpen(page, 'E2E running sport', PLAYLISTS.draft.name)
  await confirmWrite(page, 'Save to MOWL', 'Save to MOWL')
  await expect(savedState(page)).toHaveText('Saved to MOWL as version 1. Up to date.', LONG)
  const saved = savePrograms()
  expect(saved).toHaveLength(1)
  const program = saved[0]!

  // In MOWL the program is now a cycling one (the user swapped it in the app).
  setProgramSport(program.program_id, 0)
  await reviseByChat(page, 'running_revise', 'Make the first jog easier.', 2)
  await confirmWrite(page, 'Update workout', 'Update workout')
  await expect(
    page.getByText(
      'This MOWL workout is for a different sport, so it was not replaced. Nothing was written.',
    ),
  ).toBeVisible(LONG)
  // It is not a change the user can override: no "Update anyway" anywhere on the page.
  await expect(page.getByRole('button', { name: /Update anyway/ })).toHaveCount(0)

  // Nothing was written: the same program, still the first version's content, still cycling.
  const after = savePrograms()
  expect(after).toHaveLength(1)
  expect(after[0]).toMatchObject({ program_id: program.program_id, name: program.name, activity_type: 0 })
  expect(zonesOf(after[0]!)).toEqual(zonesOf(program))
  await expect(savedState(page)).toHaveText(
    'Saved to MOWL as version 1. Version 2 has unsaved changes.',
  )
})

test('with the admin switch off the form has no Running control', async ({
  page,
}) => {
  await signedInWithMowl(page, 'run-off')
  await page.getByRole('link', { name: 'New class' }).click()
  await page.getByRole('link', { name: /^Advanced/ }).click()
  const form = page.getByRole('form', { name: 'New class' })
  await expect(form.getByRole('radio', { name: 'Running', exact: true })).toBeVisible()

  // The stored admin flag can only tighten the environment's (ADR 0018 section 5).
  storeRunning('off')
  try {
    await page.reload()
    const off = page.getByRole('form', { name: 'New class' })
    await expect(off.getByLabel('Class name')).toBeVisible()
    await expect(off.getByRole('radio', { name: 'Running', exact: true })).toHaveCount(0)
    await expect(off.getByText('Sport', { exact: true })).toHaveCount(0)
    await expect(off.getByLabel('Climbs', { exact: true })).toBeVisible()
    // #509: the admin's DELETE (nothing stored) is off too, not on as before.
    storeRunning(null)
    await page.reload()
    const cleared = page.getByRole('form', { name: 'New class' })
    await expect(cleared.getByLabel('Class name')).toBeVisible()
    await expect(cleared.getByRole('radio', { name: 'Running', exact: true })).toHaveCount(0)
  } finally {
    storeRunning('on')
  }
})
