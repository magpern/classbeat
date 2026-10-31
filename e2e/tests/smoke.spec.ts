import { expect, test } from '@playwright/test'

test('landing page loads and the API is reachable', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('banner').getByText('classbeat', { exact: true })).toBeVisible()
  await expect(page.getByRole('status')).toHaveText('API: ok')
})

// ADR 0013 section 9: the web process sends the app's CSP and no-referrer on every response.
const IMG_SRC = "img-src 'self' https://i.scdn.co"

test('the SPA and the API carry the CSP and Referrer-Policy headers', async ({ page, request }) => {
  const violations: string[] = []
  page.on('console', (message) => {
    if (message.text().includes('Content Security Policy')) violations.push(message.text())
  })
  const response = await page.goto('/')
  expect(response).not.toBeNull()
  const headers = response!.headers()
  expect(headers['content-security-policy']).toContain(IMG_SRC)
  expect(headers['content-security-policy']).toContain("frame-ancestors 'none'")
  expect(headers['content-security-policy']).toContain("script-src 'self'")
  expect(headers['content-security-policy']).not.toContain('unsafe')
  expect(headers['referrer-policy']).toBe('no-referrer')
  await expect(page.getByRole('status')).toHaveText('API: ok')
  // The page, its script, styles, fonts and the API call all load under the policy.
  expect(violations).toEqual([])

  for (const path of ['/api/health', '/classes/does-not-matter']) {
    const answer = await request.get(path)
    expect(answer.headers()['content-security-policy'], path).toContain(IMG_SRC)
    expect(answer.headers()['referrer-policy'], path).toBe('no-referrer')
  }
})
