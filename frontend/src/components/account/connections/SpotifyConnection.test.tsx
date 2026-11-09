import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SIGNED_IN, fakeApi } from '../../../test/fakeApi'
import { renderAt } from '../../../test/render'

// The Spotify row on Account, Connections and its dialog (ADR 0028 section 3, ADR 0009): opening
// and closing, /account/spotify (inbound links and the return from Spotify), and the row keeping
// in step with what the dialog does. The dialog's body: ../Spotify.test.tsx.

const goToProvider = vi.hoisted(() => vi.fn<(url: string) => void>())
vi.mock('../../../lib/navigation', () => ({ goToProvider }))

const ACCOUNT_ROUTES = {
  'GET /api/auth/session': SIGNED_IN,
  'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
  'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
}

function status(value: string) {
  return {
    status: value,
    client_id: null,
    last_error: null,
    last_error_message: null,
    callback_url: 'http://127.0.0.1:8000/oauth/spotify/callback',
    pool_consent: false,
    consent_version: null,
    consent_text: 'The consent sentence.',
    song_picking_enabled: true,
  }
}

function spotifyApi(current: string, routes: Record<string, unknown> = {}) {
  return fakeApi({
    ...ACCOUNT_ROUTES,
    'GET /api/connections/spotify': () => status(current),
    ...(routes as Record<string, never>),
  })
}

const row = async () => within(await screen.findByTestId('connection-spotify'))
const spotifyDialog = () => screen.queryByRole('dialog', { name: 'Spotify' })

beforeEach(() => {
  goToProvider.mockClear()
})

describe('the Spotify row', () => {
  it('is optional, says what Spotify is for, and opens the set-up in a dialog', async () => {
    spotifyApi('not_connected')
    renderAt('/account')
    const spotify = await row()
    expect(screen.getByTestId('connection-spotify')).toHaveTextContent('Optional')
    expect(screen.getByTestId('connection-spotify')).toHaveTextContent('Picks songs for your classes')
    expect(spotify.queryByRole('link')).toBeNull() // no longer a link to another page
    const connect = spotify.getByRole('button', { name: 'Connect Spotify' })
    expect(connect).toHaveTextContent('Connect')
    expect(spotifyDialog()).toBeNull()
    fireEvent.click(connect)
    const dialog = within(await screen.findByRole('dialog', { name: 'Spotify' }))
    expect(dialog.getByText('Picks songs for your classes')).toBeInTheDocument()
    expect(await dialog.findByLabelText('Spotify Client ID')).toBeInTheDocument()
    expect(dialog.getByRole('button', { name: 'Connect Spotify' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/en/account') // opening it does not change the address
  })

  it('shows Connected and opens the management dialog', async () => {
    spotifyApi('connected')
    renderAt('/account')
    const spotify = await row()
    expect(await spotify.findByTestId('spotify-summary-status')).toHaveTextContent('Connected')
    fireEvent.click(spotify.getByRole('button', { name: 'Manage Spotify' }))
    const dialog = within(await screen.findByRole('dialog', { name: 'Spotify' }))
    expect(await dialog.findByTestId('spotify-status')).toHaveTextContent('Connected')
    expect(dialog.getByRole('switch', { name: 'Allow the working playlist' })).toBeInTheDocument()
    expect(dialog.getByRole('button', { name: 'Change Client ID' })).toBeInTheDocument()
    expect(dialog.getByRole('button', { name: 'Disconnect Spotify' })).toBeInTheDocument()
    expect(dialog.queryByLabelText('Spotify Client ID')).toBeNull()
  })

  it('shows a needed reconnect and offers to connect again in the dialog', async () => {
    spotifyApi('needs_reconnect')
    renderAt('/account')
    const spotify = await row()
    expect(await spotify.findByTestId('spotify-summary-status')).toHaveTextContent('Needs reconnecting')
    fireEvent.click(spotify.getByRole('button', { name: 'Manage Spotify' }))
    const dialog = within(await screen.findByRole('dialog', { name: 'Spotify' }))
    expect(await dialog.findByRole('button', { name: 'Connect Spotify again' })).toBeInTheDocument()
    expect(dialog.getByRole('button', { name: 'Disconnect Spotify' })).toBeInTheDocument()
  })

  it.each([
    ['the Close button', () => {
      fireEvent.click(within(screen.getByRole('dialog', { name: 'Spotify' })).getByRole('button', { name: 'Close' }))
    }],
    ['Escape', () => {
      fireEvent.keyDown(screen.getByRole('dialog', { name: 'Spotify' }), { key: 'Escape' })
    }],
  ])('closes with %s and nothing is sent', async (_how, close) => {
    const api = spotifyApi('not_connected')
    renderAt('/account')
    fireEvent.click((await row()).getByRole('button', { name: 'Connect Spotify' }))
    await screen.findByRole('dialog', { name: 'Spotify' })
    close()
    await waitFor(() => {
      expect(spotifyDialog()).toBeNull()
    })
    expect(api.to('POST', '/api/connections/spotify/start')).toHaveLength(0)
    expect(goToProvider).not.toHaveBeenCalled()
  })

  it('follows a disconnect made in the dialog', async () => {
    let connected = true
    spotifyApi('connected', {
      'GET /api/connections/spotify': () => status(connected ? 'connected' : 'not_connected'),
      'DELETE /api/connections/spotify': () => {
        connected = false
        return new Response(null, { status: 204 })
      },
    })
    renderAt('/account')
    fireEvent.click((await row()).getByRole('button', { name: 'Manage Spotify' }))
    const dialog = within(await screen.findByRole('dialog', { name: 'Spotify' }))
    fireEvent.click(await dialog.findByRole('button', { name: 'Disconnect Spotify' }))
    fireEvent.click(dialog.getByRole('button', { name: 'Yes, disconnect Spotify' }))
    expect(await dialog.findByText(/Spotify is disconnected/)).toBeInTheDocument()
    // The row behind already says so.
    expect(await (await row()).findByRole('button', { name: 'Connect Spotify' })).toBeInTheDocument()
  })
})

describe('a guard prompt from inside the dialog', () => {
  it('shows above the dialog, can be used, and the dialog waits under it', async () => {
    let confirmed = false
    spotifyApi('not_connected', {
      'POST /api/auth/reauth': () => {
        confirmed = true
        return { status: 'ok' }
      },
      'POST /api/connections/spotify/start': () =>
        confirmed
          ? { authorize_url: 'https://accounts.example.test/authorize?state=opaque' }
          : new Response(JSON.stringify({ detail: { reason: 'reauth_required' } }), { status: 403 }),
    })
    renderAt('/account')
    fireEvent.click((await row()).getByRole('button', { name: 'Connect Spotify' }))
    const spotify = await screen.findByRole('dialog', { name: 'Spotify' })
    fireEvent.change(await within(spotify).findByLabelText('Spotify Client ID'), {
      target: { value: '0123456789abcdef0123456789abcdef' },
    })
    fireEvent.click(within(spotify).getByRole('button', { name: 'Connect Spotify' }))
    const prompt = await screen.findByRole('dialog', { name: 'Confirm it is you' })
    // Not inside the page or the dialog the dialog made inert; the dialog is the one waiting.
    expect(prompt.closest('[inert]')).toBeNull()
    expect(spotify.closest('[inert]')).not.toBeNull()
    expect(prompt.contains(document.activeElement)).toBe(true)
    // Escape and a click on the prompt's backdrop are the prompt's, not the dialog's.
    fireEvent.keyDown(prompt, { key: 'Escape' })
    fireEvent.pointerDown(prompt.parentElement!)
    expect(screen.getByRole('dialog', { name: 'Spotify' })).toBe(spotify)
    fireEvent.change(await within(prompt).findByLabelText('Password'), { target: { value: 'pw' } })
    fireEvent.click(within(prompt).getByRole('button', { name: 'Confirm with password' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalledWith('https://accounts.example.test/authorize?state=opaque')
    })
    expect(spotify.closest('[inert]')).toBeNull()
  })
})

describe('/account/spotify', () => {
  it('is Connections with the Spotify dialog open', async () => {
    spotifyApi('not_connected')
    renderAt('/account/spotify')
    const dialog = within(await screen.findByRole('dialog', { name: 'Spotify' }))
    expect(await dialog.findByLabelText('Spotify Client ID')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Connections' })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByTestId('connection-spotify')).toBeInTheDocument()
  })

  it('opens Connections even with another tab in the fragment', async () => {
    spotifyApi('not_connected')
    renderAt('/account/spotify#plan')
    await screen.findByRole('dialog', { name: 'Spotify' })
    expect(screen.getByRole('tab', { name: 'Connections' })).toHaveAttribute('aria-selected', 'true')
  })

  it('shows the return from Spotify in the dialog, and the row agrees', async () => {
    spotifyApi('connected')
    renderAt('/account/spotify?result=connected')
    const dialog = within(await screen.findByRole('dialog', { name: 'Spotify' }))
    expect(await dialog.findByText('Spotify is connected.')).toBeInTheDocument()
    expect(dialog.getByTestId('spotify-status')).toHaveTextContent('Connected')
    expect(await (await row()).findByTestId('spotify-summary-status')).toHaveTextContent('Connected')
  })

  it('shows a refused return in the dialog', async () => {
    spotifyApi('not_connected')
    renderAt('/account/spotify?result=access_denied')
    const dialog = within(await screen.findByRole('dialog', { name: 'Spotify' }))
    expect(await dialog.findByRole('alert')).toHaveTextContent('Spotify was not connected.')
  })

  it('goes back to /account#connections on close, so the result is not shown again', async () => {
    spotifyApi('connected')
    renderAt('/account/spotify?result=connected')
    const dialog = within(await screen.findByRole('dialog', { name: 'Spotify' }))
    await dialog.findByText('Spotify is connected.')
    fireEvent.click(dialog.getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(spotifyDialog()).toBeNull()
    })
    expect(window.location.pathname).toBe('/en/account')
    expect(window.location.search).toBe('')
    expect(window.location.hash).toBe('#connections')
    expect(screen.getByRole('tab', { name: 'Connections' })).toHaveAttribute('aria-selected', 'true')
    // Opening it again from the row shows the status, not the old result.
    fireEvent.click((await row()).getByRole('button', { name: 'Manage Spotify' }))
    const again = within(await screen.findByRole('dialog', { name: 'Spotify' }))
    await again.findByTestId('spotify-status')
    expect(again.queryByText('Spotify is connected.')).toBeNull()
  })

  it('says song picking is off, without a row, when the feature is off', async () => {
    fakeApi(ACCOUNT_ROUTES)
    renderAt('/account/spotify')
    const dialog = within(await screen.findByRole('dialog', { name: 'Spotify' }))
    expect(await dialog.findByText('Song picking is not available right now.')).toBeInTheDocument()
    expect(screen.queryByTestId('connection-spotify')).toBeNull()
  })
})
