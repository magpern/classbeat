import { expect, test } from './helpers'
import { PLAYLISTS, disconnectMowl, fakeMowl } from './design-helpers'
import { designAndOpen, saveArm, savePrograms, saveState } from './class-helpers'
import { heldOf } from './m5-helpers'
import { LONG, designFromWords, holdHandoff, instructorWithMowl, pickMusic, say } from './ai-first-helpers'
import { useModelScript } from './model-script'

// MA5 (ADR 0029): what a request cannot be, and a hand-off that is refused. The class is never
// quietly changed to fit: a hard limit shows under "Not as asked" with its reason, and a refusal
// is shown with a way on. Fakes only; nothing reaches a real account.

test.beforeEach(() => {
  fakeMowl('arm', PLAYLISTS.draft.id)
  saveArm('', true)
})

test('a 90 minute request on a 60 minute plan shows its length under Not as asked', async ({ page }) => {
  test.setTimeout(240_000)
  await instructorWithMowl(page, 'ailong')
  await designFromWords(
    page,
    'A 90 minute ride with two climbs.',
    { reading: 'ai_first_read_long', design: 'ai_first_design_long' },
    { music: 'No music' },
  )
  const reading = page.getByTestId('interpretation')
  await expect(page.getByRole('heading', { name: /Invented/ })).toBeVisible(LONG)
  // The length is listed under Not as asked with its reason, and not under As you asked.
  const notAsked = reading.getByRole('region', { name: 'Not as asked' })
  await expect(notAsked.getByText('Length: 90 minutes')).toBeVisible()
  await expect(notAsked.getByText('It is longer than your plan allows.')).toBeVisible()
  await expect(reading.getByRole('region', { name: 'As you asked' }).getByText('Length: 90 minutes')).toHaveCount(0)
  // The designed class is the 60 minutes the plan allows, not 90.
  await expect(page.getByText('60:00', { exact: true }).first()).toBeVisible()
  expect(saveState().writes).toBe(0)
})

test('a refused hand-off is shown with a way on, takes no credit and writes nothing', async ({ page }) => {
  test.setTimeout(240_000)
  await instructorWithMowl(page, 'airefuse')
  const before = await heldOf(page)
  useModelScript('ai_first_read_pull')
  const held = await holdHandoff(page)
  await say(page, 'A 30 minute ride with two climbs and a Power Pull after climb 2.')
  await pickMusic(page, PLAYLISTS.draft.name)
  await page.getByRole('button', { name: 'Design my class' }).click()
  await expect(page.getByTestId('interpretation')).toBeVisible(LONG)
  // MOWL is disconnected in a second tab while the hand-off waits: the backend refuses it.
  const other = await page.context().newPage()
  await disconnectMowl(other)
  await other.close()
  held.release()
  const stopped = page.getByTestId('handoff-stopped')
  await expect(stopped).toBeVisible(LONG)
  await expect(stopped.getByText(/MOWL was not connected/)).toBeVisible()
  await expect(stopped.getByRole('button', { name: 'Interpret again' })).toBeVisible()
  await expect(stopped.getByRole('link', { name: 'Go to your account' })).toBeVisible()
  expect(await heldOf(page)).toBe(before)
  expect(saveState().writes).toBe(0)
  expect(savePrograms()).toEqual([])
})

test('the Advanced form still designs a class with its explicit values', async ({ page }) => {
  test.setTimeout(240_000)
  await instructorWithMowl(page, 'aiadvanced')
  await designAndOpen(page, 'E2E advanced', PLAYLISTS.draft.name)
  await expect(page.getByText('30:00', { exact: true }).first()).toBeVisible()
  expect(saveState().writes).toBe(0)
})
