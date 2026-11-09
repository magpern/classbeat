import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import { linkWith, requestReset, waitForMail } from './mail-helpers'

// ME4 (#655, ADR 0022 section 5) on the compose stack, the plan's verification list: the
// one-click header link and the page's buttons from an optional mail, and the per-category and
// all-optional opt-outs in the account preferences. Mail is read from mailpit; nothing leaves the
// stack. The preferences are read back as the user (`GET /api/mail/preferences`).
test.setTimeout(240_000)

const WELCOME = 'Welcome to classbeat'

interface Prefs {
  unsubscribed_all: boolean
  opted_out: string[]
}

test('an optional mail unsubscribes by one click and by its page, and the account page agrees', async ({
  page,
  request,
  baseURL,
  browser,
}) => {
  const email = uniqueEmail('mail-unsub')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  const prefs = async (): Promise<Prefs> => {
    const answer = await page.request.get('/api/mail/preferences')
    expect(answer.status()).toBe(200)
    return (await answer.json()) as Prefs
  }
  expect(await prefs()).toEqual({ unsubscribed_all: false, opted_out: [] })

  // The welcome is an optional mail: it carries the one-click header (RFC 8058) and the page link.
  const welcome = await waitForMail(page.request, email, WELCOME)
  const header = welcome.headers['List-Unsubscribe']?.[0] ?? ''
  expect(header).toMatch(/^<https?:\/\/[^>]+\/api\/mail\/unsubscribe\?token=[\w.-]+>$/)
  expect(welcome.headers['List-Unsubscribe-Post']).toEqual(['List-Unsubscribe=One-Click'])
  const oneClick = new URL(header.slice(1, -1))
  const pageLink = new URL(linkWith(welcome.text, '/unsubscribe#token='))
  expect(pageLink.hash).toMatch(/^#token=[\w.-]+&all=[\w.-]+$/)
  const unsubscribePage = `/unsubscribe${pageLink.hash}`

  // Opening the page, or fetching the one-click URL with GET (a mail scanner), changes nothing.
  await page.goto(unsubscribePage)
  await expect(page.getByRole('button', { name: 'Stop these emails' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Stop optional email' })).toBeVisible()
  await page.request.get(`${oneClick.pathname}${oneClick.search}`)
  expect(await prefs()).toEqual({ unsubscribed_all: false, opted_out: [] })

  // One click: a mail client's POST (no cookie, Origin or CSRF token) turns the kind off; a
  // token that was not issued by us is the same refusal whatever is wrong with it.
  const clicked = await request.post(`${baseURL}${oneClick.pathname}${oneClick.search}`, {
    form: { 'List-Unsubscribe': 'One-Click' },
  })
  expect(clicked.status()).toBe(200)
  expect(await clicked.json()).toEqual({ status: 'ok' })
  expect(await prefs()).toEqual({ unsubscribed_all: false, opted_out: ['welcome'] })
  const forged = await request.post(`${baseURL}${oneClick.pathname}?token=${oneClick.searchParams.get('token')!}x`)
  expect(forged.status()).toBe(400)
  expect(await prefs()).toEqual({ unsubscribed_all: false, opted_out: ['welcome'] })

  // The account page shows it and changes it both ways.
  await page.goto('/account#data')
  const all = page.getByRole('switch', { name: 'Optional email', exact: true })
  const welcomeSwitch = page.getByRole('switch', { name: 'Welcome mail', exact: true })
  const activity = page.getByRole('switch', { name: 'Account activity notices', exact: true })
  await expect(welcomeSwitch).not.toBeChecked()
  await expect(activity).toBeChecked()
  await expect(all).toBeChecked()
  await welcomeSwitch.click()
  await expect.poll(prefs).toEqual({ unsubscribed_all: false, opted_out: [] })
  await activity.click()
  await expect.poll(prefs).toEqual({ unsubscribed_all: false, opted_out: ['account_activity'] })
  await expect(activity).not.toBeChecked()
  await activity.click()
  await expect.poll(prefs).toEqual({ unsubscribed_all: false, opted_out: [] })

  // The page's "Stop these emails" button, from the same mail.
  await page.goto(unsubscribePage)
  await page.getByRole('button', { name: 'Stop these emails' }).click()
  await expect(page.getByRole('heading', { name: 'You are unsubscribed' })).toBeVisible()
  await expect(page.getByText('We will no longer send you this kind of email.')).toBeVisible()
  expect(await prefs()).toEqual({ unsubscribed_all: false, opted_out: ['welcome'] })

  // "Stop optional email": everything optional is off and the account page locks the kinds.
  await page.goto(unsubscribePage)
  await page.getByRole('button', { name: 'Stop optional email' }).click()
  await expect(page.getByText('We will no longer send you optional email.')).toBeVisible()
  expect((await prefs()).unsubscribed_all).toBe(true)
  await page.goto('/account#data')
  await expect(all).not.toBeChecked()
  await expect(welcomeSwitch).toBeDisabled()
  await expect(activity).toBeDisabled()

  // Essential mail still goes out, and it has no unsubscribe link.
  await requestReset(browser, baseURL, email)
  const reset = await waitForMail(page.request, email, 'Reset your classbeat password')
  expect(reset.headers['List-Unsubscribe']).toBeUndefined()
  expect(reset.text).not.toContain('/unsubscribe')

  // Back on: the "all" switch turns optional mail on again and unlocks the kinds.
  await all.click()
  await expect.poll(prefs).toMatchObject({ unsubscribed_all: false })
  await expect(activity).toBeEnabled()
})

test('the unsubscribe page without a link, and with a link that is not ours', async ({ page }) => {
  await page.goto('/unsubscribe')
  await expect(page.getByText('This page needs the link from the mail.')).toBeVisible()

  await page.goto('/unsubscribe#token=forged.token')
  await page.getByRole('button', { name: 'Stop these emails' }).click()
  await expect(page.getByText('This link is no longer valid.', { exact: false })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'You are unsubscribed' })).toHaveCount(0)
})
