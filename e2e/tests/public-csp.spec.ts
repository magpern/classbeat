import { expect, test } from '@playwright/test'
import { watchCsp } from './passkey-helpers'
import { CODES } from './lang-url-helpers'
import { LISTED } from './public-paths'

// ADR 0030 section 7: the prerendered body is served under the page's real CSP (no inline styles, no
// inline script), and the client takes over without the browser refusing anything. One check on every
// public URL in every language, published and draft.
for (const code of CODES) {
  for (const path of LISTED) {
    const url = `/${code}${path}`
    test(`${url} raises no CSP violation, before or after takeover`, async ({ page }) => {
      const violations = await watchCsp(page)
      await page.goto(url)
      await expect(page.getByRole('main')).toBeVisible()
      await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1)
      // Let the takeover and its requests finish before reading what was refused.
      await page.waitForLoadState('networkidle')
      expect(await violations(), url).toEqual([])
    })
  }
}
