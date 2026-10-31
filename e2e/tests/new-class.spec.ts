import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'

// The new-class form against the fake MOWL (e2e/fake-mowl: public test playlists), up to the page
// of the job it starts. The design run itself is design-draft.spec.ts.
const MOWL_EMAIL = 'rider@example.test'
const MOWL_PASSWORD = 'e2e-fake-mowl-password'

test('a user without MOWL is sent to connect it, then starts a design from a playlist', async ({ page }) => {
  const email = uniqueEmail('newclass')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)

  await page.getByRole('link', { name: 'New class' }).click()
  await expect(page.getByRole('link', { name: 'Connect MOWL on your account page' })).toBeVisible()

  await page.goto('/account')
  const mowl = page.getByRole('region', { name: 'MOWL (Intelligent Cycling)' })
  await mowl.getByLabel('MOWL email').fill(MOWL_EMAIL)
  await mowl.getByLabel('MOWL password').fill(MOWL_PASSWORD)
  await mowl.getByRole('button', { name: 'Connect MOWL' }).click()
  await expect(mowl.getByText('MOWL is connected.')).toBeVisible()

  await page.getByRole('link', { name: 'New class' }).click()
  const form = page.getByRole('form', { name: 'New class' })
  await form.getByLabel('Class name').fill('E2E climb')
  // Song picking is off in this stack (CLASSBEAT_SONG_PICKING unset): the song choice is not offered
  // and the form is the playlist form (the pick flow itself is the T3 spec, ADR 0008 section 12).
  await expect(form.getByRole('button', { name: 'Pick songs for me' })).toHaveCount(0)
  await expect(form.getByText(/classbeat adds this playlist to your MOWL music/)).toBeVisible()
  await form.getByRole('radio', { name: 'E2E Hill Climb' }).check({ force: true })
  await form.getByRole('button', { name: 'Design my class' }).click()

  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/)
  await expect(page.getByRole('main').getByRole('heading', { level: 1 })).toBeVisible()
})
