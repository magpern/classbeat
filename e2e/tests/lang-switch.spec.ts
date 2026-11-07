import { expect, test } from '@playwright/test'
import { CODES, WORDS, whereWithHash, type Code } from './lang-url-helpers'

// #719 (ADR 0027 section 2): the picker is the one writer of the language, and it goes to the same
// page under the new prefix, keeping the query and the fragment. Signed out here; the picker saves
// `cb-locale` only. Every language switches to every other.

const PAGES = ['/privacy', '/terms', '/signin?return_to=%2Fclasses#top']

for (const from of CODES) {
  for (const to of CODES.filter((code) => code !== from)) {
    test.describe(`${from} to ${to}`, () => {
      test.use({ locale: from })

      for (const path of PAGES) {
        test(`${path} stays the same page`, async ({ page }) => {
          await page.goto(`/${from}${path}`)
          const picker = page.getByLabel(WORDS[from].language)
          await expect(picker).toHaveValue(from)
          await expect(page.locator('html')).toHaveAttribute('lang', from)

          await picker.selectOption(to)

          await expect(page).toHaveURL(new RegExp(`/${to}/`))
          expect(whereWithHash(page)).toBe(`/${to}${path}`)
          await expect(page.locator('html')).toHaveAttribute('lang', to)
          await expect(page.getByLabel(WORDS[to].language)).toHaveValue(to)
        })
      }
    })
  }
}

test.describe('the choice is remembered, the URL still wins', () => {
  test.use({ locale: 'en' })

  test('a later visit to an unprefixed page uses the picked language; a shared prefixed link does not change it', async ({
    page,
  }) => {
    await page.goto('/en/privacy')
    await page.getByLabel(WORDS.en.language).selectOption('da' satisfies Code)
    await expect(page).toHaveURL(/\/da\/privacy$/)

    // the saved choice decides an unprefixed address ...
    await page.goto('/terms')
    await expect(page).toHaveURL(/\/da\/terms$/)

    // ... and a link in another language is shown as it is, without writing anything
    await page.goto('/sv/terms')
    await expect(page.locator('html')).toHaveAttribute('lang', 'sv')
    await page.goto('/terms')
    await expect(page).toHaveURL(/\/da\/terms$/)
  })
})
