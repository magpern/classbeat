import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { PASSWORD, createInvite, expect, passCaptcha, signUpThroughUi, test, uniqueEmail } from './helpers'
import { audit } from './mu-helpers'

// ADR 0025 sections 8 and 11 (#726): the Turnstile check on the sign-in, sign-up and reset-request
// forms against the e2e stack, where web runs the fake verifier and the page shows the stand-in
// checkbox (compose.e2e.yaml). The provider-email form is covered in oauth.spec.ts, which plays the
// provider. Cloudflare is never contacted: the network guard of helpers.ts fails a test that asks
// for any host outside the stack.
//
// A test forces the fake verifier's other answers by rewriting the token of one request: `fake-fail`
// is refused (400 captcha_failed) and `fake-unavailable` is an outage (503 captcha_unavailable).

const FAILED = 'The security check did not pass. Please try again.'
const UNAVAILABLE = 'The security check is not reachable right now. Try again in a minute.'

/** The tokens the page sends to `path`; the next request's token is replaced by `force` when given. */
async function watchTokens(page: Page, path: string, force?: string): Promise<string[]> {
  const sent: string[] = []
  let pending = force
  await page.route(`**${path}`, async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    sent.push(String(body.captcha_token))
    if (pending === undefined) return route.continue()
    const token = pending
    pending = undefined
    return route.continue({ postData: JSON.stringify({ ...body, captcha_token: token }) })
  })
  return sent
}

const box = (page: Page) => page.getByTestId('captcha-fake')

test('sign-in waits for the check, and a refused check is reset and passes the second time', async ({
  page,
}) => {
  const email = uniqueEmail('captcha-signin')
  await signUpThroughUi(page, email)
  await page.context().clearCookies()

  const sent = await watchTokens(page, '/api/auth/login', 'fake-fail')
  await page.goto('/signin')
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  const submit = page.getByRole('button', { name: 'Sign in', exact: true })
  await expect(submit).toBeDisabled()
  await expect(page.getByRole('group', { name: 'Security check' }).getByRole('status')).toHaveText(
    'Checking that you are a person…',
  )
  // The reason is linked to the waiting button.
  const describedBy = await submit.getAttribute('aria-describedby')
  expect(describedBy).toBeTruthy()
  await expect(page.locator(`[id="${describedBy}"]`)).toHaveText('Checking that you are a person…')

  await passCaptcha(page)
  await expect(submit).toBeEnabled()
  await submit.click()
  await expect(page.getByRole('alert')).toHaveText(FAILED)
  // The token is spent: the check is cleared and the form waits for a new one.
  await expect(box(page)).not.toBeChecked()
  await expect(submit).toBeDisabled()

  await passCaptcha(page)
  await submit.click()
  await expect(page.getByTestId('account-email')).toBeVisible()
  expect(sent).toHaveLength(2)
  expect(sent[0]).toMatch(/^fake-pass-[0-9a-f]{24}$/)
  expect(sent[1]).toMatch(/^fake-pass-[0-9a-f]{24}$/)
  expect(sent[1]).not.toBe(sent[0])
})

test('a wrong password spends the token too: the next try needs a new check', async ({ page }) => {
  const email = uniqueEmail('captcha-spent')
  await signUpThroughUi(page, email)
  await page.context().clearCookies()

  const sent = await watchTokens(page, '/api/auth/login')
  await page.goto('/signin')
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByLabel('Password', { exact: true }).fill('not the password at all')
  await passCaptcha(page)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('not right')
  await expect(box(page)).not.toBeChecked()

  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await passCaptcha(page)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByTestId('account-email')).toBeVisible()
  expect(sent).toHaveLength(2)
  expect(new Set(sent).size).toBe(2)
})

test('sign-up shows the unavailable message on an outage, then goes through', async ({ page }) => {
  const email = uniqueEmail('captcha-signup')
  await watchTokens(page, '/api/auth/signup', 'fake-unavailable')
  await page.goto('/signup')
  await page.getByLabel('Invite code').fill(createInvite())
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  const submit = page.getByRole('button', { name: 'Create account' })
  await expect(submit).toBeDisabled()
  await passCaptcha(page)
  await submit.click()
  await expect(page.getByRole('alert')).toHaveText(UNAVAILABLE)
  await expect(box(page)).not.toBeChecked()
  await expect(submit).toBeDisabled()

  await passCaptcha(page)
  await submit.click()
  await expect(page.getByRole('heading', { name: 'Check your inbox' })).toBeVisible()
})

test('the reset request: refused, reset, then sent', async ({ page }) => {
  await watchTokens(page, '/api/auth/password/reset/request', 'fake-fail')
  await page.goto('/reset')
  await page.getByLabel('Email', { exact: true }).fill(uniqueEmail('captcha-reset'))
  const submit = page.getByRole('button', { name: 'Send reset link' })
  await expect(submit).toBeDisabled()
  await passCaptcha(page)
  await submit.click()
  await expect(page.getByRole('alert')).toHaveText(FAILED)
  await expect(box(page)).not.toBeChecked()
  await expect(submit).toBeDisabled()

  await passCaptcha(page)
  await submit.click()
  await expect(page.getByRole('heading', { name: 'Check your inbox' })).toBeVisible()
})

test('the session tells the page to use the stand-in, and the page loads nothing from Cloudflare', async ({
  page,
  request,
}) => {
  const session = (await (await request.get('/api/auth/session')).json()) as { captcha: unknown }
  expect(session.captcha).toEqual({ site_key: null, fake: true })
  const scripts: string[] = []
  page.on('request', (r) => {
    if (r.resourceType() === 'script') scripts.push(new URL(r.url()).origin)
  })
  for (const path of ['/signin', '/signup', '/reset']) {
    await page.goto(path)
    await expect(box(page)).toBeVisible()
  }
  expect(await page.locator('script[src*="challenges.cloudflare.com"]').count()).toBe(0)
  expect(new Set(scripts)).toEqual(new Set([new URL(page.url()).origin]))
})

test('the check is in tab order before the submit button and works at 320 px', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 })
  await page.goto('/reset')
  const email = page.getByLabel('Email', { exact: true })
  await email.focus()
  await page.keyboard.press('Tab')
  await expect(box(page)).toBeFocused()
  await page.keyboard.press('Space')
  await expect(box(page)).toBeChecked()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: 'Send reset link' })).toBeFocused()

  const over = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(over).toBeLessThanOrEqual(0)
  await expect(box(page)).toBeInViewport()
  const result = await new AxeBuilder({ page })
    .include('main')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  expect(result.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([])
})

for (const [path, primary] of [
  ['/signin', 'Sign in'],
  ['/signup', 'Create account'],
  ['/reset', 'Send reset link'],
] as const) {
  test(`${path} with the check passes the page audit`, async ({ page }) => {
    await page.goto(path)
    await expect(box(page)).toBeVisible()
    const findings = await audit(page, path, page.getByRole('button', { name: primary, exact: true }))
    expect(findings.problems).toEqual([])
  })
}
