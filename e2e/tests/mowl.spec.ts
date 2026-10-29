import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'

// MOWL connect end to end (ADR 0004 section 4) against the fake MOWL API (e2e/fake-mowl, profile
// `e2e`), which answers as api.mowl.com inside the compose network. Public test values only.
const MOWL_EMAIL = 'rider@example.test'
const MOWL_PASSWORD = 'e2e-fake-mowl-password'

test('connect MOWL, see the status, disconnect', async ({ page }) => {
  const email = uniqueEmail('mowl')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email) // a fresh sign-in counts as recent authentication

  const mowl = page.getByRole('region', { name: 'MOWL (Intelligent Cycling)' })
  await mowl.getByLabel('MOWL email').fill(MOWL_EMAIL)
  await mowl.getByLabel('MOWL password').fill('not-the-password')
  await mowl.getByRole('button', { name: 'Connect MOWL' }).click()
  await expect(mowl.getByRole('alert')).toHaveText('MOWL did not accept that email and password.')
  await expect(mowl.getByLabel('MOWL password')).toHaveValue('')

  await mowl.getByLabel('MOWL password').fill(MOWL_PASSWORD)
  const connected = page.waitForResponse((r) => r.url().endsWith('/api/connections/mowl'))
  await mowl.getByRole('button', { name: 'Connect MOWL' }).click()
  const answer = await connected
  expect(answer.status()).toBe(200)
  expect(new URL(answer.url()).search).toBe('') // nothing in the URL
  expect(await answer.text()).not.toContain(MOWL_PASSWORD)
  await expect(mowl.getByText('MOWL is connected.')).toBeVisible()
  await expect(mowl.getByTestId('mowl-status')).toHaveText('Connected')
  await expect(mowl.getByLabel('MOWL password')).toHaveCount(0)

  // The status survives a reload: it comes from the server.
  await page.reload()
  await expect(mowl.getByTestId('mowl-status')).toHaveText('Connected')

  await mowl.getByRole('button', { name: 'Disconnect MOWL' }).click()
  await expect(mowl.getByText(/second time/)).toBeVisible() // the warning of ADR 0006 section 3
  await mowl.getByRole('button', { name: 'Yes, disconnect MOWL' }).click()
  await expect(mowl.getByText(/MOWL is disconnected/)).toBeVisible()
  await expect(mowl.getByRole('button', { name: 'Connect MOWL' })).toBeVisible()
  await expect(mowl.getByTestId('mowl-status')).toHaveCount(0)
})
