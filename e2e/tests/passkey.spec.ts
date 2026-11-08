import { expect, signInThroughUi, test } from './helpers'
import {
  addAuthenticator,
  addPasskey,
  ageReauth,
  heldCredentials,
  openSecurityTab,
  passkeyLabels,
  passkeyUse,
  secondUser,
  signInWithPasskey,
  signOut,
  userOnSecurityTab,
} from './passkey-helpers'

// ADR 0019 section 11: a platform passkey from a Chromium virtual authenticator, against
// `web-passkeys` on localhost (compose.e2e.yaml). Register, sign in username-less, re-authenticate,
// rename and remove, a removed passkey, and two users.
test.use(passkeyUse)

test('register a passkey, sign out and sign in with it, no password typed', async ({ page }) => {
  const authenticator = await addAuthenticator(page)
  const email = await userOnSecurityTab(page, 'pk-register')

  await expect(page.getByText('You have no passkeys yet.')).toBeVisible()
  await addPasskey(page, 'Work laptop')
  expect(await heldCredentials(authenticator)).toBe(1)
  expect(passkeyLabels(email)).toEqual(['Work laptop'])
  await expect(page.getByText('This device only')).toBeVisible()

  await signOut(page, email)
  await signInWithPasskey(page)
  await expect(page.getByRole('button', { name: email })).toBeVisible()
  // a platform passkey is the user's own device: no shared-computer notice
  await expect(page.getByText(/Signed in with your phone or security key/)).toHaveCount(0)

  await openSecurityTab(page)
  await expect(page.getByText(/Last used/).first()).toBeVisible()
})

test('re-authentication with the passkey, then rename and remove', async ({ page }) => {
  await addAuthenticator(page)
  const email = await userOnSecurityTab(page, 'pk-reauth')
  await addPasskey(page, 'Desk')

  // The session is no longer recent: removing asks who it is, and the passkey answers (no password).
  ageReauth(email)
  await page.getByRole('button', { name: 'Remove Desk' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading', { name: 'Confirm it is you' })).toBeVisible()
  await dialog.getByRole('button', { name: 'Confirm with a passkey' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByText('You have no passkeys yet.')).toBeVisible()
  expect(passkeyLabels(email)).toEqual([])
})

test('rename a passkey', async ({ page }) => {
  await addAuthenticator(page)
  const email = await userOnSecurityTab(page, 'pk-rename')
  await addPasskey(page, 'Old name')

  await page.getByRole('button', { name: 'Rename Old name' }).click()
  await page.getByLabel('Name', { exact: true }).fill('New name')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Remove New name' })).toBeVisible()
  expect(passkeyLabels(email)).toEqual(['New name'])
})

test('a removed passkey no longer signs in, and the password still does', async ({ page }) => {
  const authenticator = await addAuthenticator(page)
  const email = await userOnSecurityTab(page, 'pk-removed')
  await addPasskey(page, 'Short lived')
  await page.getByRole('button', { name: 'Remove Short lived' }).click()
  await expect(page.getByText('You have no passkeys yet.')).toBeVisible()
  // the device still holds its half; the server no longer knows it
  expect(await heldCredentials(authenticator)).toBe(1)

  await signOut(page, email)
  await page.goto('/signin')
  const button = page.getByRole('button', { name: 'Sign in with a passkey' })
  await expect(button).toBeVisible()
  // The autofill ceremony may be running as well and can cancel the first click's ceremony, so the
  // click is repeated until the refusal shows (the button is disabled while a ceremony runs).
  await expect(async () => {
    await button.click({ timeout: 2_000 })
    await expect(page.getByRole('alert')).toBeVisible({ timeout: 3_000 })
  }).toPass({ timeout: 20_000 })
  await expect(page.getByRole('heading', { name: 'Your account' })).toHaveCount(0)

  await signInThroughUi(page, email)
})

test('two users each sign in with their own passkey', async ({ page, browser }) => {
  await addAuthenticator(page)
  const first = await userOnSecurityTab(page, 'pk-two-a')
  await addPasskey(page, 'First phone')
  await signOut(page, first)

  const other = await secondUser(browser)
  try {
    const second = await userOnSecurityTab(other.page, 'pk-two-b')
    await addPasskey(other.page, 'Second phone')
    await signOut(other.page, second)

    await signInWithPasskey(page)
    await expect(page.getByRole('button', { name: first })).toBeVisible()
    await signInWithPasskey(other.page)
    await expect(other.page.getByRole('button', { name: second })).toBeVisible()

    // each account lists only its own passkey
    await openSecurityTab(other.page)
    await expect(other.page.getByRole('button', { name: 'Remove Second phone' })).toBeVisible()
    await expect(other.page.getByRole('button', { name: 'Remove First phone' })).toHaveCount(0)
  } finally {
    await other.context.close()
  }
})
