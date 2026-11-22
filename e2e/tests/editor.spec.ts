import { expect, test } from './helpers'
import { useModelScript } from './model-script'
import { sql } from './song-helpers'
import { resetSpotify } from './spotify-helpers'
import { resetSpotdown } from './spotdown-helpers'
import { LONG, designOnSpotdown, newStudioUser } from './studio-flow-helpers'
import { resetSwedebeat } from './swedebeat-helpers'

// The Class Preview and Editor (ADR 0042, CP-E3) end to end on the fakes: a studio class designed on
// spotdown's maps is opened in the editor, a boundary is moved with the keyboard, a block is locked,
// the lock is undone, an attribute edit survives a chat revise with its "Your edit kept" badge, and a
// second tab's edit makes the first tab's next edit a stale 409 that reloads and offers to re-apply.
// Nothing plays: the page has a silent cursor and no audio. No real spotdown, SwedeBeat, Spotify or
// model: the fakes answer and the worker replays recorded scripts.

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

const userEdits = (owner: string) =>
  Number(sql(`SELECT count(*) FROM class_revisions WHERE owner_id = '${owner}' AND provenance ->> 'origin' = 'user_edit'`))
const currentRevision = (classId: string) => sql(`SELECT current_revision_id FROM classes WHERE id = '${classId}'`)

test('edit a studio class by keyboard: move a boundary, lock, undo, keep an edit across a revise, survive a stale tab', async ({
  page,
}) => {
  const owner = await newStudioUser(page, 'editor')
  const classId = await designOnSpotdown(page, owner, 'Editor e2e climb')
  const start = currentRevision(classId)

  // The page opens on the class page's link, with the blocks, the songs and nothing to play.
  await page.goto(`/classes/${classId}`)
  await page.getByRole('link', { name: 'Open the editor' }).click()
  await expect(page.getByTestId('editor-timeline')).toBeVisible(LONG)
  await expect(page.getByTestId('block-list').getByRole('button').first()).toBeVisible()
  await expect(page.locator('audio, video')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /play/i })).toHaveCount(0)

  // Drag a boundary with the keyboard: the arrows choose, Enter makes one edit (one new version).
  const handle = page.getByRole('slider', { name: /^End of block 1,/ })
  await handle.focus()
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  expect(userEdits(owner)).toBe(0) // choosing sends nothing
  await page.keyboard.press('Enter')
  await expect.poll(() => userEdits(owner), LONG).toBe(1)
  const moved = currentRevision(classId)
  expect(moved).not.toBe(start)

  // Lock a block: its boundary handle goes (timing is locked) and the lock is shown.
  const list = page.getByTestId('block-list')
  await list.getByRole('button', { name: /^2\./ }).click()
  await page.getByRole('button', { name: 'Lock the block' }).click()
  await expect(page.getByTestId('lock-chip')).toBeVisible(LONG)
  await expect.poll(() => userEdits(owner), LONG).toBe(2)

  // Undo the lock: the class is back on the version before it.
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByRole('button', { name: 'Lock the block' })).toBeVisible(LONG)
  await expect.poll(() => currentRevision(classId), LONG).toBe(moved)

  // An attribute edit is kept as the user's own and shows what the AI suggested.
  await page.getByLabel('Standing').click()
  await expect(page.getByTestId('override-standing')).toContainText('Your edit kept (AI suggested', LONG)

  // A chat revise regenerates the class: the kept edit is still there, with its badge.
  await page.goto(`/classes/${classId}`)
  useModelScript('e2e_studio_revise')
  await page.getByLabel('What should change?').fill('Cut the last song shorter')
  await page.getByRole('button', { name: 'Change it', exact: true }).click()
  await expect(page.getByText(/Changed, version \d+/)).toBeVisible(LONG)
  await page.goto(`/classes/${classId}/edit`)
  await expect(page.getByTestId('editor-timeline')).toBeVisible(LONG)
  await page.getByTestId('block-list').getByRole('button', { name: /^2\./ }).click()
  await expect(page.getByTestId('override-standing')).toContainText('Your edit kept (AI suggested', LONG)

  // A second tab edits first; the first tab's next edit is stale: it reloads, keeps the edit and
  // offers it again, and applying it again is one more version.
  const other = await page.context().newPage()
  await other.goto(`/classes/${classId}/edit`)
  await expect(other.getByTestId('editor-timeline')).toBeVisible(LONG)
  await other.getByTestId('block-list').getByRole('button', { name: /^1\./ }).click()
  await other.getByRole('button', { name: 'Lock the block' }).click()
  await expect(other.getByTestId('lock-chip')).toBeVisible(LONG)
  const before = userEdits(owner)

  await page.getByTestId('block-list').getByRole('button', { name: /^3\./ }).click()
  await page.getByRole('button', { name: 'Lock the block' }).click()
  await expect(page.getByTestId('stale-notice')).toBeVisible(LONG)
  expect(userEdits(owner)).toBe(before) // the stale edit was not made
  await page.getByRole('button', { name: 'Make my edit again' }).click()
  await expect.poll(() => userEdits(owner), LONG).toBe(before + 1)
  await expect(page.getByTestId('stale-notice')).toHaveCount(0)
})
