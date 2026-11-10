import { connectMowl } from './design-helpers'
import { COVER_ORIGINS, expect, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import { audit, watchConsole } from './mu-helpers'
import { readyToPick, sql } from './song-helpers'
import { USER_A, control, fakeState, resetSpotify } from './spotify-helpers'

// Playlist covers in the New class picker (ADR 0013 section 9, amendment of 2026-11-03; #537)
// against the fake MOWL and the fake Spotify. The fake MOWL lists its five public test playlists;
// the fake Spotify holds the same ids for USER_A with images of each kind
// (spotifycore.playlist_images). The cover hosts are answered by the test's network guard (a
// pixel); nothing leaves the machine. Public test values only.

test.setTimeout(120_000)

// The MOWL fake's playlists (e2e/fake-mowl/server.py PLAYLISTS) and the image kind each gets.
const TILES = [
  { id: '0aBcDeFgHiJkLmNoPqRsTu', name: 'E2E Hill Climb', images: 'mosaic', prefix: 'https://mosaic.scdn.co/300/' },
  { id: '1zYxWvUtSrQpOnMlKjIhGf', name: 'E2E Sprints', images: 'uploaded', prefix: 'https://image-cdn-ak.spotifycdn.com/image/' },
  { id: '3dEfGhIjKlMnOpQrStUvWx', name: 'E2E Slow Mix', images: 'album', prefix: 'https://i.scdn.co/image/' },
  { id: '2cDeFgHiJkLmNoPqRsTuVw', name: 'E2E Draft Climb', images: 'foreign', prefix: null },
  { id: '4eFgHiJkLmNoPqRsTuVwXy', name: 'E2E Flaky Attach', images: 'query', prefix: null },
] as const

test.beforeAll(() => {
  // The song_picking flag is "granted" by default; admit everyone as song-pick.spec.ts does.
  sql(
    `INSERT INTO app_settings (key, value) VALUES ('song_picking', '"on"') ` +
      `ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`,
  )
  resetSpotify()
})

test.afterAll(() => {
  resetSpotify()
  sql(`DELETE FROM app_settings WHERE key = 'song_picking'`)
})

test('the picker shows the covers of the pinned shapes and initials for the rest', async ({ page }) => {
  const consoleErrors = watchConsole(page)
  const email = uniqueEmail('covers')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await readyToPick(page)
  for (const tile of TILES) {
    control(
      'POST',
      `playlist?owner=${USER_A}&name=${encodeURIComponent(tile.name)}&id=${tile.id}&images=${tile.images}`,
    )
  }
  const before = fakeState().requests.length

  const cover = page.waitForResponse((response) => response.url().endsWith('/api/music/playlist-covers'))
  const document = await page.goto('/classes/new/advanced')
  const covers = await cover
  expect(covers.status()).toBe(200)
  expect(covers.headers()['cache-control']).toBe('no-store')
  const csp = document!.headers()['content-security-policy'] ?? ''
  expect(csp).toContain(
    "img-src 'self' https://i.scdn.co/image/ https://mosaic.scdn.co/300/ https://mosaic.scdn.co/640/ " +
      'https://image-cdn-ak.spotifycdn.com/image/ https://image-cdn-fa.spotifycdn.com/image/;',
  )

  const group = page.getByRole('radiogroup', { name: 'Playlist' })
  await expect(group.locator('img')).toHaveCount(3)
  for (const tile of TILES) {
    const label = group.locator('label').filter({ has: page.getByRole('radio', { name: tile.name }) })
    if (tile.prefix === null) {
      await expect(label.locator('img')).toHaveCount(0)
      continue
    }
    const image = label.locator('img')
    await expect(image).toHaveAttribute('src', new RegExp(`^${tile.prefix.replaceAll('.', '\\.')}[0-9a-f]+$`))
    await expect(image).toHaveAttribute('referrerpolicy', 'no-referrer')
    await expect
      .poll(() => image.evaluate((img) => (img as HTMLImageElement).complete && (img as HTMLImageElement).naturalWidth > 0))
      .toBe(true)
  }
  // The names stay the radios' names; the covers are decorative.
  await expect(group.getByRole('radio')).toHaveCount(TILES.length)

  // One listing read of the user's own Spotify playlists, with their token, and no refresh.
  const sent = fakeState().requests.slice(before)
  expect(sent.map((request) => `${request.method} ${request.host} ${request.path}`)).toEqual([
    'GET api.spotify.com /v1/me/playlists',
  ])
  expect(sent[0]?.token_user).toBe(USER_A)

  // Every image on the page is a cover host's or our own bundle's.
  const stack = new URL(page.url()).origin
  const sources = await page.locator('img').evaluateAll((list) => list.map((img) => (img as HTMLImageElement).src))
  for (const src of sources) expect([stack, ...COVER_ORIGINS], src).toContain(new URL(src).origin)

  const result = await audit(page, 'new class with playlist covers', page.getByRole('button', { name: 'Design my class' }))
  expect(result.problems).toEqual([])
  expect(consoleErrors).toEqual([])
})

test('without a Spotify connection the picker asks for no covers', async ({ page }) => {
  const email = uniqueEmail('nocovers')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await connectMowl(page)
  const asked: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('/api/music/playlist-covers')) asked.push(request.url())
  })
  await page.goto('/classes/new/advanced')
  await expect(page.getByRole('radio', { name: 'E2E Hill Climb' })).toBeVisible()
  await expect(page.getByRole('radiogroup', { name: 'Playlist' }).locator('img')).toHaveCount(0)
  expect(asked).toEqual([])
})
