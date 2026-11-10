import { expect, test } from './helpers'
import { PLAYLISTS, fakeMowl } from './design-helpers'
import {
  confirmWrite,
  reviseByChat,
  saveArm,
  savePrograms,
  saveState,
  savedState,
  LAST_WRITE,
} from './class-helpers'
import { LONG, designFromWords, instructorWithMowl } from './ai-first-helpers'

// MA5 (ADR 0029, plan "Verification"): the AI-first flow end to end against the fake model, MOWL
// and Spotify. A request in words is read, shown as asked or assumed, designed, changed by chat and
// saved through the confirmation. Limits, the hand-off refusal and the axe checks are in
// ai-first-limits.spec.ts and ai-first-a11y.spec.ts.

const POWER_PULL = 'A 30 minute ride with two climbs and a Power Pull after climb 2, 2 minutes long.'
const GAME_ID_OVERFLOW = 6

test.beforeEach(() => {
  fakeMowl('arm', PLAYLISTS.draft.id)
  saveArm('', true) // no programs, no injected failures
})

test('a Power Pull after climb 2 is read, designed, changed to Overflow and saved only on confirmation', async ({
  page,
}) => {
  test.setTimeout(240_000)
  await instructorWithMowl(page, 'aifirst')
  await designFromWords(
    page,
    POWER_PULL,
    { reading: 'ai_first_read_pull', design: 'ai_first_design_pull' },
    { music: PLAYLISTS.draft.name },
  )

  // What was said is kept as said and listed as asked; the rest is marked assumed.
  const reading = page.getByTestId('interpretation')
  await expect(reading.getByText('Length: 30 minutes')).toBeVisible()
  await expect(reading.getByText('Climbs: 2', { exact: true })).toBeVisible()
  await expect(reading.getByText('Power Pull, 2 min')).toBeVisible()
  await expect(reading.getByText('Assumed')).toBeVisible()
  await expect(reading.getByText('Effort: Moderate')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Invented Power Pull ride' })).toBeVisible(LONG)
  // The explicit length is the designed length, not a default.
  await expect(page.getByText('30:00', { exact: true }).first()).toBeVisible()
  // The game badge of the segment after climb 2.
  const gameSegment = page.getByRole('region', { name: 'Game' })
  await expect(gameSegment.getByText('Power Pull')).toBeVisible()

  // Revise to Overflow: the history names the change.
  await reviseByChat(page, 'ai_first_revise_overflow', 'Change it to Overflow.', 2)
  await expect(gameSegment.getByText('Overflow')).toBeVisible()
  await expect(gameSegment.getByText('Power Pull')).toHaveCount(0)
  const history = page.getByRole('list', { name: 'Version history' })
  await expect(history.getByText('Change it to Overflow.')).toBeVisible()
  await history.getByRole('button', { name: 'Compare version 1' }).click()
  await history.getByRole('button', { name: 'Compare version 2' }).click()
  await page.getByRole('button', { name: 'Compare versions 1 and 2' }).click()
  await expect(page.getByTestId('comparison').getByText(/Power Pull to Overflow/)).toBeVisible()

  // Nothing reached MOWL while the class was designed and changed.
  expect(saveState().writes).toBe(0)
  expect(savePrograms()).toEqual([])

  // The confirmation is the write.
  await confirmWrite(page, 'Save to MOWL', 'Save to MOWL')
  await expect(savedState(page)).toContainText('Saved', LONG)
  const saved = savePrograms()
  expect(saved).toHaveLength(1)
  expect(saveState().writes).toBeLessThanOrEqual(LAST_WRITE)
  // One interval carries Overflow's id; every other interval has no game.
  const games = saved[0]!.game_ids ?? []
  expect(games.filter((g) => g !== null)).toEqual([GAME_ID_OVERFLOW])
  // The neighbouring intervals are those of the version: the same zones as the designed class.
  expect(saved[0]!.intervals.map((i) => i[1])).toEqual([2, 3, 4, 5, 1, 4, 5, 4, 2, 3, 1])
})

test('a generic game request ends with one of the four games', async ({ page }) => {
  test.setTimeout(240_000)
  await instructorWithMowl(page, 'aigeneric')
  await designFromWords(
    page,
    'A 30 minute ride with two climbs and a game after climb 2.',
    { reading: 'ai_first_read_any', design: 'ai_first_design_any' },
    { music: PLAYLISTS.draft.name },
  )
  await expect(page.getByRole('heading', { name: /Invented/ })).toBeVisible(LONG)
  const badge = page.getByRole('region', { name: 'Game' }).getByText(/^(Racetrack|Power Pull|Fast Lane|Overflow)$/)
  await expect(badge).toBeVisible()
})
