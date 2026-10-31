import { screen, within } from '@testing-library/react'
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
    expect(await screen.findByRole('heading', { name: 'Playlists classbeat made' })).toBeInTheDocument()
    const open = await screen.findByRole('link', { name: 'OPEN SPOTIFY' })
    expect(open).toHaveAttribute('href', LINK)
    expect(screen.getByRole('link', { name: 'Hill day' })).toHaveAttribute('href', '/classes/c1')
    const mowl = await screen.findByRole('heading', { name: 'Your playlists in MOWL' })
    expect(within(mowl.parentElement!).getByText('Warm up')).toBeInTheDocument()
    expect(screen.getByTestId('spotify-attribution')).toBeInTheDocument()
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
    expect(await screen.findByRole('link', { name: 'Connect Spotify' })).toHaveAttribute('href', '/account/spotify')
  })
})
