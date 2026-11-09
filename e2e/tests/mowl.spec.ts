import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'

// MOWL connect end to end (ADR 0004 section 4) against the fake MOWL API (e2e/fake-mowl, profile
// `e2e`), which answers as api.mowl.com inside the compose network. Public test values only.
const MOWL_EMAIL = 'rider@example.test'
const MOWL_PASSWORD = 'e2e-fake-mowl-password'

test('connect MOWL, see the status, disconnect', async ({ page }) => {
  const email = uniqueEmail('mowl')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email) // a fresh sign-in counts as recent authentication

  const row = page.getByTestId('connection-mowl')
  await row.getByRole('button', { name: 'Connect MOWL' }).click()
  const dialog = page.getByRole('dialog', { name: 'Connect MOWL' })
  await dialog.getByLabel('MOWL email').fill(MOWL_EMAIL)
  await dialog.getByLabel('MOWL password').fill('not-the-password')
  await dialog.getByRole('button', { name: 'Connect MOWL' }).click()
  await expect(dialog.getByRole('alert')).toHaveText('MOWL did not accept that email and password.')
  await expect(dialog.getByLabel('MOWL password')).toHaveValue('')

  await dialog.getByLabel('MOWL password').fill(MOWL_PASSWORD)
  const connected = page.waitForResponse((r) => r.url().endsWith('/api/connections/mowl'))
  await dialog.getByRole('button', { name: 'Connect MOWL' }).click()
  const answer = await connected
  expect(answer.status()).toBe(200)
  expect(new URL(answer.url()).search).toBe('') // nothing in the URL
  expect(await answer.text()).not.toContain(MOWL_PASSWORD)
  await expect(page.getByText('MOWL is connected.')).toBeVisible()
  await expect(row.getByTestId('mowl-status')).toHaveText('Connected')
  await expect(page.getByLabel('MOWL password')).toHaveCount(0) // the dialog closed

  // The status survives a reload: it comes from the server.
  await page.reload()
  await expect(row.getByTestId('mowl-status')).toHaveText('Connected')

  await row.getByRole('button', { name: 'Disconnect MOWL' }).click()
  const confirm = page.getByRole('dialog', { name: 'Disconnect MOWL?' })
  await expect(confirm.getByText(/two copies/)).toBeVisible() // the warning of ADR 0006 section 3
  await confirm.getByRole('button', { name: 'Yes, disconnect MOWL' }).click()
  await expect(page.getByText(/MOWL is disconnected/)).toBeVisible()
  await expect(row.getByRole('button', { name: 'Connect MOWL' })).toBeVisible()
  await expect(row.getByTestId('mowl-status')).toHaveCount(0)
})
