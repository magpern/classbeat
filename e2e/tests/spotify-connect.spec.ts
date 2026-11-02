import { type Page } from '@playwright/test'
import { expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import {
  CLIENT_A,
  CLIENT_B,
  CLIENT_UNKNOWN,
  USER_A,
  USER_B,
  connectSpotify,
  control,
  fakeState,
  playSpotify,
  resetSpotify,
  signInAtSpotify,
} from './spotify-helpers'

// The Spotify connection end to end (ADR 0009 sections 2 to 9; ADR 0008 section 12) against the
// fake Spotify of compose.e2e.yaml: the wizard, the consent, Disconnect, two users on different
// Client IDs and every error category's page text. The fake answers as accounts.spotify.com and
// api.spotify.com inside the compose network; here Playwright plays the browser's visit to the
// authorize page (spotify-helpers.ts). Public test values only: no real Spotify account.

const CALLBACK = `http://127.0.0.1:${process.env.WEB_PORT ?? '8000'}/oauth/spotify/callback`

test.beforeEach(() => {
  resetSpotify()
})

test.afterAll(() => {
  resetSpotify()
})

async function newUser(page: Page, tag: string): Promise<void> {
  const email = uniqueEmail(tag)
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email) // a fresh sign-in counts as recent authentication
}

test('the wizard shows the callback address and refuses a Client ID that is not 32 hex characters', async ({ page }) => {
  await newUser(page, 'sp-wizard')
  await page.goto('/account')
  await expect(page.getByTestId('spotify-summary-status')).toHaveText('Not connected')
  await page.getByRole('link', { name: 'Set up Spotify' }).click()

  await expect(page.getByTestId('spotify-callback-url')).toHaveText(CALLBACK)
  await expect(page.getByText(/never needs the app.s Client Secret/)).toBeVisible()
  await expect(page.getByLabel(/secret/i)).toHaveCount(0) // no field for a Client Secret

  await page.getByLabel('Spotify Client ID').fill('not-a-client-id')
  await page.getByRole('button', { name: 'Connect Spotify' }).click()
  await expect(page.getByText(/does not look like a Spotify Client ID/)).toBeVisible()
  expect(fakeState().requests).toEqual([]) // blocked in the page: nothing was sent anywhere
  await expect(page.getByTestId('spotify-status')).toHaveText('Not connected')
})

test('a Client ID Spotify does not know leaves nothing connected', async ({ page }) => {
  await newUser(page, 'sp-wrong')
  const { seen } = await playSpotify(page)
  await connectSpotify(page, CLIENT_UNKNOWN)
  await expect.poll(() => seen.length).toBe(1)
  expect(seen[0]).toEqual({ clientId: CLIENT_UNKNOWN, status: 400 }) // Spotify's own error page
  // Wait until the authorize navigation finishes; `seen` is set when the request starts, and
  // `goto` too soon races that redirect and Playwright reports an interrupted navigation.
  await page.waitForURL(/accounts\.spotify\.com\/authorize/)
  // No callback reached classbeat: it still says not connected, and no token was asked for.
  await page.goto('/account/spotify')
  await expect(page.getByTestId('spotify-status')).toHaveText('Not connected')
  expect(fakeState().requests.map((r) => r.path)).toEqual(['/authorize'])
})

test('cancelling at Spotify says it was not connected', async ({ page }) => {
  await newUser(page, 'sp-cancel')
  control('POST', 'deny')
  const { seen } = await playSpotify(page)
  await connectSpotify(page, CLIENT_A)
  await expect(page.getByText('Spotify was not connected.')).toBeVisible()
  expect(seen).toHaveLength(1)
  expect(new URL(page.url()).searchParams.get('result')).toBe('access_denied')
  await expect(page.getByTestId('spotify-status')).toHaveText('Not connected')
  expect(fakeState().requests.map((r) => r.path)).toEqual(['/authorize']) // no token exchange
})

test('an account that is not on the app allow-list is refused in plain words', async ({ page }) => {
  await newUser(page, 'sp-allow')
  signInAtSpotify(USER_A) // USER_A approves, but CLIENT_B allows only USER_B
  const { seen } = await playSpotify(page)
  await connectSpotify(page, CLIENT_B)
  await expect(page.getByText(/Spotify refused this account\. In your Spotify app.s User Management/)).toBeVisible()
  expect(seen).toHaveLength(1)
  await expect(page.getByTestId('spotify-status')).toHaveText('Not connected')
  const paths = fakeState().requests.map((r) => `${r.host}${r.path}`)
  expect(paths).toEqual(['accounts.spotify.com/authorize', 'accounts.spotify.com/api/token', 'api.spotify.com/v1/me'])
})

test('a callback with a changed state connects nothing', async ({ page }) => {
  await newUser(page, 'sp-state')
  await playSpotify(page, { tamper: (query) => query.set('state', `${query.get('state') ?? ''}x`) })
  await connectSpotify(page, CLIENT_A)
  await expect(page.getByTestId('spotify-status')).toHaveText('Not connected')
  expect(new URL(page.url()).searchParams.get('result')).not.toBe('connected')
  // classbeat never exchanged the code (it was not the flow's own state).
  expect(fakeState().requests.map((r) => r.path)).not.toContain('/api/token')
})

test('connect, allow the working playlist, revoke, then disconnect', async ({ page }) => {
  await newUser(page, 'sp-flow')
  const { seen } = await playSpotify(page)
  await connectSpotify(page, CLIENT_A)
  await expect(page.getByText('Spotify is connected.')).toBeVisible()
  expect(seen).toEqual([{ clientId: CLIENT_A, status: 302 }])
  await expect(page.getByTestId('spotify-status')).toHaveText('Connected')
  // classbeat's own requests: PKCE exchange and /me, with this app's token and no client secret.
  const sent = fakeState().requests
  expect(sent.map((r) => [r.path, r.client_id ?? r.token_app, r.token_user])).toEqual([
    ['/authorize', CLIENT_A, null],
    ['/api/token', CLIENT_A, null],
    ['/v1/me', CLIENT_A, USER_A],
  ])
  expect(sent.every((r) => !r.client_secret)).toBe(true)

  // The consent: the exact sentence the server sends, off until the user gives it.
  await expect(page.getByTestId('spotify-consent-state')).toHaveText('You have not allowed it yet.')
  await expect(page.getByTestId('spotify-consent-text')).not.toBeEmpty()
  await page.getByRole('switch', { name: 'Allow the working playlist' }).click()
  await expect(page.getByText(/classbeat may use the working playlist/)).toBeVisible()
  await expect(page.getByTestId('spotify-consent-state')).toHaveText('You have allowed the working playlist.')
  await page.reload()
  await expect(page.getByTestId('spotify-consent-state')).toHaveText('You have allowed the working playlist.')

  await page.getByRole('switch', { name: 'Allow the working playlist' }).click()
  await expect(page.getByRole('group', { name: 'Confirm revoke' })).toBeVisible()
  await page.getByRole('button', { name: 'Yes, revoke my consent' }).click()
  await expect(page.getByTestId('spotify-consent-state')).toHaveText('You have not allowed it yet.')

  // Disconnect: the dialog says what is deleted and what stays; afterwards no Spotify request.
  const before = fakeState().requests.length
  await page.getByRole('button', { name: 'Disconnect Spotify' }).click()
  await expect(page.getByRole('group', { name: 'Confirm disconnect' })).toContainText('Playlists already in your Spotify stay there')
  await page.getByRole('button', { name: 'Yes, disconnect Spotify' }).click()
  await expect(page.getByText(/Spotify is disconnected/)).toBeVisible()
  await expect(page.getByTestId('spotify-status')).toHaveText('Not connected')
  expect(fakeState().requests).toHaveLength(before)
  await page.reload()
  await expect(page.getByTestId('spotify-status')).toHaveText('Not connected')
})

test('two users on different Client IDs connect side by side and never mix', async ({ browser }) => {
  const contextOne = await browser.newContext()
  const contextTwo = await browser.newContext()
  const one = await contextOne.newPage()
  const two = await contextTwo.newPage()
  try {
    await newUser(one, 'sp-one')
    await newUser(two, 'sp-two')
    await playSpotify(one)
    await playSpotify(two)

    signInAtSpotify(USER_A)
    await connectSpotify(one, CLIENT_A)
    await expect(one.getByText('Spotify is connected.')).toBeVisible()
    signInAtSpotify(USER_B)
    await connectSpotify(two, CLIENT_B)
    await expect(two.getByText('Spotify is connected.')).toBeVisible()

    // Every /me carried its own user's app and token; the apps and users never crossed.
    const me = fakeState().requests.filter((r) => r.path === '/v1/me')
    expect(me.map((r) => [r.token_app, r.token_user])).toEqual([
      [CLIENT_A, USER_A],
      [CLIENT_B, USER_B],
    ])

    // Consent is each user's own, and disconnecting one leaves the other connected.
    await one.getByRole('switch', { name: 'Allow the working playlist' }).click()
    await expect(one.getByTestId('spotify-consent-state')).toHaveText('You have allowed the working playlist.')
    await two.reload()
    await expect(two.getByTestId('spotify-consent-state')).toHaveText('You have not allowed it yet.')

    await two.getByRole('button', { name: 'Disconnect Spotify' }).click()
    await two.getByRole('button', { name: 'Yes, disconnect Spotify' }).click()
    await expect(two.getByTestId('spotify-status')).toHaveText('Not connected')
    await one.reload()
    await expect(one.getByTestId('spotify-status')).toHaveText('Connected')
    await expect(one.getByTestId('spotify-consent-state')).toHaveText('You have allowed the working playlist.')
  } finally {
    await contextOne.close()
    await contextTwo.close()
  }
})

test('another Spotify account for the same user needs a confirmed switch', async ({ page }) => {
  await newUser(page, 'sp-switch')
  await playSpotify(page)
  await connectSpotify(page, CLIENT_A)
  await expect(page.getByText('Spotify is connected.')).toBeVisible()

  // The same user now connects an app whose signed-in account is another person.
  signInAtSpotify(USER_B)
  await page.getByRole('button', { name: 'Change Client ID' }).click()
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByLabel('Spotify Client ID').fill(CLIENT_B)
  await page.getByRole('button', { name: /^Connect Spotify/ }).click()
  await expect(page.getByText(/This is a different Spotify account than before/)).toBeVisible()
  await expect(page.getByTestId('spotify-status')).toHaveText('Connected') // the old connection stays
  await expect(page.getByRole('button', { name: 'Confirm and switch account' })).toBeVisible()

  await page.getByLabel('Spotify Client ID').fill(CLIENT_B)
  await page.getByRole('button', { name: 'Confirm and switch account' }).click()
  await expect(page.getByText('Spotify is connected.')).toBeVisible()
  const me = fakeState().requests.filter((r) => r.path === '/v1/me')
  expect(me.map((r) => r.token_user)).toEqual([USER_A, USER_B, USER_B])
})

// The sentence of every error category (ADR 0009 section 8), as the callback's redirect shows it.
const SENTENCES: Record<string, string | RegExp> = {
  invalid_grant: 'Spotify no longer accepts the connection. Reconnect Spotify.',
  app_misconfigured: /Check the Client ID and the redirect URI in your Spotify app\./,
  not_allowlisted: /make sure the app.s owner has Spotify Premium\./,
  forbidden: 'Spotify refused this request for your account or playlist.',
  upstream_unavailable: 'Spotify is not answering.',
  not_found: 'Spotify could not find that playlist.',
  scope_mismatch: 'Spotify did not give the access classbeat needs. Connect again and allow it.',
  invalid_response: 'Spotify did not give the access classbeat needs. Connect again and allow it.',
  account_changed: /different Spotify account than before\. Confirm to switch/,
  access_denied: 'Spotify was not connected.',
  anything_else: 'Something went wrong with Spotify. Try again later.',
}

test('every error category has its plain sentence on the Spotify page, and none shows a provider message', async ({ page }) => {
  await newUser(page, 'sp-errors')
  for (const [category, sentence] of Object.entries(SENTENCES)) {
    await page.goto(`/account/spotify?result=${category}`)
    await expect(page.getByRole('main').getByText(sentence).first(), category).toBeVisible()
  }
  await page.goto('/account/spotify?result=rate_limited&retry_after=120')
  await expect(page.getByText('Spotify asks to slow down; trying again in about 2 minutes.')).toBeVisible()
  await page.goto('/account/spotify?result=rate_limited')
  await expect(page.getByText('Spotify asks to slow down; trying again in about 1 minute.')).toBeVisible()
  await page.goto('/account/spotify?result=quota_exhausted&retry_after=7200')
  await expect(page.getByText('Your Spotify app has used up its allowance for now. Try again in about 2 hours.')).toBeVisible()
  // The category alone is read; a message in the address is never printed.
  await page.goto('/account/spotify?result=forbidden&message=SECRET-PROVIDER-TEXT')
  await expect(page.getByText('SECRET-PROVIDER-TEXT')).toHaveCount(0)
})

test('a token endpoint that refuses the app gives the app-misconfigured sentence', async ({ page }) => {
  await newUser(page, 'sp-injected')
  control('POST', 'inject?kind=invalid_client&path=/api/token&times=1')
  await playSpotify(page)
  await connectSpotify(page, CLIENT_A)
  await expect(page.getByText(/Check the Client ID and the redirect URI in your Spotify app\./)).toBeVisible()
  await expect(page.getByTestId('spotify-status')).toHaveText('Not connected')
})
