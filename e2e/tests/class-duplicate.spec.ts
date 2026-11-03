import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import { PLAYLISTS, fakeMowl } from './design-helpers'
import { useModelScript } from './model-script'
import {
  LONG,
  classId,
  designAndOpen,
  saveArm,
  savePanel,
  savePrograms,
  savedState,
  signedInWithMowl,
} from './class-helpers'
import { audit, stackContext } from './mu-helpers'

// Duplicate a class (ADR 0007 amendment of 2026-11-03 part D; issue #548), on the fakes: the copy
// is a new class with version 1 and no saved state, saving it leaves the source unsaved, another
// user cannot reach it, the plan's monthly classes count it, and the dialog and the copy's page pass
// the axe audit. The fakes' state is shared, so the file runs serially.
test.describe.configure({ mode: 'serial' })
test.setTimeout(240_000)

const HISTORY = 'section[aria-labelledby="revisions-title"]'

test.beforeEach(() => {
  fakeMowl('arm', PLAYLISTS.draft.id)
  saveArm('', true)
})

test.afterAll(() => {
  saveArm('', true)
  useModelScript('good_first_draft')
})

test('duplicate a class, save the copy, isolation and the monthly limit', async ({ page, browser, baseURL }) => {
  await signedInWithMowl(page, 'duplicate')
  await designAndOpen(page, 'E2E duplicate', PLAYLISTS.draft.name)
  const source = classId(page)
  await expect(savedState(page)).toHaveText('Not saved to MOWL yet.')

  // The dialog, audited, then the copy under an edited name.
  await page.getByRole('button', { name: 'Duplicate E2E duplicate' }).click()
  const dialog = page.getByRole('dialog', { name: 'Duplicate this class' })
  await expect(dialog).toContainText('Version 1 becomes version 1 of a new class')
  const name = dialog.getByLabel('Name of the copy')
  await expect(name).toHaveValue('E2E duplicate (copy)')
  const dialogFindings = await audit(page, 'duplicate dialog', dialog.getByRole('button', { name: 'Duplicate' }), '[role="dialog"]')
  expect(dialogFindings.problems).toEqual([])
  await name.fill('E2E duplicate copy')
  await dialog.getByRole('button', { name: 'Duplicate' }).click()

  await expect(page.getByRole('heading', { name: 'E2E duplicate copy' })).toBeVisible(LONG)
  const copy = classId(page)
  expect(copy).not.toBe(source)
  const history = page.locator(HISTORY)
  const entries = history.getByTestId('history-entry')
  await expect(entries).toHaveCount(1)
  await expect(entries.first().getByText('Duplicated from “E2E duplicate”, version 1')).toBeVisible()
  await expect(entries.first().getByText('Current')).toBeVisible()
  await expect(savedState(page)).toHaveText('Not saved to MOWL yet.')
  const pageFindings = await audit(page, 'a duplicated class', entries.first().getByRole('button', { name: 'Viewing version 1' }), HISTORY)
  expect(pageFindings.problems).toEqual([])

  // Saving the copy makes one private program and leaves the source unsaved.
  await savePanel(page).getByRole('button', { name: 'Save to MOWL' }).click()
  const group = savePanel(page).getByRole('group', { name: 'Confirm' })
  await group.getByRole('button', { name: 'Save to MOWL', exact: true }).click()
  await expect(page.getByText('Saved. The workout is in your MOWL account, private.')).toBeVisible(LONG)
  expect(savePrograms()).toHaveLength(1)
  await page.goto(`/classes/${source}`)
  await expect(page.getByRole('heading', { name: 'E2E duplicate', exact: true })).toBeVisible()
  await expect(savedState(page)).toHaveText('Not saved to MOWL yet.')

  // Another user gets the same 404 for the copy and cannot duplicate it.
  const other = await stackContext(browser, baseURL!)
  const otherPage = await other.newPage()
  const email = uniqueEmail('duplicate-other')
  await signUpThroughUi(otherPage, email)
  await signInThroughUi(otherPage, email)
  const read = await otherPage.request.get(`/api/classes/${copy}`)
  expect(read.status()).toBe(404)
  const session = await (await otherPage.request.get('/api/auth/session')).json()
  const posted = await otherPage.request.post(`/api/classes/${copy}/duplicate`, {
    headers: { 'X-CSRF-Token': session.csrf_token as string, Origin: new URL(baseURL!).origin },
    data: { revision_id: '00000000-0000-4000-8000-000000000000', content_hash: '0'.repeat(64), title: 'Mine now' },
  })
  expect(posted.status()).toBe(404)
  await other.close()

  // The free plan's three classes this month: the design and the copy took two; one more copy
  // takes the third, and the next is refused with the usual sentence.
  await page.goto('/classes')
  await page.getByRole('button', { name: 'Duplicate E2E duplicate copy' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Duplicate' }).click()
  await expect(page.getByRole('heading', { name: 'E2E duplicate copy (copy)' })).toBeVisible(LONG)
  await page.goto('/classes')
  await page.getByRole('button', { name: 'Duplicate E2E duplicate', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Duplicate' }).click()
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('You have used')
})
