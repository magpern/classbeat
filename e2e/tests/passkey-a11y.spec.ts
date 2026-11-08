import { expect, test } from './helpers'
import { audit } from './mu-helpers'
import {
  addAuthenticator,
  addPasskey,
  ageReauth,
  openResetLink,
  passkeyUse,
  signOut,
  userOnSecurityTab,
  watchCsp,
} from './passkey-helpers'

// Review of W6: the passkey pages pass axe (serious and critical) at both widths and in both themes,
// and run under the real CSP (web sends it as a header) without a single violation.
test.use(passkeyUse)

test('the passkey pages pass axe and break no CSP rule', async ({ page }) => {
  const csp = await watchCsp(page)
  await addAuthenticator(page)
  const problems: string[] = []
  const check = async (name: string, primary: ReturnType<typeof page.locator>, scope?: string) => {
    problems.push(...(await audit(page, name, primary, scope)).problems.map((p) => `${name}: ${p}`))
    problems.push(...(await csp()).map((v) => `${name}: CSP ${v}`))
  }

  const email = await userOnSecurityTab(page, 'pk-a11y')
  await addPasskey(page, 'Work laptop')
  await check('passkeys card', page.getByRole('button', { name: 'Add a passkey' }), 'main')

  // re-authentication: the last sign-in is older than 10 minutes, so Remove asks first
  ageReauth(email)
  await page.getByRole('button', { name: 'Remove Work laptop' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('button', { name: 'Confirm with a passkey' })).toBeVisible()
  await check('re-auth prompt', dialog.getByRole('button', { name: 'Confirm with a passkey' }), '[role="dialog"]')
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(dialog).toHaveCount(0)

  await signOut(page, email)
  await expect(page.getByRole('button', { name: 'Sign in with a passkey' })).toBeVisible()
  await check('sign-in page', page.getByRole('button', { name: 'Sign in with a passkey' }), 'main')

  await openResetLink(page, email)
  await expect(page.getByRole('checkbox', { name: 'Remove Work laptop' })).toBeVisible()
  await check('reset page', page.getByRole('button', { name: 'Change password' }), 'main')

  expect(problems).toEqual([])
})
