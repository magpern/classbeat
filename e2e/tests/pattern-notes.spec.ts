import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import { PLAYLISTS, connectMowl, fakeMowl, startDesign } from './design-helpers'
import { audit } from './mu-helpers'

// ADR 0017 section 4 (P6): the "Pattern notes" block on the class page, against the real dismiss
// and undo routes. The recorded draft decides whether the engine finds notes, so the revision
// answer is given two hits on the way to the browser; the dismissal is stored by the real API.
test.setTimeout(120_000)
const LONG = { timeout: 60_000 }

const HITS = [
  {
    rule: 'warmup_short',
    severity: 'warn',
    metric: 'first_zone4plus_s',
    value: 240,
    bounds: [370, null],
    explained_by_brief: false,
  },
  {
    rule: 'cooldown_long',
    severity: 'info',
    metric: 'cooldown_s',
    value: 300,
    bounds: [null, 188],
    explained_by_brief: true,
  },
]

test('pattern notes: shown, dismissed, shown again, and axe clean', async ({ page }) => {
  fakeMowl('arm', PLAYLISTS.draft.id)
  const email = uniqueEmail('notes')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await connectMowl(page)
  await startDesign(page, 'E2E notes', PLAYLISTS.draft.name)
  await expect(page.getByRole('heading', { name: 'Your class is ready' })).toBeVisible(LONG)

  await page.route(/\/api\/classes\/[0-9a-f-]{36}\/revisions\/[0-9a-f-]{36}$/, async (route) => {
    const real = await route.fetch()
    const body = (await real.json()) as Record<string, unknown>
    await route.fulfill({
      response: real,
      json: { ...body, rule_hits: HITS, notes_use_current_rules: true },
    })
  })
  await page.goto('/classes')
  await page.getByRole('link', { name: /E2E notes/ }).first().click()
  const block = page.getByTestId('pattern-notes')
  await expect(block).toBeVisible()
  await expect(page.getByTestId('pattern-note').first()).toBeHidden() // collapsed
  await block.locator('summary').click()
  await expect(page.getByTestId('pattern-note')).toHaveCount(2)
  await expect(page.getByTestId('notes-current-rules')).toHaveText('Notes use the current rules')
  await expect(page.getByRole('alert')).toHaveCount(0)

  const findings = await audit(page, 'class page with pattern notes', block.locator('summary'))
  expect(findings.problems).toEqual([])

  const classId = /\/classes\/([0-9a-f-]{36})/.exec(page.url())?.[1] ?? ''
  await page.getByRole('button', { name: /^Dismiss: Most classes of this kind ride a few/ }).click()
  await expect(page.getByRole('button', { name: /^Show again:/ })).toBeFocused()
  await expect(page.getByTestId('pattern-notes-status')).toBeEmpty()
  // The real route stored it (idempotent PUT answers the list).
  const stored = await page.request.put(`/api/classes/${classId}/dismissed-rules/warmup_short`, {
    headers: { 'X-CSRF-Token': await csrf(page), Origin: new URL(page.url()).origin },
  })
  expect(await stored.json()).toEqual({ dismissed_rules: ['warmup_short'] })

  await page.getByRole('button', { name: /^Show again:/ }).click()
  await expect(page.getByTestId('pattern-note')).toHaveCount(2)
})

async function csrf(page: import('@playwright/test').Page): Promise<string> {
  const session = await page.request.get('/api/auth/session')
  const body = (await session.json()) as { csrf_token?: string }
  return body.csrf_token ?? ''
}
