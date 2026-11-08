import {
  expect,
  randomClientIp,
  signUpThroughUi,
  test,
  uniqueEmail,
} from './helpers'
import { enrollTotp, newUser, stepUp } from './m5-helpers'
import { countMails, requestReset, waitForMail } from './mail-helpers'

// ME2 (#653, ADR 0022 sections 2 and 6) on the compose stack, the plan's verification list: an
// admin sets the branding of every mail, switches an optional kind off, previews a kind and sends
// "a test to me", and cannot switch an essential kind off. Mail is read from mailpit; nothing
// leaves the stack. The branding and the switch are put back at the end (one shared stack).
test.setTimeout(300_000)

const SENDER = 'E2E Studio'
const FOOTER = 'Sent by the E2E studio.'
const WELCOME = 'Welcome to classbeat'
const TITLES = { mail_sender_name: 'Sender name', mail_support_email: 'Support address', mail_footer: 'Footer' }

test('an admin sets the branding, previews and tests a mail, and essential kinds stay on', async ({
  browser,
  baseURL,
}) => {
  const admin = await newUser(browser, baseURL, 'mail-admin', false, true)
  const page = admin.page
  const secret = await enrollTotp(page)
  const support = `support-${Date.now()}@example.test`

  // Control: before any switch, the sign-up sent the admin a welcome.
  await waitForMail(page.request, admin.email, WELCOME)

  await page.goto('/admin/mail')
  await expect(page.getByRole('heading', { name: 'Mail', exact: true })).toBeVisible()

  // Branding: sender name, support address and footer, each saved on its own (a write each).
  for (const [title, key, value] of [
    ['sender name', 'mail_sender_name', SENDER],
    ['support address', 'mail_support_email', support],
    ['footer', 'mail_footer', FOOTER],
  ] as const) {
    const form = page.getByRole('form', { name: `Change the ${title}` })
    await form.getByRole('textbox').fill(value)
    await form.getByRole('button', { name: 'Save' }).click()
    await stepUp(page, secret)
    await expect(page.getByTestId(`mail-${key}`)).toContainText(value)
    await expect(page.getByTestId(`mail-${key}`)).toContainText('set here')
  }

  // Preview: the live branding is in the HTML (a sandboxed frame) and in the text part, whatever
  // the switch says, under the preview's own policy.
  await page.getByLabel('Mail to preview').selectOption('welcome')
  await expect(page.frameLocator('iframe[title="Mail preview"]').locator('body')).toContainText(FOOTER)
  const text = await page.request.get('/api/admin/mail/welcome/preview?part=text')
  expect(text.status()).toBe(200)
  expect(await text.text()).toContain(FOOTER)
  expect(text.headers()['content-security-policy']).toContain("default-src 'none'")
  expect(text.headers()['cache-control']).toBe('no-store')

  // "Send test to me": the mail reaches the admin's own address, marked [Test], from the
  // branded sender, replies to the support address, with the footer.
  await page.getByRole('button', { name: 'Send test to me' }).click()
  await stepUp(page, secret)
  await expect(page.getByText('Sent to your own address.')).toBeVisible()
  const sent = await waitForMail(page.request, admin.email, `[Test] ${WELCOME}`)
  expect(sent.from.name).toBe(SENDER)
  expect(sent.replyTo).toEqual([support])
  expect(sent.text).toContain(FOOTER)
  expect(sent.html).toContain(FOOTER)

  // Essential kinds have a locked switch; the server refuses to store one as switched off.
  const kinds = (await (await page.request.get('/api/admin/mail')).json()) as {
    kinds: { id: string; essential: boolean; admin_can_disable: boolean }[]
  }
  const essential = kinds.kinds.filter((k) => k.essential)
  expect(essential.length).toBeGreaterThan(0)
  for (const kind of essential) {
    expect(kind.admin_can_disable).toBe(false)
    const lock = page.getByRole('switch', { name: kind.id.replaceAll('_', ' '), exact: true })
    await expect(lock).toBeChecked()
    await expect(lock).toBeDisabled()
  }
  await expect(page.getByText('Always sent').first()).toBeVisible()
  const session = (await (await page.request.get('/api/auth/session')).json()) as { csrf_token: string }
  const refused = await page.request.put('/api/admin/settings/mail_kinds_disabled', {
    headers: { Origin: new URL(page.url()).origin, 'X-CSRF-Token': session.csrf_token },
    data: { value: [essential[0]!.id] },
  })
  expect(refused.status()).toBe(422)
  expect(JSON.stringify(await refused.json())).toContain('essential_kind')

  // An optional kind can be switched off: a new account then gets no welcome, while the
  // mail sent after it (a password reset, essential) arrives, so "no welcome" is not just early.
  const welcomeSwitch = page.getByRole('switch', { name: 'welcome', exact: true })
  await expect(welcomeSwitch).toBeChecked()
  await welcomeSwitch.click()
  await stepUp(page, secret)
  await expect(welcomeSwitch).not.toBeChecked()

  const otherEmail = uniqueEmail('mail-admin-off')
  const other = await browser.newContext({ baseURL, extraHTTPHeaders: { 'X-Forwarded-For': randomClientIp() } })
  const otherPage = await other.newPage()
  await signUpThroughUi(otherPage, otherEmail)
  await requestReset(browser, baseURL, otherEmail)
  await waitForMail(otherPage.request, otherEmail, 'Reset your classbeat password')
  expect(await countMails(otherPage.request, otherEmail, WELCOME)).toBe(0)
  await other.close()

  // The test send ignores the switch (the admin can still see what the mail looks like).
  await page.getByLabel('Mail to preview').selectOption('welcome')
  await page.getByRole('button', { name: 'Send test to me' }).click()
  await stepUp(page, secret)
  await expect(page.getByText('Sent to your own address.')).toBeVisible()
  await expect.poll(() => countMails(page.request, admin.email, `[Test] ${WELCOME}`)).toBe(2)

  // Put everything back: the kind on, the branding to the defaults.
  await welcomeSwitch.click()
  await stepUp(page, secret)
  await expect(welcomeSwitch).toBeChecked()
  for (const key of ['mail_sender_name', 'mail_support_email', 'mail_footer'] as const) {
    const card = page.getByRole('region', { name: TITLES[key], exact: true })
    await card.getByRole('button', { name: 'Use the default' }).click()
    await stepUp(page, secret)
    await expect(page.getByTestId(`mail-${key}`)).toContainText('default')
  }
})
