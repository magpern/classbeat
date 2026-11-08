import { expect, test, type Page } from '@playwright/test'
import { CODES, WORDS, whereWithHash } from './lang-url-helpers'

// #719 (ADR 0027 section 3): addresses without a language are a permanent contract (mail already sent
// uses them). They redirect once, in the client, to the best language, and keep the query and the
// fragment, which is where a mail token lives and never reaches the server.

const TOKEN = 'old-mail-token_1234567890abcdef'

// What each page shows only when it received a token, and never while it is still loading. The
// selectors use no words, so one table serves every language. With the made-up token the server
// says `/reset`'s link is not valid: that card links to a new request, whereas without a token the
// page asks for an email address.
const TOKEN_STATE: Record<string, (page: Page, code: string) => ReturnType<Page['locator']>> = {
  // the choose-password form, or the invalid-link card once the check answered; never the email form
  '/reset': (page, code) => page.locator(`main input[type="password"], main a[href="/${code}/reset"]`),
  // the confirm view: the button that spends the token (a link without a token has none)
  '/unsubscribe': (page) => page.locator('main button.button'),
  // signed out with a token: the sign-in step (without a token there is only an error notice)
  '/email-change': (page, code) => page.locator(`main a.button[href="/${code}/signin"]`),
  // the form that sets the password (without a token only a link to sign-up)
  '/signup/confirm': (page) => page.locator('main input[type="password"]'),
}

for (const code of CODES) {
  test.describe(`browser in ${WORDS[code].name}`, () => {
    test.use({ locale: code })

    // The mail-link pages read the token from the fragment and remove it from the address bar on
    // load (`useMailToken`, so it cannot leak through history or sharing): the address ends without
    // it. That the token went through the redirect is seen in what the page shows, which is not what
    // the same page shows without a token.
    for (const path of ['/reset', '/unsubscribe', '/email-change', '/signup/confirm']) {
      test(`${path}#token=... goes to /${code}${path} and the page receives the token`, async ({ page }) => {
        await page.goto(`/${code}${path}`)
        await expect(page.locator('html')).toHaveAttribute('lang', code)
        const main = page.getByRole('main')
        await expect(main).not.toBeEmpty()
        const withoutToken = await main.innerText()

        await page.goto(`${path}#token=${TOKEN}`)
        await expect(page).toHaveURL(new RegExp(`/${code}${path}$`))
        expect(whereWithHash(page)).toBe(`/${code}${path}`)
        await expect(page.locator('html')).toHaveAttribute('lang', code)
        await expect.poll(async () => main.innerText()).not.toBe(withoutToken)
        // A dropped token must not pass on a transient state: the page is in its token state.
        await expect(TOKEN_STATE[path]!(page, code).first()).toBeVisible()
        if (path === '/reset') await expect(page.locator('main input[type="email"]')).toHaveCount(0)
      })
    }

    test('the fragment of an unprefixed page is kept by the redirect', async ({ page }) => {
      await page.goto('/privacy#cookies')
      await expect(page).toHaveURL(new RegExp(`/${code}/privacy#cookies$`))
    })

    test('the query and a plain page are kept too, and nothing is doubled', async ({ page }) => {
      await page.goto('/privacy')
      await expect(page).toHaveURL(new RegExp(`/${code}/privacy$`))
      await page.goto('/signin?return_to=%2Fclasses')
      await expect(page).toHaveURL(new RegExp(`/${code}/signin\\?return_to=%2Fclasses$`))
      // an address that already has its language is not redirected again
      await page.goto(`/${code}/privacy`)
      await expect(page).toHaveURL(new RegExp(`/${code}/privacy$`))
    })
  })
}

test('the admin area has no language and is not redirected into one', async ({ page }) => {
  await page.goto('/admin')
  await expect(page).toHaveURL(/\/admin$/)
  await page.goto('/en/admin')
  await expect(page).toHaveURL(/\/admin$/)
})
