import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { CODES } from './lang-url-helpers'
import { PUBLISHED } from './public-paths'
import { SIZES, THEMES } from './public-takeover-matrix'

// PP8 (#897): every published public page, in every language, at 390 and 1440 px, in both themes,
// with axe's WCAG 2.2 AA rules and no sideways scroll. (`lang-axe.spec.ts` covers 390 px in the
// default theme and the sign-in pages.) The theme is the stored choice, as the takeover gate sets it.
test.describe.configure({ mode: 'parallel' })

for (const code of CODES) {
  for (const theme of THEMES) {
    for (const size of SIZES) {
      test.describe(`${code}, ${theme}, ${size.name} px`, () => {
        test.use({ locale: code, colorScheme: theme, viewport: { width: size.width, height: size.height } })

        for (const page of PUBLISHED) {
          const path = `/${code}${page}`
          test(`${path} has no serious axe violation`, async ({ page: tab, baseURL }) => {
            await tab.addInitScript(
              ([t]) => {
                try {
                  window.localStorage.setItem('cb-theme', t ?? 'dark')
                  window.localStorage.setItem('cb-cookie-notice', '1')
                } catch {
                  // storage can be blocked; the page then follows its default
                }
              },
              [theme],
            )
            await tab.goto(new URL(path, baseURL).toString())
            await expect(tab.locator('html')).toHaveAttribute('lang', code)
            await expect(tab.locator('html')).toHaveAttribute('data-theme', theme)
            await expect(tab.getByRole('main')).toBeVisible()
            // reduced motion shortens the colour transitions, so axe never reads a colour mid-fade
            await tab.emulateMedia({ reducedMotion: 'reduce', colorScheme: theme })

            const result = await new AxeBuilder({ page: tab })
              .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
              .analyze()
            const serious = result.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
            expect(
              serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`),
              path,
            ).toEqual([])

            const overflow = await tab.evaluate(
              () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
            )
            expect(overflow, 'the page scrolls sideways').toBeLessThanOrEqual(0)
          })
        }
      })
    }
  }
}
