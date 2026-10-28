import { PASSWORD, createInvite, expect, test, linkFromMail, signInThroughUi, signUpThroughUi, uniqueEmail } from './helpers'

// The pages of #40 against the compose stack: sign up with an invite, confirm by the mailed
// link, sign in, sign out, change the password, and reset it by mail.

test('sign up with an invite, confirm by mail, sign in and out', async ({ page }) => {
  const email = uniqueEmail('signup')
  await signUpThroughUi(page, email)

  // The mail link never signs the browser in, and its token is gone from the address bar.
  await expect(page).not.toHaveURL(/token/)
  await expect(page.getByRole('navigation', { name: 'Account' })).toContainText('Sign in')

  await signInThroughUi(page, email)
  await expect(page.getByRole('navigation', { name: 'Account' })).toContainText(email)

  await page.getByRole('button', { name: 'Sign out', exact: true }).first().click()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await page.goto('/account')
  await expect(page.getByRole('heading', { name: 'Sign in first' })).toBeVisible()
})

test('a wrong password on the confirmation page does not create the account', async ({ page }) => {
  const email = uniqueEmail('wrongpw')
  await page.goto('/signup')
  await page.getByLabel('Invite code').fill(createInvite())
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: 'Create account' }).click()
  await page.goto(await linkFromMail(page.request, email, 'Confirm your classbeat account'))
  await page.getByLabel('Password', { exact: true }).fill('not the chosen one')
  await page.getByRole('button', { name: 'Confirm account' }).click()
  await expect(page.getByRole('alert')).toContainText('not the password you chose')
  // the link is still good: the right password finishes it
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: 'Confirm account' }).click()
  await expect(page.getByRole('heading', { name: 'Account created' })).toBeVisible()
})

test('a bad invite is refused with its own message', async ({ page }) => {
  await page.goto('/signup')
  await page.getByLabel('Invite code').fill('definitely-not-an-invite')
  await page.getByLabel('Email', { exact: true }).fill(uniqueEmail('badinvite'))
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByRole('alert')).toContainText('invite code is not valid')
})

test('forgot password: request, mailed link, new password, old one stops working', async ({ page }) => {
  const email = uniqueEmail('reset')
  const next = 'a brand new passphrase!'
  await signUpThroughUi(page, email)

  await page.goto('/reset')
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByRole('button', { name: 'Send reset link' }).click()
  await expect(page.getByRole('heading', { name: 'Check your inbox' })).toBeVisible()

  await page.goto(await linkFromMail(page.request, email, 'Reset your classbeat password'))
  await expect(page).not.toHaveURL(/token/)
  await page.getByLabel('New password', { exact: true }).fill(next)
  await page.getByRole('button', { name: 'Change password' }).click()
  await expect(page.getByRole('heading', { name: 'Password changed' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Account' })).toContainText('Sign in')

  await page.goto('/signin')
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('not right')

  await signInThroughUi(page, email, next)
})

test('change the password on the account page (the session is recent after sign-in)', async ({ page }) => {
  const email = uniqueEmail('change')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)

  const next = 'another long passphrase 1'
  await page.getByLabel('New password', { exact: true }).fill(next)
  await page.getByLabel('Repeat the new password').fill(next)
  await page.getByRole('button', { name: 'Save password' }).click()
  await expect(page.getByText('Password saved')).toBeVisible()

  await page.getByRole('button', { name: 'Sign out everywhere' }).click()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await signInThroughUi(page, email, next)
})
