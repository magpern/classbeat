import { type Page } from '@playwright/test'
import { createInvite, expect, linkFromMail, PASSWORD, signUpThroughUi, test, uniqueEmail } from './helpers'
import { sql } from './song-helpers'

// OAuth sign-in against the fake provider of compose.e2e.yaml (the `e2e` profile). The fake
// answers the providers' real back-channel hostnames inside the stack; here Playwright plays
// the browser's visit to the provider: it answers the redirect to the authorize URL with a
// redirect back to our callback, and puts who "signed in" into the authorization code.

interface Persona {
  sub: string
  email: string | null
  emailVerified: boolean
  /** The sign-in's age in seconds for the ID token's auth_time, or null to leave the claim out
   *  (#522); read at each visit, so a test can change it between flows. Absent: now. */
  authTime?: number | null
}

function code(grant: object): string {
  return Buffer.from(JSON.stringify(grant)).toString('base64url')
}

async function playProvider(page: Page, host: 'accounts.google.com' | 'login.microsoftonline.com', who: Persona) {
  await page.route(`https://${host}/**`, async (route) => {
    const authorize = new URL(route.request().url())
    const grant =
      host === 'accounts.google.com'
        ? {
            sub: who.sub,
            email: who.email,
            email_verified: who.emailVerified,
            nonce: authorize.searchParams.get('nonce'),
            ...(who.authTime === undefined ? {} : { auth_time: who.authTime }),
          }
        : {
            tid: '11111111-2222-3333-4444-555555555555',
            oid: who.sub,
            email: who.email,
            nonce: authorize.searchParams.get('nonce'),
          }
    const callback = new URL(authorize.searchParams.get('redirect_uri')!)
    callback.searchParams.set('code', code(grant))
    callback.searchParams.set('state', authorize.searchParams.get('state')!)
    await route.fulfill({ status: 302, headers: { location: callback.href } })
  })
}

test('the stack offers the providers the fake switches on', async ({ request }) => {
  const body = (await (await request.get('/api/auth/oauth/providers')).json()) as { providers: string[] }
  expect(body.providers).toEqual(['google', 'microsoft'])
})

test('the sign-in screen shows Google and the flagged-on Microsoft, never GitHub', async ({ page }) => {
  await page.goto('/signin')
  await expect(page.getByRole('button', { name: 'Continue with Google' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Continue with Microsoft' })).toBeVisible()
  await expect(page.getByRole('button', { name: /GitHub/ })).toHaveCount(0)
  await expect(page.getByText('OR', { exact: true })).toBeVisible()
})

test('sign up and sign in with Google through the fake provider', async ({ page }) => {
  const email = uniqueEmail('google')
  await playProvider(page, 'accounts.google.com', { sub: `g-${email}`, email, emailVerified: true })

  // First sign-in: an invite is needed, and the verified email creates the account.
  await page.goto('/signup')
  await page.getByLabel(/Invite code/).fill(createInvite())
  await page.getByRole('button', { name: 'Continue with Google' }).click()
  await expect(page.getByRole('button', { name: email })).toBeVisible()

  await page.getByRole('button', { name: email }).click()
  await page.getByRole('menuitem', { name: 'Sign out' }).click()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()

  // Second time: the identity exists, no invite.
  await page.getByRole('button', { name: 'Continue with Google' }).click()
  await expect(page.getByRole('button', { name: email })).toBeVisible()

  await page.goto('/account#security')
  await expect(page.getByRole('button', { name: 'Unlink Google' })).toBeVisible()
  // The only sign-in method cannot be unlinked.
  await page.getByRole('button', { name: 'Unlink Google' }).click()
  await expect(page.getByRole('alert')).toContainText('only way to sign in')
})

test('a first Google sign-in without an invite creates nothing and says why', async ({ page }) => {
  const email = uniqueEmail('noinvite')
  await playProvider(page, 'accounts.google.com', { sub: `g-${email}`, email, emailVerified: true })
  await page.goto('/signup')
  await page.getByRole('button', { name: 'Continue with Google' }).click()
  await expect(page.getByRole('alert')).toContainText('need an invite')
  await expect(page.getByRole('navigation', { name: 'Account' })).toContainText('Sign in')
})

test('Microsoft: an unverified email is handed off to the address page and confirmed by mail', async ({ page }) => {
  const email = uniqueEmail('microsoft')
  const oid = `ms-${Date.now()}`
  await playProvider(page, 'login.microsoftonline.com', { sub: oid, email: null, emailVerified: false })

  await page.goto('/signup')
  await page.getByLabel(/Invite code/).fill(createInvite())
  await page.getByRole('button', { name: 'Continue with Microsoft' }).click()

  await expect(page.getByRole('heading', { name: 'Confirm your email address' })).toBeVisible()
  expect(new URL(page.url()).pathname).toBe('/signin/email')
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByRole('button', { name: 'Send confirmation link' }).click()
  await expect(page.getByText(/a second try would not send anything/)).toBeVisible()
  await expect(page.getByRole('link', { name: /Start the provider sign-in again/ })).toBeVisible()

  // The link finishes the account in this browser, and does not sign it in.
  await page.goto(await linkFromMail(page.request, email, 'Confirm your classbeat account'))
  await page.getByRole('button', { name: 'Confirm account' }).click()
  await expect(page.getByRole('heading', { name: 'Account created' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Account' })).toContainText('Sign in')

  // Signing in with Microsoft now finds the identity.
  await page.goto('/signin')
  await page.getByRole('button', { name: 'Continue with Microsoft' }).click()
  await expect(page.getByRole('button', { name: email })).toBeVisible()
})

/** Make the user's last sign-in older than the 10-minute window, as the database superuser. */
function ageReauth(email: string): void {
  if (!/^[\w.+-]+@example\.test$/.test(email)) throw new Error('not a test address')
  sql(`UPDATE sessions SET reauth_at = now() - interval '11 minutes' WHERE user_id = (SELECT id FROM users WHERE email = '${email}')`)
}

test('Google re-authentication: no auth_time fails closed with a sentence, a fresh one passes (#522)', async ({ page }) => {
  const email = uniqueEmail('reauth')
  const who: Persona = { sub: `g-${email}`, email, emailVerified: true }
  await playProvider(page, 'accounts.google.com', who)
  await signUpThroughUi(page, email)
  await page.goto('/signin')
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your account' })).toBeVisible()
  await page.goto('/account#security')
  await page.getByRole('button', { name: 'Link Google' }).click()
  await expect(page.getByText('The sign-in method is linked to your account.')).toBeVisible()

  const changePassword = async () => {
    await page.goto('/account#security')
    await page.getByLabel('New password', { exact: true }).fill('another long passphrase')
    await page.getByLabel('Repeat the new password').fill('another long passphrase')
    await page.getByRole('button', { name: 'Save password' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toContainText('Google can confirm it only if you signed in there in the last 5 minutes.')
    await dialog.getByRole('button', { name: 'Confirm with Google' }).click()
  }

  // Google leaves auth_time out (a fresh iat and this flow's nonce, state and PKCE notwithstanding).
  ageReauth(email)
  who.authTime = null
  await changePassword()
  await expect(page).toHaveURL(/\/account\?oauth=reauth_stale/)
  const alert = page.getByRole('alert').filter({ hasText: 'did not confirm' })
  await expect(alert).toContainText('signed in again in the last 5 minutes, so nothing was changed')
  await expect(alert).toContainText('Use your password instead.')

  // A sign-in at Google an hour ago is not fresh either.
  who.authTime = 3600
  await changePassword()
  await expect(page).toHaveURL(/\/account\?oauth=reauth_stale/)

  // A sign-in at Google just now passes.
  who.authTime = 5
  await changePassword()
  await expect(page.getByText('Thanks, you are confirmed. Repeat the change you were making.')).toBeVisible()
})
