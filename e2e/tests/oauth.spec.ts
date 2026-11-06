import { type Page } from '@playwright/test'
import { createInvite, expect, linkFromMail, PASSWORD, signUpThroughUi, test, uniqueEmail } from './helpers'
import { audit } from './mu-helpers'
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

/** How many accounts have this test address, as the database superuser. */
function accountsWith(email: string): number {
  if (!/^[\w.+-]+@example\.test$/.test(email)) throw new Error('not a test address')
  return Number(sql(`SELECT count(*) FROM users WHERE email = '${email}'`))
}

test('sign-up with Google and no invite never leaves the page (#617)', async ({ page }) => {
  const email = uniqueEmail('noinvite')
  let providerVisits = 0
  await playProvider(page, 'accounts.google.com', { sub: `g-${email}`, email, emailVerified: true })
  page.on('request', (request) => {
    if (request.url().startsWith('https://accounts.google.com/')) providerVisits += 1
  })
  await page.goto('/signup')
  const invite = page.getByLabel(/Invite code/)
  await page.getByRole('button', { name: 'Continue with Google' }).click()
  await expect(page.getByRole('alert')).toHaveText('Enter your invite code first.')
  await expect(invite).toHaveAttribute('aria-invalid', 'true')
  await expect(invite).toHaveAttribute('required', '')
  await expect(invite).toBeFocused()
  expect(new URL(page.url()).pathname).toBe('/signup')
  expect(providerVisits).toBe(0)
  expect(accountsWith(email)).toBe(0)
  const findings = await audit(page, 'Sign up, invite missing', page.getByRole('button', { name: 'Create account' }))
  expect(findings.problems).toEqual([])
})

test('sign-in with an unknown Google account says no account is linked and creates nothing (#617)', async ({ page }) => {
  const email = uniqueEmail('noaccount')
  await playProvider(page, 'accounts.google.com', { sub: `g-${email}`, email, emailVerified: true })
  await page.goto('/signin')
  await page.getByRole('button', { name: 'Continue with Google' }).click()
  await expect(page.getByRole('alert')).toHaveText(
    'No classbeat account is linked to this Google account. Create an account to join the invite-only beta.',
  )
  expect(new URL(page.url()).pathname).toBe('/signin')
  await expect(page.getByText(/invite code/i)).toHaveCount(0)
  await expect(page.getByLabel(/Invite code/)).toHaveCount(0)
  await expect(page.getByRole('navigation', { name: 'Account' })).toContainText('Sign in')
  expect(accountsWith(email)).toBe(0)
  const findings = await audit(page, 'Sign in, no account', page.getByRole('button', { name: 'Sign in', exact: true }))
  expect(findings.problems).toEqual([])

  // The link goes to sign-up, where a valid invite still starts the Google flow and creates it.
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.getByRole('link', { name: 'Create an account', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Create an account' })).toBeVisible()
  await page.getByLabel(/Invite code/).fill(createInvite())
  await page.getByRole('button', { name: 'Continue with Google' }).click()
  await expect(page.getByRole('button', { name: email })).toBeVisible()
  expect(accountsWith(email)).toBe(1)
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

test('Google re-authentication: refused with a password, accepted without auth_time for a Google-only account (#687)', async ({ page }) => {
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
    return page.getByRole('dialog')
  }

  // With a password the prompt offers the password only, and the server refuses a Google start.
  ageReauth(email)
  let dialog = await changePassword()
  await expect(dialog.getByRole('button', { name: 'Confirm with password' })).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Confirm with Google' })).toHaveCount(0)
  const csrf = (await (await page.request.get('/api/auth/session')).json()) as { csrf_token: string }
  const refused = await page.request.post('/api/auth/oauth/google/start', {
    data: { purpose: 'reauth' },
    headers: { 'X-CSRF-Token': csrf.csrf_token, Origin: new URL(page.url()).origin },
  })
  expect(refused.status()).toBe(403)
  expect(await refused.json()).toEqual({ detail: { reason: 'reauth_use_password' } })

  // A Google-only account (no password): Google's answer counts without any auth_time.
  sql(`UPDATE users SET password_hash = NULL WHERE email = '${email}'`)
  ageReauth(email)
  who.authTime = null
  dialog = await changePassword()
  await expect(dialog.getByRole('button', { name: 'Confirm with password' })).toHaveCount(0)
  await expect(dialog).not.toContainText('Google can confirm it only if')
  await dialog.getByRole('button', { name: 'Confirm with Google' }).click()
  await expect(page.getByText('Thanks, you are confirmed. Repeat the change you were making.')).toBeVisible()
})
