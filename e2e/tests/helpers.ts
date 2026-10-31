import { execFileSync } from 'node:child_process'
import { expect, test as base, type APIRequestContext, type Page } from '@playwright/test'

// Shared by the UI specs. Mail is read from mailpit's API; nothing leaves the stack.

export const mailpit = `http://127.0.0.1:${process.env.MAILPIT_PORT ?? '8025'}`
export const PASSWORD = 'correct horse battery staple'

export function uniqueEmail(tag: string): string {
  return `${tag}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.test`
}

/** A fresh single-use invite code from the operator CLI (ADR 0003 section 7). */
export function createInvite(): string {
  return execFileSync('docker', ['compose', 'exec', '-T', 'web', 'python', '-m', 'classbeat', 'invite', 'create'], {
    cwd: new URL('../..', import.meta.url),
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'ignore'],
  }).trim()
}

/** The link in the newest mail with this subject to this address (waits for it to arrive). */
export async function linkFromMail(request: APIRequestContext, to: string, subject: string): Promise<string> {
  for (let attempt = 0; attempt < 40; attempt++) {
    const list = (await (await request.get(`${mailpit}/api/v1/messages`)).json()) as {
      messages: { ID: string; Subject: string; To: { Address: string }[] }[]
    }
    const found = list.messages.find((m) => m.Subject.includes(subject) && m.To.some((t) => t.Address === to))
    if (found) {
      const message = (await (await request.get(`${mailpit}/api/v1/message/${found.ID}`)).json()) as {
        Text: string
      }
      const match = /https?:\/\/\S+#token=[\w-]+/.exec(message.Text)
      expect(match, 'the mail carries a token link').not.toBeNull()
      return match![0]
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error(`no "${subject}" mail for ${to} arrived in mailpit`)
}

/** Sign up with an invite, confirm by the mailed link and type the password again. */
export async function signUpThroughUi(page: Page, email: string, password = PASSWORD): Promise<void> {
  await page.goto('/signup')
  await page.getByLabel('Invite code').fill(createInvite())
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByLabel('Password', { exact: true }).fill(password)
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByRole('heading', { name: 'Check your inbox' })).toBeVisible()

  await page.goto(await linkFromMail(page.request, email, 'Confirm your classbeat account'))
  await page.getByLabel('Password', { exact: true }).fill(password)
  await page.getByRole('button', { name: 'Confirm account' }).click()
  await expect(page.getByRole('heading', { name: 'Account created' })).toBeVisible()
}

export async function signInThroughUi(page: Page, email: string, password = PASSWORD): Promise<void> {
  await page.goto('/signin')
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByLabel('Password', { exact: true }).fill(password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your account' })).toBeVisible()
}

// Sign-ups, mails and sign-in failures are rate limited per client IP (ADR 0003 section 9).
// The e2e stack trusts the proxy range of the docker network (compose.e2e.yaml), so each test
// presents its own address in X-Forwarded-For and the tests never share a limit.
function randomClientIp(): string {
  const octet = () => 1 + Math.floor(Math.random() * 254)
  return `203.0.${octet()}.${octet()}`
}

// The browser may only talk to the stack under test: any other origin is blocked and fails the
// test (the fakes are compose-internal; a page asking for a real host is a bug). Routes a test
// sets on its page (the OAuth provider's) answer first and never reach this guard.
const PIXEL = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
)

export const test = base.extend<{ networkGuard: void }>({
  extraHTTPHeaders: async ({}, use) => {
    await use({ 'X-Forwarded-For': randomClientIp() })
  },
  networkGuard: [
    async ({ context, baseURL }, use) => {
      const allowed = new URL(baseURL ?? 'http://127.0.0.1').origin
      const blocked: string[] = []
      // The one third party the pages may ask for: album cover art from Spotify's image host
      // (CSP img-src, frontend CoverArt). The fake Spotify names such URLs; the answer is a pixel.
      await context.route(
        (url) => url.origin === 'https://i.scdn.co',
        (route) => route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL }),
      )
      await context.route(
        (url) => url.origin !== allowed && url.origin !== 'https://i.scdn.co',
        async (route) => {
          blocked.push(new URL(route.request().url()).origin)
          await route.abort('blockedbyclient')
        },
      )
      await use()
      expect(blocked, 'the browser asked for hosts outside the stack').toEqual([])
    },
    { auto: true },
  ],
})

export { expect }
