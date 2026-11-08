import type { Browser, BrowserContext, CDPSession, Page } from '@playwright/test'
import {
  expect,
  linkFromMail,
  passCaptcha,
  randomClientIp,
  signInThroughUi,
  signUpThroughUi,
  uniqueEmail,
} from './helpers'
import { sql } from './song-helpers'

// Shared by the passkey specs (ADR 0019 section 11). They run against `web-passkeys` (compose.e2e.yaml):
// the same image and database as `web`, on `localhost` (a valid RP ID and a secure context), with
// passkeys on. Every other spec keeps its 127.0.0.1 base URL. A Chromium virtual authenticator
// (the CDP WebAuthn domain) stands in for the user's device; no real authenticator, account or
// network is involved.

export const passkeyBaseURL =
  process.env.E2E_PASSKEY_BASE_URL ?? `http://localhost:${process.env.WEB_PASSKEYS_PORT ?? '8001'}`

/** Context options of a passkey spec: its own base URL, with the cookie notice dismissed there. */
export const passkeyUse = {
  baseURL: passkeyBaseURL,
  storageState: {
    cookies: [],
    origins: [
      { origin: new URL(passkeyBaseURL).origin, localStorage: [{ name: 'cb-cookie-notice', value: '1' }] },
    ],
  },
}

export interface VirtualAuthenticator {
  client: CDPSession
  id: string
}

/**
 * Attaches a virtual CTAP2 authenticator to the page's tab. `internal` is a platform passkey (the
 * browser reports `platform`); `usb` is a roaming one (`cross-platform`), which is what a phone over
 * the QR code looks like to the server (CDP cannot simulate the phone itself).
 */
export async function addAuthenticator(
  page: Page,
  transport: 'internal' | 'usb' = 'internal',
): Promise<VirtualAuthenticator> {
  const client = await page.context().newCDPSession(page)
  await client.send('WebAuthn.enable')
  const { authenticatorId } = await client.send('WebAuthn.addVirtualAuthenticator', {
    options: {
      protocol: 'ctap2',
      transport,
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  })
  return { client, id: authenticatorId }
}

export async function removeAuthenticator(authenticator: VirtualAuthenticator): Promise<void> {
  await authenticator.client.send('WebAuthn.removeVirtualAuthenticator', {
    authenticatorId: authenticator.id,
  })
}

export async function heldCredentials(authenticator: VirtualAuthenticator): Promise<number> {
  const { credentials } = await authenticator.client.send('WebAuthn.getCredentials', {
    authenticatorId: authenticator.id,
  })
  return credentials.length
}

/** A new user with a password, signed in by it, on the security tab of the account page. */
export async function userOnSecurityTab(page: Page, tag: string): Promise<string> {
  const email = uniqueEmail(tag)
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await openSecurityTab(page)
  return email
}

export async function openSecurityTab(page: Page): Promise<void> {
  // The page may still be settling after a sign-in (the session is read again), which can reset the
  // tab, so the click is repeated until the card shows.
  await expect(async () => {
    await page.getByRole('tab', { name: 'Sign-in and security' }).click({ timeout: 2_000 })
    await expect(page.getByRole('heading', { name: 'Passkeys', exact: true })).toBeVisible({ timeout: 2_000 })
  }).toPass({ timeout: 15_000 })
}

/** Adds a passkey from the account page's card and waits for its row. */
export async function addPasskey(page: Page, label: string): Promise<void> {
  await page.getByLabel('Name (optional)').fill(label)
  await page.getByRole('button', { name: 'Add a passkey' }).click()
  await expect(page.getByRole('button', { name: `Remove ${label}` })).toBeVisible()
}

export async function signOut(page: Page, email: string): Promise<void> {
  await page.getByRole('button', { name: email }).click()
  await page.getByRole('menuitem', { name: 'Sign out' }).click()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
}

/** Username-less sign-in from the sign-in page: the button, or the autofill ceremony if it came first. */
export async function signInWithPasskey(page: Page): Promise<void> {
  await page.goto('/signin')
  const button = page.getByRole('button', { name: 'Sign in with a passkey' })
  await expect(button).toBeVisible()
  await button.click({ timeout: 5_000 }).catch(() => undefined)
  await expect(page.getByRole('heading', { name: 'Your account' })).toBeVisible()
}

/** Another browser context for a second user, with its own virtual authenticator. */
export async function secondUser(
  browser: Browser,
): Promise<{ context: BrowserContext; page: Page; authenticator: VirtualAuthenticator }> {
  const context = await browser.newContext({
    ...passkeyUse,
    locale: 'en',
    extraHTTPHeaders: { 'X-Forwarded-For': randomClientIp() },
  })
  const page = await context.newPage()
  return { context, page, authenticator: await addAuthenticator(page) }
}

/** Requests a reset mail on /reset and opens its link (the page then shows the keep list). */
export async function openResetLink(page: Page, email: string): Promise<void> {
  await page.goto('/reset')
  await page.getByLabel('Email', { exact: true }).fill(email)
  await passCaptcha(page)
  await page.getByRole('button', { name: 'Send reset link' }).click()
  await expect(page.getByRole('heading', { name: 'Check your inbox' })).toBeVisible()
  await page.goto(await linkFromMail(page.request, email, 'Reset your classbeat password'))
  await expect(page.getByLabel('New password', { exact: true })).toBeVisible()
}

function checkedEmail(email: string): string {
  if (!/^[\w.+-]+@example\.test$/.test(email)) throw new Error('not a test address')
  return email
}

/** The user's passkey labels as the database holds them (constants only: never user text). */
export function passkeyLabels(email: string): string[] {
  return sql(
    `SELECT c.label FROM webauthn_credentials c JOIN users u ON u.id = c.user_id WHERE u.email = '${checkedEmail(email)}' ORDER BY c.label`,
  )
    .split('\n')
    .filter((line) => line !== '')
}

/** Makes the user's last sign-in older than the 10-minute window, as the database superuser. */
export function ageReauth(email: string): void {
  sql(
    `UPDATE sessions SET reauth_at = now() - interval '11 minutes' WHERE id = (SELECT s.id FROM sessions s JOIN users u ON u.id = s.user_id WHERE u.email = '${checkedEmail(email)}' ORDER BY s.created_at DESC LIMIT 1)`,
  )
}

/** Seconds until the user's newest session expires. */
export function sessionSecondsLeft(email: string): number {
  return Number(
    sql(
      `SELECT extract(epoch FROM s.expires_at - now()) FROM sessions s JOIN users u ON u.id = s.user_id WHERE u.email = '${checkedEmail(email)}' ORDER BY s.created_at DESC LIMIT 1`,
    ),
  )
}

/**
 * Collects what the real CSP (sent by web as a header) refuses on this page: `securitypolicyviolation`
 * events and the console's "Refused to ..." lines. Call before the first navigation; the returned
 * function lists what has been seen so far (events of an earlier page are gone with that page).
 */
export async function watchCsp(page: Page): Promise<() => Promise<string[]>> {
  const seen: string[] = []
  await page.addInitScript(() => {
    const store: string[] = []
    ;(window as unknown as { __csp: string[] }).__csp = store
    document.addEventListener('securitypolicyviolation', (event) => {
      store.push(`${event.violatedDirective} ${event.blockedURI}`)
    })
  })
  page.on('console', (message) => {
    if (/content security policy|refused to/i.test(message.text())) seen.push(message.text())
  })
  return async () => [
    ...seen,
    ...(await page.evaluate(() => (window as unknown as { __csp?: string[] }).__csp ?? [])),
  ]
}
