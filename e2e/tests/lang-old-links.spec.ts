import { expect, test } from '@playwright/test'
import { CODES, WORDS, whereWithHash } from './lang-url-helpers'

// #719 (ADR 0027 section 3): addresses without a language are a permanent contract (mail already sent
// uses them). They redirect once, in the client, to the best language, and keep the query and the
// fragment, which is where a mail token lives and never reaches the server.

const TOKEN = 'old-mail-token_1234567890abcdef'

for (const code of CODES) {
  test.describe(`browser in ${WORDS[code].name}`, () => {
    test.use({ locale: code })

    for (const path of ['/reset', '/unsubscribe', '/email-change', '/signup/confirm']) {
      test(`${path}#token=... goes to /${code}${path} and keeps the token`, async ({ page }) => {
        await page.goto(`${path}#token=${TOKEN}`)
        await expect(page).toHaveURL(new RegExp(`^[^#]*/${code}${path}#token=${TOKEN}$`))
        expect(whereWithHash(page)).toBe(`/${code}${path}#token=${TOKEN}`)
        await expect(page.locator('html')).toHaveAttribute('lang', code)
      })
    }

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
