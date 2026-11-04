import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { fakeApi, json, reason } from '../test/fakeApi'
import { SESSION_ROUTES } from '../test/jobFixtures'
import { renderAt } from '../test/render'
import { connectionsBody } from '../test/songFixtures'

// The New class picker's playlist covers (ADR 0013 section 9, amendment of 2026-11-03, #537).
// Invented URLs only: jsdom never loads an image, and nothing here asks a network.
const HEX = 'ab12cd34ef'.repeat(4)
const url = (host: string, path: string) => `${['https:', '', host].join('/')}${path}`
const MOSAIC = url('mosaic.scdn.co', `/300/${HEX.repeat(4)}`)
const UPLOADED = url('image-cdn-ak.spotifycdn.com', `/image/${HEX}`)
const HILLS = 'AbCdEfGhIjKlMnOpQrStUv'
const SPRINTS = 'ZyXwVuTsRqPoNmLkJiHgFe'
const RECOVERY = 'RecoveryRide0000000000'
const SPOTIFY = {
  status: 'connected',
  last_error: null,
  last_error_message: null,
  callback_url: 'http://127.0.0.1:8000/oauth/spotify/callback',
  pool_consent: true,
  consent_version: 1,
  consent_text: 'x',
}
const READY = {
  ...SESSION_ROUTES,
  'GET /api/connections': connectionsBody(),
  'GET /api/connections/spotify': SPOTIFY,
  'GET /api/music/mowl-playlists': {
    playlists: [
      { spotify_playlist_id: HILLS, name: 'Hills' },
      { spotify_playlist_id: SPRINTS, name: 'Sprint Mix' },
      { spotify_playlist_id: RECOVERY, name: 'Recovery Ride' },
    ],
  },
}

function tile(name: string): HTMLElement {
  const label = screen.getByRole('radio', { name }).closest('label')
  if (label === null) throw new Error(`no tile for ${name}`)
  return label
}

describe('playlist covers in the New class picker', () => {
  it('shows each cover on its own tile by Spotify playlist id, initials for the rest', async () => {
    const api = fakeApi({
      ...READY,
      'GET /api/music/playlist-covers': {
        covers: [
          { spotify_playlist_id: HILLS, cover: MOSAIC },
          { spotify_playlist_id: SPRINTS, cover: UPLOADED },
          { spotify_playlist_id: 'NotInMowl0000000000000', cover: MOSAIC },
        ],
      },
    })
    renderAt('/classes/new')
    await screen.findByRole('radio', { name: 'Hills' })
    await waitFor(() => {
      expect(tile('Hills').querySelector('img')?.getAttribute('src')).toBe(MOSAIC)
    })
    const hills = tile('Hills').querySelector('img')
    expect(hills?.getAttribute('referrerpolicy')).toBe('no-referrer')
    expect(hills?.getAttribute('loading')).toBe('lazy')
    expect(hills?.getAttribute('alt')).toBe('')
    expect(tile('Sprint Mix').querySelector('img')?.getAttribute('src')).toBe(UPLOADED)
    expect(tile('Recovery Ride').querySelector('img')).toBeNull()
    expect(within(tile('Recovery Ride')).getByText('RR')).toBeInTheDocument()
    // The tile's name stays the radio's accessible name; the cover is decorative.
    expect(within(screen.getByRole('radiogroup', { name: 'Playlist' })).getAllByRole('radio')).toHaveLength(3)
    expect(document.querySelectorAll('img')).toHaveLength(2)
    expect(api.to('GET', '/api/music/playlist-covers')).toHaveLength(1)
  })

  it('renders the picker with initials at once and adds covers when the slow answer arrives', async () => {
    let answer: (value: Response) => void = () => undefined
    const pending = new Promise<Response>((resolve) => {
      answer = resolve
    })
    fakeApi(READY)
    // fakeApi answers at once; hold this one request open instead.
    const answered = globalThis.fetch
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      (typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url).endsWith(
        '/api/music/playlist-covers',
      )
        ? pending
        : answered(input, init),
    )
    renderAt('/classes/new')
    // The covers request is still open: every tile has its initials and the form can be used.
    expect(await screen.findByRole('radio', { name: 'Hills' })).toBeInTheDocument()
    expect(within(tile('Hills')).getByText('H')).toBeInTheDocument()
    expect(within(tile('Sprint Mix')).getByText('SM')).toBeInTheDocument()
    expect(document.querySelectorAll('img')).toHaveLength(0)
    expect(screen.getByRole('button', { name: 'Design my class' })).toBeInTheDocument()
    answer(json({ covers: [{ spotify_playlist_id: HILLS, cover: MOSAIC }] }))
    await waitFor(() => {
      expect(tile('Hills').querySelector('img')?.getAttribute('src')).toBe(MOSAIC)
    })
    expect(within(tile('Sprint Mix')).getByText('SM')).toBeInTheDocument()
  })

  it('never shows a URL outside the pinned shapes, even when the API sent one', async () => {
    fakeApi({
      ...READY,
      'GET /api/music/playlist-covers': {
        covers: [
          { spotify_playlist_id: HILLS, cover: url('evil.example', `/image/${HEX}`) },
          { spotify_playlist_id: SPRINTS, cover: `${MOSAIC}?x=1` },
          { spotify_playlist_id: RECOVERY, cover: url('daily-mix.scdn.co', `/covers/${HEX}`) },
        ],
      },
    })
    renderAt('/classes/new')
    await screen.findByRole('radio', { name: 'Hills' })
    await waitFor(() => {
      expect(tile('Hills').querySelector('[data-testid="cover-tile"]')).not.toBeNull()
    })
    expect(document.querySelectorAll('img')).toHaveLength(0)
  })

  it.each([
    ['a server error', () => reason(503, 'upstream_unavailable')],
    ['its own rate limit', () => json({ detail: { reason: 'rate_limited' } }, 429, { 'Retry-After': '60' })],
    ['a malformed answer', () => ({ covers: [{ spotify_playlist_id: HILLS }] })],
  ])('shows initials and no error after %s', async (_name, answer) => {
    fakeApi({ ...READY, 'GET /api/music/playlist-covers': answer })
    renderAt('/classes/new')
    await screen.findByRole('radio', { name: 'Hills' })
    await waitFor(() => {
      expect(within(tile('Hills')).getByText('H')).toBeInTheDocument()
    })
    expect(document.querySelectorAll('img')).toHaveLength(0)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByRole('button', { name: 'Design my class' })).toBeInTheDocument()
  })

  it('asks for covers only for a user with a Spotify connection', async () => {
    const api = fakeApi({
      ...READY,
      'GET /api/connections': connectionsBody(false),
      'GET /api/connections/spotify': { ...SPOTIFY, status: 'not_connected' },
    })
    renderAt('/classes/new')
    await screen.findByRole('radio', { name: 'Hills' })
    expect(api.to('GET', '/api/music/playlist-covers')).toHaveLength(0)
    expect(document.querySelectorAll('img')).toHaveLength(0)
  })

  it('asks once, after the MOWL list, and not when MOWL lists no playlists', async () => {
    const api = fakeApi({ ...READY, 'GET /api/music/mowl-playlists': { playlists: [] } })
    renderAt('/classes/new')
    await screen.findByLabelText('Class name')
    expect(api.to('GET', '/api/music/mowl-playlists')).toHaveLength(1)
    expect(api.to('GET', '/api/music/playlist-covers')).toHaveLength(0)
  })
})
