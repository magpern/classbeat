import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { expect, signUpThroughUi, test, uniqueEmail } from './helpers'

// M7a-5 (#950): the Pricing page (a draft, so it is prerendered but not listed) and Account, Plan,
// at 1440 and 390 px with axe's WCAG 2.2 AA rules and no sideways scroll. A signed-in account that
// is not a tester sees the plans with "Plans are not open yet" and no price.
test.setTimeout(120_000)

const SIZES = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'phone', width: 390, height: 844 },
]

async function checkAxe(page: Page, label: string): Promise<void> {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const result = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  const serious = result.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
  expect(serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`), label).toEqual([])
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow, `${label} scrolls sideways`).toBeLessThanOrEqual(0)
}

for (const size of SIZES) {
  test.describe(`${size.name} (${size.width} px)`, () => {
    test.use({ viewport: { width: size.width, height: size.height } })

    test.beforeEach(async ({ page }) => {
      await page.addInitScript(() => {
        try {
          window.localStorage.setItem('cb-cookie-notice', '1')
        } catch {
          // storage can be blocked; the notice then shows
        }
      })
    })

    test('the Pricing page has no price and no serious axe violation', async ({ page }) => {
      await page.goto('/en/pricing')
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
      // The numbers arrive from the API after takeover; the words are there from the start.
      await expect(page.getByTestId('pricing-instructor')).toBeVisible()
      await expect(page.getByTestId('pricing-instructor').getByText('75 minutes')).toBeVisible()
      await expect(page.getByRole('main')).not.toContainText(/[$€£]|\bUSD\b|\bSEK\b/)
      await checkAxe(page, `/en/pricing at ${size.width}`)
    })

    test('Account, Plan shows what each plan includes and no price to a non-tester', async ({ page }) => {
      await signUpThroughUi(page, uniqueEmail('plans'))
      await page.goto('/account#plan')
      const options = page.getByTestId('plan-options')
      await expect(options).toBeVisible()
      await expect(options.getByText('Plans are not open yet. Here is what each one includes.')).toBeVisible()
      await expect(options).not.toContainText(/[$€£]|\bUSD\b/)
      await expect(options.getByRole('button')).toHaveCount(0)
      await checkAxe(page, `/account#plan at ${size.width}`)
    })
  })
}
