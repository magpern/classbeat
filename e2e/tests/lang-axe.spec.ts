import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { CODES, WORDS } from './lang-url-helpers'

// #719: the language-prefixed pages at 390 px (a phone), in each language, with axe's WCAG 2.2 AA
// rules, and no sideways scroll. The pages are the ones a visitor and a crawler see before signing
// in; the signed-in pages are audited by the ML7 and MU specs under their own prefix.
test.use({ viewport: { width: 390, height: 844 } })

const PAGES = ['', '/privacy', '/terms', '/signin', '/signup']

for (const code of CODES) {
  test.describe(WORDS[code].name, () => {
    test.use({ locale: code })

    for (const page of PAGES) {
      const path = `/${code}${page}`
      test(`${path} has no serious axe violation at 390 px`, async ({ page: tab }) => {
        await tab.goto(path)
        await expect(tab.locator('html')).toHaveAttribute('lang', code)
        await expect(tab.getByRole('main')).toBeVisible()
        // reduced motion shortens the colour transitions, so axe never reads a colour mid-fade
        await tab.emulateMedia({ reducedMotion: 'reduce' })

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
