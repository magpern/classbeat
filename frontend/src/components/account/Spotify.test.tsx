import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SIGNED_IN, fakeApi, json, reason } from '../../test/fakeApi'
import { renderAt } from '../../test/render'

const goToProvider = vi.hoisted(() => vi.fn<(url: string) => void>())
vi.mock('../../lib/navigation', () => ({ goToProvider }))

const ACCOUNT_ROUTES = {
  'GET /api/auth/session': SIGNED_IN,
  'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
  'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
}
// The wording of ADR 0009 section 9, as the server sends it.
const CONSENT_TEXT =
  'classbeat manages one private working playlist in your Spotify account, named classbeat working copy, to ask for song candidates and have MOWL analyse them. It is overwritten on each pick; anyone with its link could open it.'
const CALLBACK = 'http://127.0.0.1:8000/oauth/spotify/callback'
const GOOD_ID = '0123456789abcdef0123456789ABCDEF'
const AUTHORIZE = 'https://accounts.example.test/authorize?state=opaque'

function status(overrides: Record<string, unknown> = {}) {
  return {
    status: 'not_connected',
    client_id: null,
    last_error: null,
    last_error_message: null,
    callback_url: CALLBACK,
    pool_consent: false,
    consent_version: null,
    consent_text: CONSENT_TEXT,
    song_picking_enabled: true,
    ...overrides,
  }
}

function spotifyApi(
  current: Record<string, unknown> = status(),
  routes: Record<string, unknown> = {},
) {
  return fakeApi({
    ...ACCOUNT_ROUTES,
    'GET /api/connections/spotify': () => current,
    ...(routes as Record<string, never>),
  })
}

beforeEach(() => {
  goToProvider.mockClear()
})

describe('feature switched off', () => {
  it('hides the card on the account page', async () => {
    fakeApi(ACCOUNT_ROUTES)
    renderAt('/account')
    await screen.findByRole('heading', { name: 'MOWL (Intelligent Cycling)' })
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Spotify' })).toBeNull()
    })
    expect(screen.queryByText('Set up Spotify')).toBeNull()
  })

  it('says so on the Spotify page instead of showing a wizard', async () => {
    fakeApi(ACCOUNT_ROUTES)
    renderAt('/account/spotify')
    expect(await screen.findByText('Song picking is not available right now.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Spotify Client ID')).toBeNull()
  })
})

describe('account page card', () => {
  it('links to the wizard when not connected and to management when connected', async () => {
    spotifyApi()
    renderAt('/account')
    expect(await screen.findByText('Set up Spotify')).toHaveAttribute('href', '/account/spotify')
    expect(screen.getByTestId('spotify-summary-status')).toHaveTextContent('Not connected')
  })

  it('shows a connected status', async () => {
    spotifyApi(status({ status: 'connected' }))
    renderAt('/account')
    expect(await screen.findByText('Manage Spotify')).toBeInTheDocument()
    expect(screen.getByTestId('spotify-summary-status')).toHaveTextContent('Connected')
  })
})

describe('the wizard', () => {
  it('walks through the dashboard steps with the Premium and allow-list sentences', async () => {
    spotifyApi()
    renderAt('/account/spotify')
    const steps = within((await screen.findByTestId('spotify-callback-url')).closest('ol')!)
      .getAllByRole('listitem')
      .map((item) => item.textContent)
    expect(steps).toHaveLength(5)
    const all = steps.join(' ')
    expect(all).toMatch(/owns the app needs Spotify Premium/)
    expect(all).toMatch(/allow-list of 5 people/)
    expect(all).toMatch(/User Management/)
    expect(all).toMatch(/Redirect URIs/)
    expect(screen.getByTestId('spotify-callback-url')).toHaveTextContent(CALLBACK)
  })

  it('copies the fixed callback URL', async () => {
    const writeText = vi.fn<(text: string) => Promise<void>>().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    spotifyApi()
    renderAt('/account/spotify')
    fireEvent.click(await screen.findByRole('button', { name: 'Copy callback URL' }))
    expect(await screen.findByText('Copied.')).toBeInTheDocument()
    expect(writeText).toHaveBeenCalledWith(CALLBACK)
  })

  it('says so when the clipboard is not available', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
    spotifyApi()
    renderAt('/account/spotify')
    fireEvent.click(await screen.findByRole('button', { name: 'Copy callback URL' }))
    expect(await screen.findByText(/Could not copy/)).toBeInTheDocument()
  })

  it('never offers a Client Secret field', async () => {
    spotifyApi()
    renderAt('/account/spotify')
    await screen.findByLabelText('Spotify Client ID')
    const inputs = Array.from(document.querySelectorAll('input, textarea, select'))
    expect(inputs).toHaveLength(1)
    for (const input of inputs) {
      const label = input.id ? (document.querySelector(`label[for="${input.id}"]`)?.textContent ?? '') : ''
      expect(`${input.getAttribute('name') ?? ''} ${label} ${input.getAttribute('type') ?? ''}`).not.toMatch(
        /secret|password/i,
      )
    }
    expect(screen.queryByLabelText(/secret/i)).toBeNull()
  })

  it('starts the connect with the trimmed Client ID and no other field, then leaves for Spotify', async () => {
    const api = spotifyApi(status(), {
      'POST /api/connections/spotify/start': { authorize_url: AUTHORIZE },
    })
    renderAt('/account/spotify')
    fireEvent.change(await screen.findByLabelText('Spotify Client ID'), {
      target: { value: `  ${GOOD_ID}  ` },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Connect Spotify' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalledWith(AUTHORIZE)
    })
    const [call] = api.to('POST', '/api/connections/spotify/start')
    expect(call?.body).toEqual({ client_id: GOOD_ID, confirm_account_change: false })
    expect(call?.headers.get('X-CSRF-Token')).toBe('csrf-session')
  })

  it('blocks an invalid Client ID without sending it or echoing it', async () => {
    const api = spotifyApi(status(), {
      'POST /api/connections/spotify/start': { authorize_url: AUTHORIZE },
    })
    renderAt('/account/spotify')
    const typed = 'not-a-client-id-PRIVATE-123'
    fireEvent.change(await screen.findByLabelText('Spotify Client ID'), { target: { value: typed } })
    fireEvent.click(screen.getByRole('button', { name: 'Connect Spotify' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('That does not look like a Spotify Client ID.')
    expect(document.body.textContent).not.toContain(typed)
    expect(api.to('POST', '/api/connections/spotify/start')).toHaveLength(0)
    expect(goToProvider).not.toHaveBeenCalled()
  })

  it.each(['', '   ', '0123456789abcdef0123456789abcde', '0123456789abcdef0123456789abcdefg'])(
    'blocks the Client ID %j',
    async (value) => {
      const api = spotifyApi()
      renderAt('/account/spotify')
      fireEvent.change(await screen.findByLabelText('Spotify Client ID'), { target: { value } })
      fireEvent.click(screen.getByRole('button', { name: 'Connect Spotify' }))
      expect(await screen.findByRole('alert')).toBeInTheDocument()
      expect(api.to('POST', '/api/connections/spotify/start')).toHaveLength(0)
    },
  )

  it('shows the same sentence, without the value, when the server refuses it', async () => {
    spotifyApi(status(), { 'POST /api/connections/spotify/start': reason(422, 'invalid_client_id') })
    renderAt('/account/spotify')
    fireEvent.change(await screen.findByLabelText('Spotify Client ID'), { target: { value: GOOD_ID } })
    fireEvent.click(screen.getByRole('button', { name: 'Connect Spotify' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('does not look like a Spotify Client ID')
    expect(document.body.textContent).not.toContain(GOOD_ID)
  })

  it('explains a rate limit on the start', async () => {
    spotifyApi(status(), {
      'POST /api/connections/spotify/start': json({ detail: { reason: 'rate_limited' } }, 429, {
        'Retry-After': '600',
      }),
    })
    renderAt('/account/spotify')
    fireEvent.change(await screen.findByLabelText('Spotify Client ID'), { target: { value: GOOD_ID } })
    fireEvent.click(screen.getByRole('button', { name: 'Connect Spotify' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Try again in 10 minutes.')
  })

  it('asks for the password again when the session is not recent, then starts', async () => {
    let confirmed = false
    const api = spotifyApi(status(), {
      'POST /api/auth/reauth': () => {
        confirmed = true
        return { status: 'ok' }
      },
      'POST /api/connections/spotify/start': () =>
        confirmed ? { authorize_url: AUTHORIZE } : reason(403, 'reauth_required'),
    })
    renderAt('/account/spotify')
    fireEvent.change(await screen.findByLabelText('Spotify Client ID'), { target: { value: GOOD_ID } })
    fireEvent.click(screen.getByRole('button', { name: 'Connect Spotify' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(await within(dialog).findByLabelText('Password'), { target: { value: 'pw' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm with password' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalledWith(AUTHORIZE)
    })
    expect(api.to('POST', '/api/connections/spotify/start')).toHaveLength(2)
  })
})

describe('the callback result in the query', () => {
  it('shows connected', async () => {
    spotifyApi(status({ status: 'connected' }))
    renderAt('/account/spotify?result=connected')
    expect(await screen.findByText('Spotify is connected.')).toBeInTheDocument()
    expect(screen.getByTestId('spotify-status')).toHaveTextContent('Connected')
  })

  // ADR 0009 section 8, every category and its sentence.
  it.each([
    ['invalid_grant', '', 'Spotify no longer accepts the connection. Reconnect Spotify.'],
    [
      'app_misconfigured',
      '',
      'Spotify does not recognise this app setup. Check the Client ID and the redirect URI in your Spotify app.',
    ],
    [
      'not_allowlisted',
      '',
      "Spotify refused this account. In your Spotify app's User Management add this account's name and email (Development Mode allows 5 people), and make sure the app's owner has Spotify Premium.",
    ],
    ['forbidden', '', 'Spotify refused this request for your account or playlist.'],
    ['rate_limited', '&retry_after=600', 'Spotify asks to slow down; trying again in about 10 minutes.'],
    ['rate_limited', '&retry_after=60', 'Spotify asks to slow down; trying again in about 1 minute.'],
    ['rate_limited', '', 'Spotify asks to slow down; trying again in about 1 minute.'],
    [
      'quota_exhausted',
      '&retry_after=7200',
      'Your Spotify app has used up its allowance for now. Try again in about 2 hours.',
    ],
    [
      'quota_exhausted',
      '&retry_after=3601',
      'Your Spotify app has used up its allowance for now. Try again in about 2 hours.',
    ],
    ['upstream_unavailable', '', 'Spotify is not answering.'],
    ['not_found', '', 'Spotify could not find that playlist.'],
    [
      'scope_mismatch',
      '',
      'Spotify did not give the access classbeat needs. Connect again and allow it.',
    ],
    [
      'invalid_response',
      '',
      'Spotify did not give the access classbeat needs. Connect again and allow it.',
    ],
    [
      'account_changed',
      '',
      'This is a different Spotify account than before. Confirm to switch (classbeat forgets the working playlist).',
    ],
    ['access_denied', '', 'Spotify was not connected.'],
  ])('shows %s%s', async (category, extra, sentence) => {
    spotifyApi()
    renderAt(`/account/spotify?result=${category}${extra}`)
    expect(await screen.findByRole('alert')).toHaveTextContent(sentence)
  })

  it('never shows an unknown result code or a bad retry hint', async () => {
    spotifyApi()
    renderAt('/account/spotify?result=%3Cb%3Eweird%3C%2Fb%3E&retry_after=abc')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Something went wrong with Spotify. Try again later.',
    )
    expect(screen.queryByText(/weird/)).toBeNull()
    expect(document.querySelector('b')).toBeNull()
  })

  it('offers the confirmation after account_changed and sends it', async () => {
    const api = spotifyApi(status({ status: 'connected' }), {
      'POST /api/connections/spotify/start': { authorize_url: AUTHORIZE },
    })
    renderAt('/account/spotify?result=account_changed')
    fireEvent.change(await screen.findByLabelText('Spotify Client ID'), { target: { value: GOOD_ID } })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm and switch account' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalled()
    })
    const [call] = api.to('POST', '/api/connections/spotify/start')
    expect(call?.body).toEqual({ client_id: GOOD_ID, confirm_account_change: true })
  })
})

describe('the connection card', () => {
  it.each([
    ['invalid_grant', 'Spotify no longer accepts the connection. Reconnect Spotify.'],
    ['upstream_unavailable', 'Spotify is not answering.'],
    ['access_denied', 'Spotify was not connected.'],
  ])("shows the server's sentence for last_error %s", async (category, sentence) => {
    spotifyApi(status({ status: 'connected', last_error: category, last_error_message: sentence }))
    renderAt('/account/spotify')
    expect(await screen.findByTestId('spotify-last-error')).toHaveTextContent(sentence)
  })

  it('falls back to its own sentence, and never prints an unknown code', async () => {
    spotifyApi(status({ status: 'connected', last_error: 'forbidden' }))
    renderAt('/account/spotify')
    expect(await screen.findByTestId('spotify-last-error')).toHaveTextContent(
      'Spotify refused this request for your account or playlist.',
    )
  })

  it('escapes whatever the server sends', async () => {
    spotifyApi(
      status({ status: 'connected', last_error: 'x', last_error_message: '<img src=x onerror=alert(1)>' }),
    )
    renderAt('/account/spotify')
    expect(await screen.findByTestId('spotify-last-error')).toHaveTextContent('<img src=x')
    expect(document.querySelector('img')).toBeNull()
  })

  it('offers to connect again when it needs reconnecting', async () => {
    spotifyApi(
      status({
        status: 'needs_reconnect',
        last_error: 'invalid_grant',
        last_error_message: 'Spotify no longer accepts the connection. Reconnect Spotify.',
      }),
    )
    renderAt('/account/spotify')
    expect(await screen.findByTestId('spotify-status')).toHaveTextContent('Needs to be connected again')
    expect(screen.getByRole('button', { name: 'Connect Spotify again' })).toBeInTheDocument()
    expect(screen.queryByText('Working playlist')).toBeNull()
  })

  it('does not render the Client ID, ids or tokens it was given', async () => {
    spotifyApi(status({ status: 'connected', client_id: GOOD_ID }))
    renderAt('/account/spotify')
    await screen.findByTestId('spotify-status')
    expect(document.body.textContent).not.toContain(GOOD_ID)
  })

  it('states what Disconnect deletes, asks first, then disconnects', async () => {
    let connected = true
    const api = spotifyApi(status({ status: 'connected' }), {
      'GET /api/connections/spotify': () => status({ status: connected ? 'connected' : 'not_connected' }),
      'DELETE /api/connections/spotify': () => {
        connected = false
        return new Response(null, { status: 204 })
      },
    })
    renderAt('/account/spotify')
    fireEvent.click(await screen.findByRole('button', { name: 'Disconnect Spotify' }))
    const dialog = screen.getByRole('group', { name: 'Confirm disconnect' })
    expect(dialog).toHaveTextContent('the stored sign-in')
    expect(dialog).toHaveTextContent('the working playlist setting and your consent for it')
    expect(dialog).toHaveTextContent('left undecided')
    expect(dialog).toHaveTextContent('Playlists already in your Spotify stay there')
    expect(dialog).toHaveTextContent('remove classbeat’s access under Apps in your Spotify account settings')
    expect(api.to('DELETE', '/api/connections/spotify')).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes, disconnect Spotify' }))
    expect(await screen.findByText(/Spotify is disconnected/)).toBeInTheDocument()
    expect(api.to('DELETE', '/api/connections/spotify')).toHaveLength(1)
    expect(await screen.findByRole('button', { name: 'Connect Spotify' })).toBeInTheDocument()
  })

  it('keeps the connection when Disconnect is cancelled', async () => {
    const api = spotifyApi(status({ status: 'connected' }))
    renderAt('/account/spotify')
    fireEvent.click(await screen.findByRole('button', { name: 'Disconnect Spotify' }))
    fireEvent.click(screen.getByRole('button', { name: 'Keep it connected' }))
    expect(screen.getByRole('button', { name: 'Disconnect Spotify' })).toBeInTheDocument()
    expect(api.to('DELETE', '/api/connections/spotify')).toHaveLength(0)
  })

  it('states what a Client ID change deletes before it shows the field, then starts', async () => {
    const api = spotifyApi(status({ status: 'connected' }), {
      'POST /api/connections/spotify/start': { authorize_url: AUTHORIZE },
    })
    renderAt('/account/spotify')
    fireEvent.click(await screen.findByRole('button', { name: 'Change Client ID' }))
    const dialog = screen.getByRole('group', { name: 'Confirm Client ID change' })
    expect(dialog).toHaveTextContent('keeps using the current connection until the new Client ID has worked')
    expect(dialog).toHaveTextContent('forgets the working playlist setting')
    expect(dialog).toHaveTextContent('asked for your consent again')
    expect(dialog).toHaveTextContent('the playlist stays in your Spotify')
    expect(dialog).toHaveTextContent('a different Spotify account')
    expect(screen.queryByLabelText('Spotify Client ID')).toBeNull()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }))
    fireEvent.change(await screen.findByLabelText('Spotify Client ID'), { target: { value: GOOD_ID } })
    fireEvent.click(screen.getByRole('button', { name: 'Connect Spotify' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalledWith(AUTHORIZE)
    })
    expect(api.to('POST', '/api/connections/spotify/start')).toHaveLength(1)
  })

  it('leaves the Client ID alone when the change is cancelled', async () => {
    const api = spotifyApi(status({ status: 'connected' }))
    renderAt('/account/spotify')
    fireEvent.click(await screen.findByRole('button', { name: 'Change Client ID' }))
    fireEvent.click(screen.getByRole('button', { name: 'Keep the current Client ID' }))
    expect(screen.queryByLabelText('Spotify Client ID')).toBeNull()
    expect(api.to('POST', '/api/connections/spotify/start')).toHaveLength(0)
  })
})

describe('the pool consent', () => {
  it('is not offered before Spotify is connected', async () => {
    spotifyApi()
    renderAt('/account/spotify')
    await screen.findByLabelText('Spotify Client ID')
    expect(screen.queryByRole('switch', { name: 'Allow the working playlist' })).toBeNull()
  })

  it('shows the exact wording with the link-sharing sentence and records the consent', async () => {
    let given = false
    const api = spotifyApi(status({ status: 'connected' }), {
      'GET /api/connections/spotify': () =>
        status({ status: 'connected', pool_consent: given, consent_version: given ? 1 : null }),
      'PUT /api/connections/spotify/pool-consent': () => {
        given = true
        return new Response(null, { status: 204 })
      },
    })
    renderAt('/account/spotify')
    const quote = await screen.findByTestId('spotify-consent-text')
    expect(quote.textContent).toBe(CONSENT_TEXT)
    expect(quote).toHaveTextContent('anyone with its link could open it')
    expect(screen.getByTestId('spotify-consent-state')).toHaveTextContent('not allowed it yet')
    expect(api.to('PUT', '/api/connections/spotify/pool-consent')).toHaveLength(0)
    fireEvent.click(screen.getByRole('switch', { name: 'Allow the working playlist' }))
    expect(await screen.findByText(/classbeat may use the working playlist/)).toBeInTheDocument()
    expect(screen.getByTestId('spotify-consent-state')).toHaveTextContent('You have allowed')
    const [call] = api.to('PUT', '/api/connections/spotify/pool-consent')
    expect(call?.headers.get('X-CSRF-Token')).toBe('csrf-session')
  })

  it('revokes after saying the playlist stays in Spotify', async () => {
    let given = true
    const api = spotifyApi(status({ status: 'connected', pool_consent: true, consent_version: 1 }), {
      'GET /api/connections/spotify': () =>
        status({ status: 'connected', pool_consent: given, consent_version: given ? 1 : null }),
      'DELETE /api/connections/spotify/pool-consent': () => {
        given = false
        return new Response(null, { status: 204 })
      },
    })
    renderAt('/account/spotify')
    fireEvent.click(await screen.findByRole('switch', { name: 'Allow the working playlist' }))
    const dialog = screen.getByRole('group', { name: 'Confirm revoke' })
    expect(dialog).toHaveTextContent('The playlist stays in your Spotify')
    expect(dialog).toHaveTextContent('delete it there')
    expect(dialog).toHaveTextContent('the next pick creates a new working playlist')
    expect(api.to('DELETE', '/api/connections/spotify/pool-consent')).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes, revoke my consent' }))
    expect(await screen.findByText(/Your consent is withdrawn/)).toBeInTheDocument()
    expect(api.to('DELETE', '/api/connections/spotify/pool-consent')).toHaveLength(1)
    expect(await screen.findByRole('switch', { name: 'Allow the working playlist' })).toBeInTheDocument()
  })

  it('keeps the consent when the revoke is cancelled', async () => {
    const api = spotifyApi(status({ status: 'connected', pool_consent: true, consent_version: 1 }))
    renderAt('/account/spotify')
    fireEvent.click(await screen.findByRole('switch', { name: 'Allow the working playlist' }))
    fireEvent.click(screen.getByRole('button', { name: 'Keep my consent' }))
    expect(screen.getByRole('switch', { name: 'Allow the working playlist' })).toBeInTheDocument()
    expect(api.to('DELETE', '/api/connections/spotify/pool-consent')).toHaveLength(0)
  })

  it('shows an error when the consent is refused', async () => {
    spotifyApi(status({ status: 'connected' }), {
      'PUT /api/connections/spotify/pool-consent': reason(409, 'not_connected'),
    })
    renderAt('/account/spotify')
    fireEvent.click(await screen.findByRole('switch', { name: 'Allow the working playlist' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})
