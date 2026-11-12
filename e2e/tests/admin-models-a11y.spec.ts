import { expect, test } from './helpers'
import { enrollTotp, newUser } from './m5-helpers'
import { audit } from './mu-helpers'

// M7a hardening C (#967, ADR 0031 section 8): the admin Model routes screen shows each plan's
// quality word beside its route key, and passes the accessibility audit at 390 and 1440 px in
// both themes (no sideways scroll, the primary action in the window, no serious or critical axe
// violation). Read only: no route is set, so the shared stack is left as it was.
test('the admin Model routes screen lists the plans with their quality and passes the audit', async ({
  browser,
  baseURL,
}) => {
  const admin = await newUser(browser, baseURL, 'models-a11y', false, true)
  const page = admin.page
  await enrollTotp(page)

  await page.goto('/admin/models')
  await expect(page.getByRole('heading', { name: 'Model routes' })).toBeVisible()
  for (const [plan, quality] of [
    ['free', 'Standard'],
    ['instructor', 'Better'],
    ['pro', 'Best'],
  ] as const) {
    await expect(page.getByTestId(`quality-${plan}`)).toHaveText(quality)
  }

  const found = await audit(page, 'Admin model routes', page.getByRole('button', { name: 'Set route' }))
  expect(found.problems).toEqual([])
  await admin.context.close()
})
