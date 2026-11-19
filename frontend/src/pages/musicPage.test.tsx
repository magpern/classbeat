import { cleanup, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { fakeApi, json, SIGNED_IN } from '../test/fakeApi'
import { connectionsBody } from '../test/songFixtures'
import { renderAt } from '../test/render'

const LINK = 'https://open.spotify.com/playlist/' + 'a'.repeat(22)
const PLAYLIST = {
  class_id: 'c1',
  class_title: 'Hill day',
  name: 'Hill day by classbeat',
  link: LINK,
  detached: false,
  note: null,
  songs: [
    { title: 'One', cover: null },
    { title: 'Two', cover: null },
  ],
}

function routes(extra: Record<string, unknown> = {}) {
  return {
    'GET /api/auth/session': SIGNED_IN,
    'GET /api/music/mowl-playlists': { playlists: [{ spotify_playlist_id: 'p1', name: 'Warm up' }] },
    'GET /api/music/class-playlists': { playlists: [PLAYLIST] },
    ...extra,
  } as Parameters<typeof fakeApi>[0]
}

describe('the Music page', () => {
  it('shows MOWL playlists as initials tiles and made playlists with the API link', async () => {
    fakeApi(routes())
    renderAt('/music')
    expect(await screen.findByRole('heading', { name: 'Playlists we made' })).toBeInTheDocument()
    const open = await screen.findByRole('link', { name: 'OPEN SPOTIFY' })
    expect(open).toHaveAttribute('href', LINK)
    expect(screen.getByRole('link', { name: 'Hill day' })).toHaveAttribute('href', '/en/classes/c1')
    const mowl = await screen.findByRole('heading', { name: 'Your playlists in MOWL' })
    expect(within(mowl.parentElement!).getByText('Warm up')).toBeInTheDocument()
  })

  it('shows the Spotify attribution once, under the intro and above both lists', async () => {
    fakeApi(routes())
    renderAt('/music')
    const mowl = await screen.findByRole('heading', { name: 'Your playlists in MOWL' })
    await screen.findByText('Warm up')
    const pill = screen.getByTestId('spotify-attribution-pill')
    expect(screen.getAllByTestId('spotify-attribution')).toHaveLength(1)
    // The logo keeps its alt text (one file per theme) and a caption follows it.
    expect(within(pill).getAllByAltText('Spotify')).toHaveLength(2)
    expect(within(pill).getByText('Songs and art from Spotify')).toBeInTheDocument()
    const intro = screen.getByText(/The playlists made for your classes/)
    const made = screen.getByRole('heading', { name: 'Playlists we made' })
    const after = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
    expect(after(intro, pill)).toBe(true)
    expect(after(pill, made)).toBe(true)
    expect(after(pill, mowl)).toBe(true)
  })

  it('shows the attribution when only one of the two lists has items', async () => {
    fakeApi(routes({ 'GET /api/music/mowl-playlists': { playlists: [] } }))
    renderAt('/music')
    await screen.findByText('Hill day by classbeat')
    expect(screen.getAllByTestId('spotify-attribution')).toHaveLength(1)
    cleanup()
    fakeApi(routes({ 'GET /api/music/class-playlists': { playlists: [] } }))
    renderAt('/music')
    await screen.findByText('Warm up')
    expect(screen.getAllByTestId('spotify-attribution')).toHaveLength(1)
  })

  it('shows no attribution while loading, when both lists are empty, or when both fail', async () => {
    fakeApi(routes())
    renderAt('/music')
    expect(screen.queryByTestId('spotify-attribution')).toBeNull()
    await screen.findByText('Warm up')
    cleanup()
    fakeApi(
      routes({
        'GET /api/connections': connectionsBody(false),
        'GET /api/music/mowl-playlists': { playlists: [] },
        'GET /api/music/class-playlists': { playlists: [] },
      }),
    )
    renderAt('/music')
    await screen.findByText(/No playlists yet/)
    await screen.findByText('MOWL lists no Spotify playlists for your account.')
    expect(screen.queryByTestId('spotify-attribution')).toBeNull()
    cleanup()
    fakeApi(
      routes({
        'GET /api/music/mowl-playlists': json({ detail: 'down' }, 502),
        'GET /api/music/class-playlists': json({ detail: 'down' }, 502),
      }),
    )
    renderAt('/music')
    await screen.findByText(/Your MOWL playlists could not be loaded\./)
    expect(screen.queryByTestId('spotify-attribution')).toBeNull()
  })

  it('offers no link for a playlist without one, and drops a link that is not a playlist link', async () => {
    fakeApi(
      routes({
        'GET /api/music/class-playlists': {
          playlists: [
            { ...PLAYLIST, link: null, detached: true, note: 'This playlist was changed in Spotify.' },
            { ...PLAYLIST, class_id: 'c2', link: 'https://evil.example/x' },
          ],
        },
      }),
    )
    renderAt('/music')
    expect(await screen.findByText('This playlist was changed in Spotify.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'OPEN SPOTIFY' })).toBeNull()
  })

  it('says so when there are no made playlists', async () => {
    fakeApi(routes({ 'GET /api/music/class-playlists': json({ playlists: [] }) }))
    renderAt('/music')
    expect(await screen.findByText(/No playlists yet/)).toBeInTheDocument()
  })

  it('shows a Connect Spotify card while Spotify is not connected', async () => {
    fakeApi(
      routes({
        'GET /api/connections/spotify': {
          status: 'not_connected',
          last_error: null,
          last_error_message: null,
          callback_url: 'https://x.test/cb',
          pool_consent: false,
          consent_version: null,
          consent_text: 'x',
        },
      }),
    )
    renderAt('/music')
    expect(await screen.findByRole('link', { name: 'Connect Spotify' })).toHaveAttribute('href', '/en/account/spotify')
  })
})

describe('the Music page when MOWL playlists cannot be listed', () => {
  it('shows an empty-state card with one sentence and the connect link', async () => {
    fakeApi(routes({ 'GET /api/music/mowl-playlists': json({ detail: 'down' }, 502) }))
    renderAt('/music')
    expect(await screen.findByText(/Your MOWL playlists could not be loaded\./)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Connect MOWL' })).toHaveAttribute('href', '/en/account')
  })

  it('offers no connect link when MOWL is connected but the playlists cannot be loaded', async () => {
    fakeApi(
      routes({
        'GET /api/connections': connectionsBody(false),
        'GET /api/music/mowl-playlists': json({ detail: 'down' }, 502),
      }),
    )
    renderAt('/music')
    expect(await screen.findByText(/Try again in a few minutes\./)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Connect MOWL' })).toBeNull()
  })
})

describe('the Music page load', () => {
  it('reads the connections once and shares them between the Spotify card and the MOWL state', async () => {
    const api = fakeApi(
      routes({
        'GET /api/connections': { connections: [] },
        'GET /api/connections/spotify': {
          status: 'not_connected',
          last_error: null,
          last_error_message: null,
          callback_url: 'https://x.test/cb',
          pool_consent: false,
          consent_version: null,
          consent_text: 'x',
        },
      }),
    )
    renderAt('/music')
    expect(await screen.findByRole('link', { name: 'Connect Spotify' })).toBeInTheDocument()
    await screen.findByText('Warm up')
    expect(api.to('GET', '/api/connections')).toHaveLength(1)
  })
})

describe('the Music page when MOWL lists no playlists', () => {
  const empty = { 'GET /api/music/mowl-playlists': { playlists: [] } }

  it('says so with no connect link while MOWL is connected', async () => {
    fakeApi(routes({ ...empty, 'GET /api/connections': connectionsBody(false) }))
    renderAt('/music')
    expect(await screen.findByText('MOWL lists no Spotify playlists for your account.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Connect MOWL' })).toBeNull()
  })

  it('offers Connect MOWL while MOWL is not connected', async () => {
    fakeApi(routes({ ...empty, 'GET /api/connections': { connections: [] } }))
    renderAt('/music')
    expect(await screen.findByRole('link', { name: 'Connect MOWL' })).toHaveAttribute('href', '/en/account')
  })
})

describe('the Music tiles', () => {
  it('lift on hover only when motion is allowed: every transition and hover class is motion-safe', async () => {
    fakeApi(routes())
    renderAt('/music')
    const tiles = [(await screen.findByText('Warm up')).closest('li'), (await screen.findByText('Hill day by classbeat')).closest('div')]
    for (const tile of tiles) {
      expect(tile).not.toBeNull()
      const motion = [...(tile?.classList ?? [])].filter((c) => /transition|duration|translate|shadow-glow/.test(c))
      expect(motion.length).toBeGreaterThan(0)
      for (const c of motion) expect(c.startsWith('motion-safe:')).toBe(true)
    }
  })

  it('size the made-playlist cover by the tile, not by a fixed size', async () => {
    fakeApi(routes())
    renderAt('/music')
    await screen.findByText('Hill day by classbeat')
    const mosaic = screen.getAllByTestId('cover-mosaic')[0]
    expect(mosaic).toHaveClass('w-full', 'aspect-square')
    expect(mosaic).not.toHaveClass('size-32')
  })
})
