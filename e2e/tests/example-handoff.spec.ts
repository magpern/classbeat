import { expect, test, PASSWORD, passCaptcha, signInThroughUi, signUpThroughUi, uniqueEmail } from './helpers'
import { connectMowl } from './design-helpers'
import { sql } from './song-helpers'

// PP6 (#895): "Create a class like this" on an example page opens the designer with the example's
// request filled in, through sign-in, and writes nothing until the instructor submits. The link
// carries a slug, never text. Sign-in is by form here; OAuth and e-mail sign-up are covered by the
// unit tests of the same behaviour (return_to and the 24-hour slug).

test('an example link survives sign-in, fills and focuses the prompt and makes nothing', async ({ page }) => {
  test.setTimeout(180_000)
  const email = uniqueEmail('handoff')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await connectMowl(page)
  const me = (await (await page.request.get('/api/auth/session')).json()) as { user?: { id?: string } }
  const id = me.user?.id ?? ''
  expect(id).toMatch(/^[0-9a-f-]{36}$/)
  const jobs = () => sql(`SELECT count(*) FROM jobs WHERE owner_id = '${id}'`)
  const before = jobs()

  await page.context().clearCookies()
  await page.goto('/en/examples/hill-challenge')
  const shown = (await page.getByTestId('example-prompt').textContent()) ?? ''
  expect(shown.length).toBeGreaterThan(20)
  await page.getByRole('link', { name: 'Create a class like this' }).click()
  await expect(page).toHaveURL(/\/en\/classes\/new\?example=hill-challenge$/)

  // Signed out: sign-in keeps only the slug in return_to.
  await page.getByRole('main').getByRole('link', { name: 'Sign in' }).click()
  await expect(page).toHaveURL(/return_to=%2Fclasses%2Fnew%3Fexample%3Dhill-challenge$/)
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await passCaptcha(page)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()

  const prompt = page.getByLabel('Describe your class')
  await expect(prompt).toHaveValue(shown, { timeout: 30_000 })
  await expect(prompt).toBeFocused()
  expect(jobs()).toBe(before)
})

test('an unknown example slug is ignored', async ({ page }) => {
  const email = uniqueEmail('handoff-bad')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await connectMowl(page)
  await page.goto('/en/classes/new?example=nope&prompt=hello')
  await expect(page.getByLabel('Describe your class')).toHaveValue('')
})
