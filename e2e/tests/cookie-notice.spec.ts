import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

// #715: a small, non-modal notice, shown to a first-time visitor and never again once dismissed.
// The other specs start with it dismissed (playwright.config.ts); this one starts clean.
test.use({ storageState: { cookies: [], origins: [] } })

const LANGUAGES = [
  { code: 'en', label: 'Cookie notice', ok: 'OK' },
  { code: 'sv', label: 'Information om kakor', ok: 'OK' },
  { code: 'da', label: 'Information om cookies', ok: 'OK' },
] as const

for (const language of LANGUAGES) {
  test(`cookie notice in ${language.code}: shown, axe clean, dismissed for good`, async ({ page }) => {
    await page.addInitScript((code) => {
      if (!window.localStorage.getItem('cb-locale')) window.localStorage.setItem('cb-locale', code)
    }, language.code)
    for (const viewport of [
      { width: 1280, height: 800 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport)
      await page.goto('/')
      const notice = page.getByRole('region', { name: language.label })
      await expect(notice).toBeVisible()
      const box = await notice.boundingBox()
      expect(box).not.toBeNull()
      expect(box!.x).toBeLessThan(40)
      expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width)
      const axe = await new AxeBuilder({ page }).analyze()
      expect(axe.violations.map((v) => v.id)).toEqual([])
    }
    const cookiesBefore = await page.context().cookies()
    await page.getByRole('button', { name: language.ok }).click()
    await expect(page.getByTestId('cookie-notice')).toHaveCount(0)
    expect(await page.context().cookies()).toEqual(cookiesBefore)
    await page.reload()
    await expect(page.getByRole('banner')).toBeVisible()
    await expect(page.getByTestId('cookie-notice')).toHaveCount(0)
  })
}
