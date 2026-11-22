import { type Locator, type Page } from '@playwright/test'
import { expect, test } from './helpers'
import { useModelScript } from './model-script'
import { sql } from './song-helpers'
import { resetSpotify } from './spotify-helpers'
import { resetSpotdown } from './spotdown-helpers'
import { LONG, designOnSpotdown, newStudioUser } from './studio-flow-helpers'
import { resetSwedebeat } from './swedebeat-helpers'

// The Class Preview and Editor (ADR 0042, CP-E3) end to end on the fakes: a studio class designed on
// spotdown's maps is opened in the editor, a boundary is moved with the keyboard, a block is locked,
// the lock is undone, and a second tab's edit makes the first tab's next edit a stale 409 that reloads and
// offers to re-apply. A second test keeps an attribute edit across a chat revise with its "Your edit kept" badge.
// The audio preview has its own spec (editor-preview.spec.ts); here nothing is played. No real spotdown,
// SwedeBeat, Spotify or model: the fakes answer and the worker replays recorded scripts.

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

/** Waits for `target`; when it never shows, the failure says what the page said instead. */
async function shows(page: Page, target: Locator): Promise<void> {
  try {
    await expect(target).toBeVisible(LONG)
  } catch (error) {
    const said = await page.getByTestId('editor-page').innerText().catch(() => '(no editor page)')
    throw new Error(`${(error as Error).message}\nThe editor page said:\n${said.slice(0, 1500)}`)
  }
}

test('edit a studio class by keyboard: move a boundary, lock, undo, survive a stale tab', async ({ page }) => {
  const owner = await newStudioUser(page, 'editor')
  const classId = await designOnSpotdown(page, owner, 'Editor e2e climb')
  const start = currentRevision(classId)

  // The page opens on the class page's link, with the blocks, the songs and Play, nothing loaded yet.
  await page.goto(`/classes/${classId}`)
  await page.getByRole('link', { name: 'Open the editor' }).click()
  await shows(page, page.getByTestId('editor-timeline'))
  await expect(page.getByTestId('block-list').getByRole('button').first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Play' })).toBeVisible()
  await expect(page.locator('audio[src]')).toHaveCount(0) // no clip is asked for before Play

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

  // Lock a block: the lock is shown. The controls wait until the page has read the moved draft.
  const list = page.getByTestId('block-list')
  await list.getByRole('button', { name: /^2\./ }).click()
  await page.getByRole('button', { name: 'Lock the block' }).click()
  await shows(page, page.getByTestId('lock-chip'))
  await expect.poll(() => userEdits(owner), LONG).toBe(2)

  // Undo the lock: the class is back on the version before it.
  await page.getByRole('button', { name: 'Undo' }).click()
  await shows(page, page.getByRole('button', { name: 'Lock the block' }))
  await expect.poll(() => currentRevision(classId), LONG).toBe(moved)

  // A second tab edits first; the first tab's next edit is stale: it reloads, keeps the edit and
  // offers it again, and applying it again is one more version.
  const other = await page.context().newPage()
  await other.goto(`/classes/${classId}/edit`)
  await shows(other, other.getByTestId('editor-timeline'))
  await other.getByTestId('block-list').getByRole('button', { name: /^1\./ }).click()
  await other.getByRole('button', { name: 'Lock the block' }).click()
  await shows(other, other.getByTestId('lock-chip'))
  const before = userEdits(owner)

  await list.getByRole('button', { name: /^3\./ }).click()
  await page.getByRole('button', { name: 'Lock the block' }).click()
  await shows(page, page.getByTestId('stale-notice'))
  expect(userEdits(owner)).toBe(before) // the stale edit was not made
  await page.getByRole('button', { name: 'Make my edit again' }).click()
  await expect.poll(() => userEdits(owner), LONG).toBe(before + 1)
  await expect(page.getByTestId('stale-notice')).toHaveCount(0)
})

test('an attribute edit is kept across a chat revise and shows what the AI suggested', async ({ page }) => {
  const owner = await newStudioUser(page, 'editor-revise')
  const classId = await designOnSpotdown(page, owner, 'Editor e2e revise')

  await page.goto(`/classes/${classId}/edit`)
  await shows(page, page.getByTestId('editor-timeline'))
  await page.getByTestId('block-list').getByRole('button', { name: /^2\./ }).click()
  await page.getByLabel('Standing').click()
  await shows(page, page.getByTestId('override-standing'))
  await expect(page.getByTestId('override-standing')).toContainText('Your edit kept (AI suggested')

  // A chat revise regenerates the class (it cuts the last song); the kept edit is re-applied.
  await page.goto(`/classes/${classId}`)
  await expect(page.getByRole('heading', { name: 'Music timeline' })).toBeVisible(LONG)
  useModelScript('e2e_studio_revise')
  await page.getByLabel('What should change?').fill('Cut the last song shorter')
  await page.getByRole('button', { name: 'Change it', exact: true }).click()
  await expect(page.getByText(/Changed, version \d+/)).toBeVisible(LONG)
  await page.goto(`/classes/${classId}/edit`)
  await shows(page, page.getByTestId('editor-timeline'))
  await page.getByTestId('block-list').getByRole('button', { name: /^2\./ }).click()
  await shows(page, page.getByTestId('override-standing'))
  await expect(page.getByTestId('override-standing')).toContainText('Your edit kept (AI suggested')
})
