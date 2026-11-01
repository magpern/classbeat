import { type Page } from '@playwright/test'
import { createInvite, expect, linkFromMail, test, uniqueEmail } from './helpers'

// OAuth sign-in against the fake provider of compose.e2e.yaml (the `e2e` profile). The fake
// answers the providers' real back-channel hostnames inside the stack; here Playwright plays
// the browser's visit to the provider: it answers the redirect to the authorize URL with a
// redirect back to our callback, and puts who "signed in" into the authorization code.

interface Persona {
  sub: string
  email: string | null
  emailVerified: boolean
}

function code(grant: object): string {
  return Buffer.from(JSON.stringify(grant)).toString('base64url')
}

async function playProvider(page: Page, host: 'accounts.google.com' | 'login.microsoftonline.com', who: Persona) {
  await page.route(`https://${host}/**`, async (route) => {
    const authorize = new URL(route.request().url())
    const grant =
      host === 'accounts.google.com'
        ? { sub: who.sub, email: who.email, email_verified: who.emailVerified, nonce: authorize.searchParams.get('nonce') }
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
