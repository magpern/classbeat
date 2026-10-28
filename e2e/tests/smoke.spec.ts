import { expect, test } from '@playwright/test'

test('landing page loads and the API is reachable', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('banner').getByText('classbeat', { exact: true })).toBeVisible()
  await expect(page.getByRole('status')).toHaveText('API: ok')
})
