// `test` from helpers gives each test its own client address: sign-up is limited to 5 per hour per
// address, and the locale specs that run later share the runner's address otherwise.
import { PASSWORD, expect, test } from './helpers'
import { CODES, WORDS, accountThenSignedOut, expectSignedIn, submitSignIn, where } from './lang-url-helpers'

// #719 (ADR 0027 section 4): the page you wanted survives the sign-in round trip. `return_to` is
// language-neutral and the prefix of the language in effect is added when you land, so the way
// back is `/<lang>/classes` and never `/<lang>/<lang>/classes`, in each language and also when the
// language is switched on the sign-in page.
test.describe.configure({ mode: 'serial' })
test.setTimeout(240_000)

for (const code of CODES) {
  const w = WORDS[code]
  test.describe(code, () => {
    test.use({ locale: code })

    test(`a deep link to a signed-in page returns to it after sign-in (${code})`, async ({ page }) => {
      const email = await accountThenSignedOut(page, w)

      await page.goto(`/${code}/classes`)
      await expect(page.getByRole('heading', { name: w.signInFirst })).toBeVisible()
      await page.getByRole('region', { name: w.signInFirst }).getByRole('link', { name: w.signIn, exact: true }).click()
      // The query carries the neutral path; the URL carries the language.
      await expect(page).toHaveURL(new RegExp(`/${code}/signin\\?return_to=%2Fclasses$`))

      await submitSignIn(page, w, email, PASSWORD)
      await expectSignedIn(page, email)
      expect(where(page)).toBe(`/${code}/classes`)
    })
  })
}

test.describe('language switched in the middle of the round trip', () => {
  test.use({ locale: 'en' })

  // ADR 0027 item 3: after a password sign-in the account's saved language wins once, so an
  // English account that picked Swedish on the sign-in page lands on the same page in English.
  test('the page survives, and the account language wins after sign-in', async ({ page }) => {
    const email = await accountThenSignedOut(page, WORDS.en)

    await page.goto('/en/classes')
    await page
      .getByRole('region', { name: WORDS.en.signInFirst })
      .getByRole('link', { name: WORDS.en.signIn, exact: true })
      .click()
    await expect(page).toHaveURL(/\/en\/signin\?return_to=%2Fclasses$/)

    await page.getByLabel(WORDS.en.language).selectOption('sv')
    await expect(page).toHaveURL(/\/sv\/signin\?return_to=%2Fclasses$/)
    await expect(page.locator('html')).toHaveAttribute('lang', 'sv')

    await submitSignIn(page, WORDS.sv, email, PASSWORD)
    await expectSignedIn(page, email)
    expect(where(page)).toBe('/en/classes')
  })
})
