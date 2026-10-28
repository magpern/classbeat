import { expect, test } from '@playwright/test'

test('landing page loads and the API is reachable', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.brand__name')).toHaveText('classbeat')
  await expect(page.getByRole('status')).toHaveText('API: ok')
})
