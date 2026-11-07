import { expect, test } from '@playwright/test'
import { auditPage, expectValidLang } from './locale-helpers'

// Issue #718: the privacy notice and the terms are public pages in en, sv and da. A browser with no
// session opens them by address, they read in its language, and axe finds nothing at 390 px and
// 1440 px (the audit helper checks both, dark and light). The words are the catalogue's own.
test.setTimeout(120_000)

const LANGUAGES = [
  { code: 'en', locale: 'en-US', privacy: 'Privacy notice', terms: 'Terms of use', footer: 'Legal' },
  {
    code: 'sv',
    locale: 'sv-SE',
    privacy: 'Integritetspolicy',
    terms: 'Användarvillkor',
    footer: 'Juridisk information',
  },
  {
    code: 'da',
    locale: 'da-DK',
    privacy: 'Privatlivspolitik',
    terms: 'Brugsvilkår',
    footer: 'Juridisk information',
  },
] as const

for (const language of LANGUAGES) {
  test.describe(`${language.code}`, () => {
    test.use({ locale: language.locale })

    for (const [path, title] of [
      ['/privacy', language.privacy],
      ['/terms', language.terms],
    ] as const) {
      test(`${path} opens signed out and passes axe`, async ({ page }) => {
        await page.goto(path)
        const heading = page.getByRole('heading', { level: 1, name: title })
        await expect(heading).toBeVisible()
        await expectValidLang(page, language.code)
        await expect(page.getByRole('navigation', { name: language.footer })).toBeVisible()
        await auditPage(page, `${language.code} ${path}`, heading)
      })
    }
  })
}

test('the sign-in page links to both pages', async ({ page }) => {
  await page.goto('/signin')
  await page.getByRole('link', { name: 'privacy notice' }).click()
  await expect(page).toHaveURL(/\/privacy$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Privacy notice' })).toBeVisible()
  await page.getByRole('navigation', { name: 'Legal' }).getByRole('link', { name: 'Terms' }).click()
  await expect(page).toHaveURL(/\/terms$/)
})
