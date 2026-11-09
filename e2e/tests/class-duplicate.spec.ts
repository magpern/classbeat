import { randomUUID } from 'node:crypto'
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
  await expect(page.getByText('Saved to MOWL as a private workout.')).toBeVisible(LONG)
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
    headers: {
      'X-CSRF-Token': session.csrf_token as string,
      Origin: new URL(baseURL!).origin,
      'Idempotency-Key': randomUUID(),
    },
    data: { revision_id: '00000000-0000-4000-8000-000000000000', content_hash: '0'.repeat(64), title: 'Mine now' },
  })
  expect(posted.status()).toBe(404)
  await other.close()

  // A replayed request (the same Idempotency-Key, made at run time) makes no second copy and
  // takes no second credit: 201, then 200 with the same class. This copy is the plan's third class.
  const timeline = await (await page.request.get(`/api/classes/${copy}/history`)).json()
  const version = timeline.revisions[0] as { id: string; content_hash: string }
  const ownSession = await (await page.request.get('/api/auth/session')).json()
  const key = randomUUID()
  const replay = () =>
    page.request.post(`/api/classes/${copy}/duplicate`, {
      headers: {
        'X-CSRF-Token': ownSession.csrf_token as string,
        Origin: new URL(baseURL!).origin,
        'Idempotency-Key': key,
      },
      data: { revision_id: version.id, content_hash: version.content_hash, title: 'E2E replayed copy' },
    })
  const classesBefore = (await (await page.request.get('/api/classes')).json()).classes.length as number
  const first = await replay()
  const again = await replay()
  expect([first.status(), again.status()]).toEqual([201, 200])
  expect((await again.json()).class_id).toBe((await first.json()).class_id)
  const classesAfter = (await (await page.request.get('/api/classes')).json()).classes.length as number
  expect(classesAfter).toBe(classesBefore + 1)

  // The free plan's three classes this month are used (the design, the dialog's copy and the
  // replayed one): Duplicate in My classes is refused with the usual sentence, in full.
  const months = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
  ]
  const now = new Date()
  const sentence = `You have used your 3 classes for ${months[now.getUTCMonth()]!}. More from 1 ${months[(now.getUTCMonth() + 1) % 12]!}.`
  await page.goto('/classes')
  await page.getByRole('button', { name: 'Duplicate E2E duplicate', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Duplicate' }).click()
  await expect(page.getByRole('dialog').getByRole('alert')).toHaveText(sentence)
})
