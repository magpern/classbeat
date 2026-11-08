import { expect, signInThroughUi, test } from './helpers'
import {
  addAuthenticator,
  addPasskey,
  openResetLink,
  openSecurityTab,
  passkeyLabels,
  passkeyUse,
  removeAuthenticator,
  signOut,
  userOnSecurityTab,
} from './passkey-helpers'

// ADR 0019 section 11: a password reset shows the user's passkeys and removes the ticked ones; the
// others stay. The virtual authenticator holds one passkey per user, so the second is made on a
// second authenticator.
test.use(passkeyUse)

test('reset keeps the passkeys that are not ticked and removes the ticked ones', async ({ page }) => {
  const first = await addAuthenticator(page)
  const email = await userOnSecurityTab(page, 'pk-reset')
  await addPasskey(page, 'Keep me')
  await removeAuthenticator(first)
  await addAuthenticator(page)
  await addPasskey(page, 'Drop me')
  expect(passkeyLabels(email)).toEqual(['Drop me', 'Keep me'])
  await signOut(page, email)

  await openResetLink(page, email)
  // a ticked box means "remove this one"
  await page.getByRole('checkbox', { name: 'Remove Drop me' }).check()
  await page.getByLabel('New password', { exact: true }).fill('a brand new passphrase!')
  await page.getByRole('button', { name: 'Change password' }).click()
  await expect(page.getByRole('heading', { name: 'Password changed' })).toBeVisible()

  expect(passkeyLabels(email)).toEqual(['Keep me'])

  await signInThroughUi(page, email, 'a brand new passphrase!')
  await openSecurityTab(page)
  await expect(page.getByRole('button', { name: 'Remove Keep me' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Remove Drop me' })).toHaveCount(0)
})
