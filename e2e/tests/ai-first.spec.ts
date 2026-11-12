import type { Page } from '@playwright/test'
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
  classId,
} from './class-helpers'
import { LONG, designFromWords, instructorWithMowl } from './ai-first-helpers'

// MA5 (ADR 0029, plan "Verification"): the AI-first flow end to end against the fake model, MOWL
// and Spotify. A request in words is read, shown as asked or assumed, designed, changed by chat and
// saved through the confirmation. Limits, the hand-off refusal and the axe checks are in
// ai-first-limits.spec.ts and ai-first-a11y.spec.ts.

const POWER_PULL = 'A 30 minute ride with two climbs and a Power Pull after climb 2, 2 minutes long.'
const GAME_ID_OVERFLOW = 6

interface StoredInterval {
  game?: string
  [field: string]: unknown
}
interface StoredDraft {
  name: string
  description: string
  segments: { name: string; kind: string; intervals: StoredInterval[] }[]
}

/** The stored draft of each version of the class by its version number (what Compare reads). */
async function storedDrafts(page: Page): Promise<Map<number, StoredDraft>> {
  const id = classId(page)
  const list = (await (await page.request.get(`/api/classes/${id}/revisions`)).json()) as {
    revisions: { id: string; number: number }[]
  }
  const drafts = await Promise.all(
    list.revisions.map(async (r) => {
      const body = (await (await page.request.get(`/api/classes/${id}/revisions/${r.id}`)).json()) as { content: StoredDraft }
      return [r.number, body.content] as const
    }),
  )
  return new Map(drafts)
}

/** Every field of every interval, the games aside. */
const withoutGames = (d: StoredDraft) => ({
  ...d,
  segments: d.segments.map((s) => ({ ...s, intervals: s.intervals.map(({ game: _game, ...rest }) => rest) })),
})
const gamesOf = (d: StoredDraft) => d.segments.flatMap((s) => s.intervals.map((i) => i.game ?? null))

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
  // One interval carries Overflow's id, the one that carried the Power Pull; every other has none.
  const games = saved[0]!.game_ids ?? []
  expect(games.filter((g) => g !== null)).toEqual([GAME_ID_OVERFLOW])
  // The neighbouring intervals are those of the first version: the same lengths, zones, rpm, cues and
  // names (every field, not the zones alone). Only the game differs: Overflow where the Power Pull was.
  const versions = await storedDrafts(page)
  expect(versions.size).toBe(2)
  const first = versions.get(1)!
  const second = versions.get(2)!
  expect(withoutGames(second)).toEqual(withoutGames(first))
  // The zones are those of the designed class, spelled out as well as compared field by field.
  expect(second.segments.flatMap((s) => s.intervals.map((i) => i.zone as number))).toEqual([2, 3, 4, 5, 1, 4, 5, 4, 2, 3, 1])
  const was = gamesOf(first)
  const now = gamesOf(second)
  expect(was.filter((g) => g !== null)).toEqual(['power_pull'])
  expect(now).toEqual(was.map((g) => (g === null ? null : 'overflow')))
  // What MOWL holds has the second version's lengths and zones.
  expect(saved[0]!.intervals.map((i) => i[1])).toEqual(
    second.segments.flatMap((s) => s.intervals.map((i) => i.zone as number)),
  )
})

test('a generic game request ends with the game the model chose', async ({ page }) => {
  test.setTimeout(240_000)
  await instructorWithMowl(page, 'aigeneric')
  await designFromWords(
    page,
    'A 30 minute ride with two climbs and a game after climb 2.',
    { reading: 'ai_first_read_any', design: 'ai_first_design_any' },
    { music: PLAYLISTS.draft.name },
  )
  await expect(page.getByRole('heading', { name: /Invented/ })).toBeVisible(LONG)
  // The fake model chose a Racetrack (`ai_first_design_any`): that badge, none of the other three.
  const segment = page.getByRole('region', { name: 'Game' })
  await expect(segment.getByText('Racetrack', { exact: true })).toBeVisible()
  for (const other of ['Power Pull', 'Fast Lane', 'Overflow']) {
    await expect(segment.getByText(other, { exact: true })).toHaveCount(0)
  }
})
