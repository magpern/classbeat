import { cleanup, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { fakeApi, json, SIGNED_IN } from '../test/fakeApi'
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

const NOT_CONNECTED = {
  status: 'not_connected',
  last_error: null,
  last_error_message: null,
  callback_url: 'https://x.test/cb',
  pool_consent: false,
  consent_version: null,
  consent_text: 'x',
}

function routes(extra: Record<string, unknown> = {}) {
  return {
    'GET /api/auth/session': SIGNED_IN,
    'GET /api/music/class-playlists': { playlists: [PLAYLIST] },
    ...extra,
  } as Parameters<typeof fakeApi>[0]
}

/** None of the removed section's words: heading, loading line, failure and empty sentences, connect link. */
function expectNoMowlSection() {
  expect(screen.queryByRole('heading', { name: /MOWL/ })).toBeNull()
  expect(screen.queryByRole('link', { name: /MOWL/ })).toBeNull()
  for (const gone of [
    /Your playlists in MOWL/,
    /Loading your MOWL music/,
    /MOWL playlists could not be loaded/,
    /MOWL lists no Spotify playlists/,
  ]) {
    expect(screen.queryByText(gone)).toBeNull()
  }
}

describe('the Music page', () => {
  it('shows the made playlists with the API link and a page subtitle that names only them', async () => {
    fakeApi(routes())
    renderAt('/music')
    expect(await screen.findByRole('heading', { name: 'Playlists we made' })).toBeInTheDocument()
    const open = await screen.findByRole('link', { name: 'OPEN SPOTIFY' })
    expect(open).toHaveAttribute('href', LINK)
    expect(screen.getByRole('link', { name: 'Hill day' })).toHaveAttribute('href', '/en/classes/c1')
    expect(screen.getByText('The playlists made for your classes.')).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 2 })).toHaveLength(1)
    expectNoMowlSection()
  })

  it('shows the Spotify attribution once, under the intro and above the made playlists', async () => {
    fakeApi(routes())
    renderAt('/music')
    await screen.findByText('Hill day by classbeat')
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
  })

  it('shows the attribution only when a made playlist exists: not while loading, empty or failed', async () => {
    fakeApi(routes())
    renderAt('/music')
    expect(screen.queryByTestId('spotify-attribution')).toBeNull()
    await screen.findByText('Hill day by classbeat')
    cleanup()
    fakeApi(routes({ 'GET /api/music/class-playlists': { playlists: [] } }))
    renderAt('/music')
    await screen.findByText(/No playlists yet/)
    expect(screen.queryByTestId('spotify-attribution')).toBeNull()
    cleanup()
    fakeApi(routes({ 'GET /api/music/class-playlists': json({ detail: 'down' }, 502) }))
    renderAt('/music')
    await screen.findByText('Your playlists could not be loaded.')
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
    fakeApi(routes({ 'GET /api/connections/spotify': NOT_CONNECTED }))
    renderAt('/music')
    expect(await screen.findByRole('link', { name: 'Connect Spotify' })).toHaveAttribute('href', '/en/account/spotify')
  })
})

describe('the Music page and the MOWL playlists', () => {
  const states: [string, Record<string, unknown>][] = [
    ['made playlists present', {}],
    ['no made playlists', { 'GET /api/music/class-playlists': { playlists: [] } }],
    ['the made playlists failed', { 'GET /api/music/class-playlists': json({ detail: 'down' }, 502) }],
    ['MOWL not connected', { 'GET /api/connections': { connections: [] } }],
    ['Spotify not connected', { 'GET /api/connections/spotify': NOT_CONNECTED }],
  ]

  it.each(states)('shows no MOWL section and asks for no MOWL list: %s', async (_name, extra) => {
    const api = fakeApi(routes(extra))
    renderAt('/music')
    await screen.findByRole('heading', { name: 'Playlists we made' })
    await waitFor(() => {
      expect(screen.queryByText('Loading your playlists')).toBeNull()
    })
    expectNoMowlSection()
    expect(api.calls.filter((c) => c.path.includes('mowl-playlists'))).toHaveLength(0)
  })

  it('reads the connections once for the Connect Spotify card', async () => {
    const api = fakeApi(routes({ 'GET /api/connections': { connections: [] }, 'GET /api/connections/spotify': NOT_CONNECTED }))
    renderAt('/music')
    expect(await screen.findByRole('link', { name: 'Connect Spotify' })).toBeInTheDocument()
    expect(api.to('GET', '/api/connections')).toHaveLength(1)
  })
})

describe('the Music tiles', () => {
  it('lift on hover only when motion is allowed: every transition and hover class is motion-safe', async () => {
    fakeApi(routes())
    renderAt('/music')
    const tile = (await screen.findByText('Hill day by classbeat')).closest('div')
    expect(tile).not.toBeNull()
    const motion = [...(tile?.classList ?? [])].filter((c) => /transition|duration|translate|shadow-glow/.test(c))
    expect(motion.length).toBeGreaterThan(0)
    for (const c of motion) expect(c.startsWith('motion-safe:')).toBe(true)
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
