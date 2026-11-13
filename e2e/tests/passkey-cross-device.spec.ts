import { expect, test } from './helpers'
import {
  addAuthenticator,
  addPasskey,
  holdPasskeys,
  passkeyUse,
  sessionSecondsLeft,
  signInWithPasskey,
  signOut,
  userOnSecurityTab,
} from './passkey-helpers'

// ADR 0019 section 11: a sign-in with a roaming (cross-platform) passkey, which is what a phone over
// the QR code looks like to the server, gets a 24 h session and a notice; a platform passkey does not.
// CDP cannot simulate the phone or the hybrid transport itself, so a `usb` authenticator stands in.
test.use(passkeyUse)

const DAY = 86_400
const NOTICE = 'Signed in with your phone or security key; sign out when you are done.'

test('a roaming passkey signs in for at most 24 hours and shows the notice', async ({ page }) => {
  const authenticator = await addAuthenticator(page, 'usb')
  const email = await userOnSecurityTab(page, 'pk-phone')
  await addPasskey(page, 'Security key')
  const giveBack = await holdPasskeys(authenticator)
  await signOut(page, email)
  await giveBack()

  await signInWithPasskey(page)
  await expect(page.getByText(NOTICE)).toBeVisible()
  const left = sessionSecondsLeft(email)
  expect(left).toBeLessThanOrEqual(DAY)
  expect(left).toBeGreaterThan(DAY - 600)

  await holdPasskeys(authenticator)
  await signOut(page, email)
  await expect(page.getByText(NOTICE)).toHaveCount(0)
})

test('a platform passkey keeps the normal session length and shows no notice', async ({ page }) => {
  const authenticator = await addAuthenticator(page, 'internal')
  const email = await userOnSecurityTab(page, 'pk-platform')
  await addPasskey(page, 'This laptop')
  const giveBack = await holdPasskeys(authenticator)
  await signOut(page, email)
  await giveBack()

  await signInWithPasskey(page)
  await expect(page.getByText(NOTICE)).toHaveCount(0)
  expect(sessionSecondsLeft(email)).toBeGreaterThan(2 * DAY)
})
