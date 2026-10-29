import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SIGNED_IN, fakeApi, json, reason } from '../../test/fakeApi'
import { renderAt } from '../../test/render'

const ACCOUNT_ROUTES = {
  'GET /api/auth/session': SIGNED_IN,
  'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
  'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
}
const CONNECTED = {
  id: 'c0000000-0000-4000-8000-000000000001',
  provider: 'mowl',
  status: 'connected',
  last_error: null,
  connected_at: '2026-10-28T10:00:00Z',
  last_used_at: null,
  expires_at: null,
}

async function mowlSection() {
  const heading = await screen.findByRole('heading', { name: 'MOWL (Intelligent Cycling)' })
  const section = heading.closest('section')
  if (section === null) throw new Error('no section')
  return within(section)
}

async function fillAndConnect(password = 'mowl-secret-pw') {
  const mowl = await mowlSection()
  fireEvent.change(await mowl.findByLabelText('MOWL email'), {
    target: { value: ' rider@example.test ' },
  })
  fireEvent.change(mowl.getByLabelText('MOWL password'), { target: { value: password } })
  fireEvent.click(mowl.getByRole('button', { name: 'Connect MOWL' }))
  return mowl
}

describe('MOWL connection on the account page', () => {
  it('connects with email and password in the body, then shows the status', async () => {
    const api = fakeApi({ ...ACCOUNT_ROUTES, 'POST /api/connections/mowl': CONNECTED })
    renderAt('/account')
    const mowl = await fillAndConnect()
    expect(await mowl.findByText('MOWL is connected.')).toBeInTheDocument()
    expect(mowl.getByTestId('mowl-status')).toHaveTextContent('Connected')
    expect(mowl.queryByLabelText('MOWL password')).toBeNull()
    const [call] = api.to('POST', '/api/connections/mowl')
    expect(call?.body).toEqual({ email: 'rider@example.test', password: 'mowl-secret-pw' })
    expect(call?.headers.get('X-CSRF-Token')).toBe('csrf-session')
    // The password goes in the JSON body only: no URL carries it.
    expect(api.calls.every((c) => !c.path.includes('mowl-secret-pw'))).toBe(true)
  })

  it('explains a refused sign-in and clears the password field', async () => {
    fakeApi({
      ...ACCOUNT_ROUTES,
      'POST /api/connections/mowl': reason(400, 'invalid_credentials'),
    })
    renderAt('/account')
    const mowl = await fillAndConnect()
    expect(await mowl.findByRole('alert')).toHaveTextContent(
      'MOWL did not accept that email and password.',
    )
    expect(mowl.getByLabelText('MOWL password')).toHaveValue('')
    expect(mowl.getByLabelText('MOWL email')).toHaveValue('rider@example.test')
  })

  it.each([
    [reason(503, 'upstream_unavailable'), 'MOWL could not be reached.'],
    [reason(502, 'unknown'), 'MOWL answered in a way classbeat did not expect.'],
    [json({ detail: { reason: 'rate_limited' } }, 429, { 'Retry-After': '600' }), 'Try again in 10 minutes.'],
  ])('shows the error category in plain words (%#)', async (answer, text) => {
    fakeApi({ ...ACCOUNT_ROUTES, 'POST /api/connections/mowl': answer })
    renderAt('/account')
    const mowl = await fillAndConnect()
    expect(await mowl.findByRole('alert')).toHaveTextContent(text)
  })

  it('asks for the password again when the session is not recent, then connects', async () => {
    let confirmed = false
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      'POST /api/auth/reauth': () => {
        confirmed = true
        return { status: 'ok' }
      },
      'POST /api/connections/mowl': () =>
        confirmed ? CONNECTED : reason(403, 'reauth_required'),
    })
    renderAt('/account')
    const mowl = await fillAndConnect()
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(await within(dialog).findByLabelText('Password'), {
      target: { value: 'classbeat-pw' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm with password' }))
    expect(await mowl.findByText('MOWL is connected.')).toBeInTheDocument()
    expect(api.to('POST', '/api/connections/mowl')).toHaveLength(2)
  })

  it('shows a connection that needs reconnecting, with its reason and the form', async () => {
    fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': {
        connections: [{ ...CONNECTED, status: 'needs_reconnect', last_error: 'needs_reconnect' }],
      },
    })
    renderAt('/account')
    const mowl = await mowlSection()
    expect(await mowl.findByTestId('mowl-status')).toHaveTextContent('Needs to be connected again')
    expect(mowl.getByText(/no longer accepts the stored sign-in/)).toBeInTheDocument()
    expect(mowl.getByRole('button', { name: 'Connect MOWL again' })).toBeInTheDocument()
    // Connecting again in place forgets the playlists classbeat added (ADR 0006 section 3).
    expect(mowl.getByTestId('reconnect-warning')).toHaveTextContent(/undecided.*second time/)
  })

  it('disconnects and offers the form again', async () => {
    let connected = true
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': () => ({ connections: connected ? [CONNECTED] : [] }),
      'DELETE /api/connections/mowl': () => {
        connected = false
        return new Response(null, { status: 204 })
      },
    })
    renderAt('/account')
    const mowl = await mowlSection()
    fireEvent.click(await mowl.findByRole('button', { name: 'Disconnect MOWL' }))
    fireEvent.click(mowl.getByRole('button', { name: 'Yes, disconnect MOWL' }))
    expect(await mowl.findByText(/MOWL is disconnected/)).toBeInTheDocument()
    await waitFor(() => {
      expect(mowl.getByRole('button', { name: 'Connect MOWL' })).toBeInTheDocument()
    })
    expect(api.to('DELETE', '/api/connections/mowl')).toHaveLength(1)
  })

  it('warns before disconnecting that undecided attaches are forgotten and asks first', async () => {
    const api = fakeApi({ ...ACCOUNT_ROUTES, 'GET /api/connections': { connections: [CONNECTED] } })
    renderAt('/account')
    const mowl = await mowlSection()
    fireEvent.click(await mowl.findByRole('button', { name: 'Disconnect MOWL' }))
    expect(mowl.getByText(/any adding that was left undecided/)).toBeInTheDocument()
    expect(mowl.getByText(/a second time/)).toBeInTheDocument()
    expect(api.to('DELETE', '/api/connections/mowl')).toHaveLength(0)
    fireEvent.click(mowl.getByRole('button', { name: 'Keep it connected' }))
    expect(mowl.getByRole('button', { name: 'Disconnect MOWL' })).toBeInTheDocument()
    expect(api.to('DELETE', '/api/connections/mowl')).toHaveLength(0)
  })

  it('never shows an unknown category code', async () => {
    fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': { connections: [{ ...CONNECTED, last_error: '<b>weird</b>' }] },
    })
    renderAt('/account')
    const mowl = await mowlSection()
    expect(await mowl.findByText('The last use of this connection failed.')).toBeInTheDocument()
    expect(mowl.queryByText(/weird/)).toBeNull()
  })
})
