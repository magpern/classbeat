import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import { withSpotify } from './spotify-helpers'

// The new-class form against the fake MOWL (e2e/fake-mowl: public test playlists), up to the page
// of the job it starts. The design run itself is design-draft.spec.ts.
const MOWL_EMAIL = 'rider@example.test'
const MOWL_PASSWORD = 'e2e-fake-mowl-password'

test('a user without MOWL is sent to connect it, then starts a design from a playlist', async ({ page }) => {
  const email = uniqueEmail('newclass')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)

  await page.getByRole('link', { name: 'New class' }).click()
  await expect(page.getByRole('link', { name: 'Connect MOWL', exact: true })).toBeVisible()

  await page.goto('/account')
  await page.getByTestId('connection-mowl').getByRole('button', { name: 'Connect MOWL' }).click()
  const mowl = page.getByRole('dialog', { name: 'Connect MOWL' })
  await mowl.getByLabel('MOWL email').fill(MOWL_EMAIL)
  await mowl.getByLabel('MOWL password').fill(MOWL_PASSWORD)
  await mowl.getByRole('button', { name: 'Connect MOWL' }).click()
  await expect(page.getByText('MOWL is connected.')).toBeVisible()

  await page.getByRole('link', { name: 'New class' }).click()
  await page.getByRole('link', { name: /^Advanced/ }).click()
  const form = page.getByRole('form', { name: 'New class' })
  await form.getByLabel('Class name').fill('E2E climb')
  // Song picking is on in this stack (compose.e2e.yaml) but this user has no Spotify: the choice is
  // shown and "Pick for me" cannot be selected, and a class on a playlist needs Spotify first
  // (roadmap Amendment 25): the form says so, links to it and sends nothing (the pick flow itself
  // is song-pick.spec.ts).
  await expect(form.getByRole('radio', { name: 'Pick for me', exact: true })).toBeDisabled()
  await expect(form.getByText(/Added to your MOWL music, private to you\./)).toBeVisible()
  await form.getByRole('radio', { name: 'E2E Hill Climb' }).check({ force: true })
  await expect(form.getByTestId('spotify-prompt')).toHaveAttribute('data-need', 'connect')
  await expect(form.getByRole('link', { name: 'Connect Spotify' })).toBeVisible()
  await expect(form.getByRole('button', { name: 'Design my class' })).toBeDisabled()

  // With Spotify the playlist class starts. The song_picking flag does not admit this user here
  // (granted, no feature), so the server refuses the song job and the form starts the write-free design.
  await withSpotify(page)
  await page.getByRole('link', { name: 'New class' }).click()
  await page.getByRole('link', { name: /^Advanced/ }).click()
  await form.getByLabel('Class name').fill('E2E climb')
  await form.getByRole('radio', { name: 'E2E Hill Climb' }).check({ force: true })
  await expect(form.getByTestId('spotify-prompt')).toHaveCount(0)
  const created = page.waitForResponse(
    (response) => response.url().endsWith('/api/jobs') && response.request().method() === 'POST' && response.status() === 201,
  )
  await form.getByRole('button', { name: 'Design my class' }).click()
  expect(((await (await created).json()) as { kind: string }).kind).toBe('design_draft')

  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/)
  await expect(page.getByRole('main').getByRole('heading', { level: 1 })).toBeVisible()
})
