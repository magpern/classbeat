import { expect, nextTotp, test } from './helpers'
import {
  FREE,
  LONG,
  INSTRUCTOR,
  burst,
  contextsOf,
  createDesign,
  endedAs,
  expectClasses,
  expectHeld,
  lapseAdminCheck,
  newUser,
  statusesOf,
} from './m5-helpers'
import type { Page } from '@playwright/test'

// M5 T1 (#228, ADR 0011 sections 6 and 10): an admin with TOTP grants Instructor and the user's
// limit rises at once; the admin refunds a committed credit; both are in the audit log; a redeem
// code raises a second user's limit. Fakes only (the fake MOWL, the FakeProvider).
test.setTimeout(420_000)

/** The first admin visit: the page offers two-factor set-up (the session still says `user`). */
async function enrollTotp(page: Page): Promise<string> {
  await page.goto('/admin')
  await expect(page.getByRole('heading', { name: 'Set up two-factor' })).toBeVisible()
  await page.getByRole('button', { name: 'Set up an authenticator app' }).click()
  const secret = (await page.locator('code').first().textContent())!.trim()
  await page.getByLabel('Code from the app').fill(await nextTotp(secret))
  await page.getByRole('button', { name: 'Turn on two-factor' }).click()
  await page.getByRole('button', { name: 'I have saved them' }).click()
  await expect(page.getByRole('heading', { name: 'Admin', exact: true })).toBeVisible()
  return secret
}

/** Answer the step-up dialog when an admin write asks for it. */
async function stepUp(page: Page, secret: string): Promise<void> {
  const dialog = page.getByRole('dialog', { name: 'Enter a two-factor code' })
  if (await dialog.isVisible({ timeout: 3_000 }).catch(() => false)) {
    await dialog.getByLabel('Authenticator or recovery code').fill(await nextTotp(secret))
    await dialog.getByRole('button', { name: 'Continue' }).click()
    await expect(dialog).toBeHidden()
  }
}

test('an admin grants a plan, refunds a credit, and a code raises another limit', async ({ browser, baseURL }) => {
  const admin = await newUser(browser, baseURL, 'm5-admin', false, true)
  const secret = await enrollTotp(admin.page)

  // A Free user uses all three classes; the fourth is refused.
  const user = await newUser(browser, baseURL, 'm5-user')
  for (let n = 0; n < FREE.classes; n++) {
    const made = await createDesign(user.context, user.connectionId)
    expect(made.status).toBe(201)
    expect(await endedAs(user.page, made.body.id!)).toBe('completed')
  }
  await expectClasses(user.page, 3, FREE.classes)
  expect((await createDesign(user.context, user.connectionId)).status).toBe(409)

  // The admin finds the user and grants Instructor (thirty classes, two jobs at a time).
  const page = admin.page
  await page.goto('/admin/users')
  await page.getByLabel('Email starts with').fill(user.email.split('@')[0]!)
  await page.getByRole('button', { name: 'Search' }).click()
  await page.getByRole('link', { name: user.email }).click()
  await expect(page.getByTestId('admin-user-plan')).toContainText('Plan: Free')
  await page.getByRole('form', { name: 'Grant' }).getByRole('button', { name: 'Grant' }).click()
  await stepUp(page, secret)
  await expect(page.getByTestId('admin-user-plan')).toContainText('Plan: Instructor')

  // At once: the user's page shows the new limit and two creates start together.
  await expectClasses(user.page, 3, INSTRUCTOR.classes)
  const contexts = await contextsOf(browser, user.page, 3)
  const more = await burst(contexts, user.connectionId, 6)
  expect(statusesOf(more)).toEqual([201, 201, 429, 429, 429, 429])
  for (const busy of more.filter((a) => a.status === 429)) {
    expect(busy.body).toEqual({ detail: { reason: 'busy', limit: INSTRUCTOR.concurrent } })
  }
  for (const made of more.filter((a) => a.status === 201)) expect(await endedAs(user.page, made.body.id!)).toBe('completed')
  await expectHeld(user.page, 5)

  // The admin refunds one used credit; the user's count drops by one, once.
  // The worker's settle pass (every 30 s) turns the finished runs' credits into used ones.
  const credits = page.getByRole('table', { name: 'Credits' })
  const used = credits.locator('tbody tr').filter({ hasText: 'used' })
  await expect
    .poll(async () => {
      await page.reload()
      await expect(credits.locator('tbody tr').first()).toBeVisible()
      return used.count()
    }, LONG)
    .toBe(5)
  await credits.getByRole('button', { name: 'Refund' }).first().click()
  await credits.getByRole('button', { name: 'Confirm refund' }).click()
  await stepUp(page, secret)
  await expect(page.getByText('Refunded. The class no longer counts this month.')).toBeVisible()
  await expect(credits.locator('tbody tr').filter({ hasText: 'refunded' })).toHaveCount(1, LONG)
  await expectHeld(user.page, 4)
  await expectClasses(user.page, 4, INSTRUCTOR.classes)
  await expect(credits.locator('tbody tr').filter({ hasText: 'used' })).toHaveCount(4)

  // Both are in the audit log.
  await page.goto('/admin/audit')
  const mine = page.locator('tbody tr').filter({ hasText: admin.email })
  await expect(mine.filter({ hasText: 'user.grant' })).toHaveCount(1)
  await expect(mine.filter({ hasText: 'refund.credit' })).toHaveCount(1)

  // A redeem code, made by the admin, raises a second Free user's limit.
  await page.goto('/admin/codes')
  const form = page.getByRole('form', { name: 'Create a redeem code' })
  const until = new Date(Date.now() + 7 * 86_400_000).toISOString().slice(0, 10)
  await form.getByLabel('Label').fill('E2E one use')
  await form.getByLabel('How many people can use it').fill('1')
  await form.getByLabel('Can be redeemed until').fill(until)
  await form.getByRole('button', { name: 'Create redeem code' }).click()
  await stepUp(page, secret)
  const code = (await page.getByTestId('one-time-code').locator('p.select-all').textContent())!.trim()
  expect(code.length).toBeGreaterThan(8)

  const other = await newUser(browser, baseURL, 'm5-redeem')
  await expectClasses(other.page, 0, FREE.classes)
  await other.page.getByLabel('Redeem a code').fill(code)
  await other.page.getByRole('button', { name: 'Redeem', exact: true }).click()
  await expect(other.page.getByText('The code is redeemed.')).toBeVisible()
  await expect(other.page.getByTestId('plan-name')).toContainText('Instructor')
  await expectClasses(other.page, 0, INSTRUCTOR.classes)

  for (const u of [admin, user, other]) await u.context.close()
})

// #330: an admin whose TOTP check lapsed (12 hours) is shown the code prompt, not "not an admin
// account"; after the code the admin area opens. A non-admin sees no prompt and no admin page.
test('a lapsed admin is asked for a code and the admin area opens after it', async ({ browser, baseURL }) => {
  const admin = await newUser(browser, baseURL, 'm5-lapse', false, true)
  const page = admin.page
  const secret = await enrollTotp(page)

  lapseAdminCheck(admin.email)
  await page.goto('/admin/users')
  await expect(page.getByRole('heading', { name: 'Two-factor check' })).toBeVisible()
  await expect(page.getByText('not an admin account')).toHaveCount(0)
  await expect(page.getByRole('navigation', { name: 'Admin' })).toHaveCount(0)
  await page.getByLabel('Authenticator or recovery code').fill(await nextTotp(secret))
  await page.getByRole('button', { name: 'Verify' }).click()
  await expect(page.getByRole('heading', { name: 'Admin', exact: true })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Admin' })).toBeVisible()

  const plain = await newUser(browser, baseURL, 'm5-plain', false)
  await plain.page.goto('/admin/users')
  await expect(plain.page.getByText('Your account is not an admin account.')).toBeVisible()
  await expect(plain.page.getByLabel('Authenticator or recovery code')).toHaveCount(0)

  for (const u of [admin, plain]) await u.context.close()
})
