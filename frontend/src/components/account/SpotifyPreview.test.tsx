import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SIGNED_IN, fakeApi } from '../../test/fakeApi'
import { renderAt } from '../../test/render'

// ADR 0042 section 7.1 (#1393): the opt-in listening preview in the Spotify dialog. The wizard's
// opt-in is unticked, so a plain connect sends no `preview`; ticking it, or the connected dialog's
// "Enable listening preview", starts the same PKCE flow with `preview: true`. Never a token here.

const goToProvider = vi.hoisted(() => vi.fn<(url: string) => void>())
vi.mock('../../lib/navigation', () => ({ goToProvider }))

const ACCOUNT_ROUTES = {
  'GET /api/auth/session': SIGNED_IN,
  'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
  'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
}
const GOOD_ID = '0123456789abcdef0123456789abcdef'
const AUTHORIZE = 'https://accounts.example.test/authorize?state=opaque'
const START = 'POST /api/connections/spotify/start'

function status(overrides: Record<string, unknown> = {}) {
  return {
    status: 'not_connected',
    client_id: null,
    last_error: null,
    last_error_message: null,
    callback_url: 'http://127.0.0.1:8000/oauth/spotify/callback',
    pool_consent: false,
    consent_version: null,
    consent_text: 'The consent wording.',
    song_picking_enabled: true,
    pool_playlist: null,
    class_playlists: [],
    ...overrides,
  }
}

function spotifyApi(current: Record<string, unknown>) {
  return fakeApi({
    ...ACCOUNT_ROUTES,
    'GET /api/connections/spotify': () => current,
    [START]: { authorize_url: AUTHORIZE },
  })
}

async function openSpotify() {
  renderAt('/account/spotify')
  return within(await screen.findByRole('dialog', { name: 'Spotify' }))
}

beforeEach(() => {
  goToProvider.mockClear()
})

describe('the wizard opt-in', () => {
  it('is unticked, explains Premium and the allow-list, and a plain connect sends no preview', async () => {
    const api = spotifyApi(status())
    const sp = await openSpotify()
    const box = await sp.findByRole('checkbox', { name: 'Enable listening preview' })
    expect(box).not.toBeChecked()
    expect(box).toHaveAccessibleDescription(/Premium.*User Management/)
    fireEvent.change(sp.getByLabelText('Spotify Client ID'), { target: { value: GOOD_ID } })
    fireEvent.click(sp.getByRole('button', { name: 'Connect Spotify' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalledWith(AUTHORIZE)
    })
    const [call] = api.to('POST', '/api/connections/spotify/start')
    expect(call?.body).toEqual({ client_id: GOOD_ID, confirm_account_change: false })
  })

  it('starts with the box ticked for a user who already granted the preview', async () => {
    const api = spotifyApi(status({ status: 'connected', client_id: GOOD_ID, preview_granted: true }))
    renderAt('/account/spotify?result=account_changed')
    const sp = within(await screen.findByRole('dialog', { name: 'Spotify' }))
    expect(await sp.findByRole('checkbox', { name: 'Enable listening preview' })).toBeChecked()
    fireEvent.change(sp.getByLabelText('Spotify Client ID'), { target: { value: GOOD_ID } })
    fireEvent.click(sp.getByRole('button', { name: 'Switch account' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalledWith(AUTHORIZE)
    })
    const [call] = api.to('POST', '/api/connections/spotify/start')
    expect(call?.body).toEqual({ client_id: GOOD_ID, confirm_account_change: true, preview: true })
  })

  it('sends preview only when ticked', async () => {
    const api = spotifyApi(status())
    const sp = await openSpotify()
    fireEvent.click(await sp.findByRole('checkbox', { name: 'Enable listening preview' }))
    fireEvent.change(sp.getByLabelText('Spotify Client ID'), { target: { value: GOOD_ID } })
    fireEvent.click(sp.getByRole('button', { name: 'Connect Spotify' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalledWith(AUTHORIZE)
    })
    const [call] = api.to('POST', '/api/connections/spotify/start')
    expect(call?.body).toEqual({ client_id: GOOD_ID, confirm_account_change: false, preview: true })
  })
})

describe('a connected account', () => {
  it('offers the re-consent with the stored Client ID, without printing it', async () => {
    const api = spotifyApi(status({ status: 'connected', client_id: GOOD_ID, preview_granted: false }))
    const sp = await openSpotify()
    const section = within(await sp.findByTestId('spotify-preview'))
    expect(section.getByRole('heading', { name: 'Listening preview' })).toBeInTheDocument()
    expect(section.getByText(/Needs Spotify Premium/)).toBeInTheDocument()
    expect(section.getByText(/If you cancel, your connection stays as it is/)).toBeInTheDocument()
    expect(document.body.textContent).not.toContain(GOOD_ID)
    fireEvent.click(section.getByRole('button', { name: 'Enable listening preview' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalledWith(AUTHORIZE)
    })
    const [call] = api.to('POST', '/api/connections/spotify/start')
    expect(call?.body).toEqual({ client_id: GOOD_ID, confirm_account_change: false, preview: true })
  })

  it('says the preview is on once granted, with no button', async () => {
    spotifyApi(status({ status: 'connected', client_id: GOOD_ID, preview_granted: true }))
    const sp = await openSpotify()
    const section = within(await sp.findByTestId('spotify-preview'))
    expect(section.getByText(/Listening preview is on/)).toBeInTheDocument()
    expect(section.queryByRole('button')).toBeNull()
  })

  it('reads a status without preview_granted as not granted', async () => {
    spotifyApi(status({ status: 'connected', client_id: GOOD_ID }))
    const sp = await openSpotify()
    const section = within(await sp.findByTestId('spotify-preview'))
    expect(section.getByRole('button', { name: 'Enable listening preview' })).toBeInTheDocument()
  })

  it('never carries an account-change confirmation and hides during a switch', async () => {
    spotifyApi(status({ status: 'connected', client_id: GOOD_ID, preview_granted: false }))
    renderAt('/account/spotify?result=account_changed')
    const sp = within(await screen.findByRole('dialog', { name: 'Spotify' }))
    await sp.findByRole('button', { name: 'Switch account' })
    expect(sp.queryByTestId('spotify-preview')).toBeNull()
  })
})
