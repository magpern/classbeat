import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SIGNED_IN, fakeApi, json, reason } from '../../test/fakeApi'
import { renderAt } from '../../test/render'

const ACCOUNT_ROUTES = {
  'GET /api/auth/session': SIGNED_IN,
  'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
  'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
}
const ON = { enabled: true, available: true }
const CONNECTED = {
  id: 'c0000000-0000-4000-8000-000000000002',
  provider: 'swedebeat',
  status: 'connected',
  last_error: null,
  connected_at: '2026-10-31T10:00:00Z',
  last_used_at: null,
  expires_at: null,
  account: 'rider@example.test',
}
const PASSWORD = 'swedebeat-secret-pw'

function list(swedebeat: unknown, connections: unknown[] = []) {
  return { connections, swedebeat }
}

async function card() {
  const heading = await screen.findByRole('heading', { name: 'Swedebeat' })
  const section = heading.closest('section')
  if (section === null) throw new Error('no section')
  return within(section)
}

async function fillAndConnect(password = PASSWORD) {
  const swedebeat = await card()
  fireEvent.change(await swedebeat.findByLabelText('Swedebeat email'), {
    target: { value: ' rider@example.test ' },
  })
  fireEvent.change(swedebeat.getByLabelText('Swedebeat password'), { target: { value: password } })
  return swedebeat
}

describe('Swedebeat connection on the account page', () => {
  it('says it is coming soon while the flag is off, with no form', async () => {
    fakeApi({ ...ACCOUNT_ROUTES, 'GET /api/connections': list({ enabled: false, available: true }) })
    renderAt('/account')
    const swedebeat = await card()
    expect(await swedebeat.findByText('Swedebeat is coming soon.')).toBeInTheDocument()
    expect(swedebeat.queryByLabelText('Swedebeat email')).toBeNull()
    expect(swedebeat.queryByRole('button')).toBeNull()
  })

  it('says it is not available on this server when the flag is on but the key is missing', async () => {
    fakeApi({ ...ACCOUNT_ROUTES, 'GET /api/connections': list({ enabled: true, available: false }) })
    renderAt('/account')
    const swedebeat = await card()
    expect(await swedebeat.findByText('Swedebeat sign-in is not available on this server.')).toBeInTheDocument()
    expect(swedebeat.queryByLabelText('Swedebeat password')).toBeNull()
  })

  it('connects with the password in the body only, shows the email, and clears the field', async () => {
    let connected = false
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': () => list(ON, connected ? [CONNECTED] : []),
      'POST /api/connections/swedebeat': () => {
        connected = true
        return CONNECTED
      },
    })
    renderAt('/account')
    const swedebeat = await fillAndConnect()
    fireEvent.click(swedebeat.getByRole('button', { name: 'Connect Swedebeat' }))
    expect(await swedebeat.findByText('Swedebeat is connected.')).toBeInTheDocument()
    expect(swedebeat.getByTestId('swedebeat-status')).toHaveTextContent('Connected')
    expect(swedebeat.getByTestId('swedebeat-account')).toHaveTextContent('rider@example.test')
    expect(swedebeat.queryByLabelText('Swedebeat password')).toBeNull()
    const [call] = api.to('POST', '/api/connections/swedebeat')
    expect(call?.body).toEqual({ email: 'rider@example.test', password: PASSWORD, force: false })
    expect(call?.headers.get('X-CSRF-Token')).toBe('csrf-session')
    // The password goes in the JSON body only: no URL carries it, and it is not kept on the page.
    expect(api.calls.every((c) => !c.path.includes(PASSWORD))).toBe(true)
    expect(document.body.innerHTML).not.toContain(PASSWORD)
    expect(JSON.stringify({ ...localStorage })).not.toContain(PASSWORD)
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(PASSWORD)
  })

  it('explains a refused sign-in and clears the password field', async () => {
    fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': list(ON),
      'POST /api/connections/swedebeat': reason(400, 'invalid_credentials'),
    })
    renderAt('/account')
    const swedebeat = await fillAndConnect()
    fireEvent.click(swedebeat.getByRole('button', { name: 'Connect Swedebeat' }))
    expect(await swedebeat.findByRole('alert')).toHaveTextContent('Swedebeat did not accept that email and password.')
    expect(swedebeat.getByLabelText('Swedebeat password')).toHaveValue('')
    expect(swedebeat.getByLabelText('Swedebeat email')).toHaveValue('rider@example.test')
  })

  it.each([
    [reason(503, 'upstream_unavailable'), 'Swedebeat could not be reached.'],
    [reason(503, 'app_key_unavailable'), 'Swedebeat sign-in is not available on this server.'],
    [reason(502, 'unknown'), 'Swedebeat answered in a way classbeat did not expect.'],
    [reason(404, 'feature_off'), 'Swedebeat is not switched on right now.'],
    [json({ detail: { reason: 'rate_limited' } }, 429, { 'Retry-After': '600' }), 'Try again in 10 minutes.'],
  ])('shows the error category in plain words (%#)', async (answer, text) => {
    fakeApi({ ...ACCOUNT_ROUTES, 'GET /api/connections': list(ON), 'POST /api/connections/swedebeat': answer })
    renderAt('/account')
    const swedebeat = await fillAndConnect()
    fireEvent.click(swedebeat.getByRole('button', { name: 'Connect Swedebeat' }))
    expect(await swedebeat.findByRole('alert')).toHaveTextContent(text)
  })

  it('asks before signing another device out, then sends force only after the user chose it', async () => {
    let attempts = 0
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': list(ON),
      'POST /api/connections/swedebeat': () => {
        attempts += 1
        return attempts === 1 ? reason(409, 'device_in_use') : CONNECTED
      },
    })
    renderAt('/account')
    const swedebeat = await fillAndConnect()
    fireEvent.click(swedebeat.getByRole('button', { name: 'Connect Swedebeat' }))
    expect(await swedebeat.findByRole('alert')).toHaveTextContent('signed in on another device')
    expect(swedebeat.getByLabelText('Swedebeat password')).toHaveValue('')
    expect(swedebeat.getByTestId('swedebeat-device-warning')).toBeInTheDocument()
    fireEvent.change(swedebeat.getByLabelText('Swedebeat password'), { target: { value: PASSWORD } })
    fireEvent.click(swedebeat.getByRole('button', { name: 'Sign in here anyway' }))
    expect(await swedebeat.findByText('Swedebeat is connected.')).toBeInTheDocument()
    expect(api.to('POST', '/api/connections/swedebeat').map((c) => (c.body as { force: boolean }).force)).toEqual([
      false,
      true,
    ])
  })

  it('drops the force answer when the email is edited after a device-in-use answer', async () => {
    let attempts = 0
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': list(ON),
      'POST /api/connections/swedebeat': () => {
        attempts += 1
        return attempts === 1 ? reason(409, 'device_in_use') : CONNECTED
      },
    })
    renderAt('/account')
    const swedebeat = await fillAndConnect()
    fireEvent.click(swedebeat.getByRole('button', { name: 'Connect Swedebeat' }))
    await swedebeat.findByTestId('swedebeat-device-warning')
    fireEvent.change(swedebeat.getByLabelText('Swedebeat email'), { target: { value: 'other@example.test' } })
    await waitFor(() => {
      expect(swedebeat.queryByTestId('swedebeat-device-warning')).toBeNull()
    })
    fireEvent.change(swedebeat.getByLabelText('Swedebeat password'), { target: { value: PASSWORD } })
    fireEvent.click(swedebeat.getByRole('button', { name: 'Connect Swedebeat' }))
    expect(await swedebeat.findByText('Swedebeat is connected.')).toBeInTheDocument()
    expect(api.to('POST', '/api/connections/swedebeat').map((c) => (c.body as { force: boolean }).force)).toEqual([
      false,
      false,
    ])
  })

  it('goes back to a normal sign-in when the user keeps the other device signed in', async () => {
    fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': list(ON),
      'POST /api/connections/swedebeat': reason(409, 'device_in_use'),
    })
    renderAt('/account')
    const swedebeat = await fillAndConnect()
    fireEvent.click(swedebeat.getByRole('button', { name: 'Connect Swedebeat' }))
    await swedebeat.findByRole('alert')
    fireEvent.click(swedebeat.getByRole('button', { name: 'Keep the other device signed in' }))
    expect(swedebeat.getByRole('button', { name: 'Connect Swedebeat' })).toBeInTheDocument()
    expect(swedebeat.queryByTestId('swedebeat-device-warning')).toBeNull()
  })

  it('shows a connection that needs to be made again, with the form', async () => {
    fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': list(ON, [{ ...CONNECTED, status: 'needs_reconnect', last_error: 'needs_reconnect' }]),
    })
    renderAt('/account')
    const swedebeat = await card()
    expect(await swedebeat.findByTestId('swedebeat-status')).toHaveTextContent('Needs to be connected again')
    expect(swedebeat.getByText('Swedebeat no longer accepts the stored sign-in. Connect again.')).toBeInTheDocument()
    expect(swedebeat.getByRole('button', { name: 'Connect Swedebeat again' })).toBeInTheDocument()
  })

  it('disconnects after a confirmation, also while the flag is off', async () => {
    let gone = false
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': () => list({ enabled: false, available: true }, gone ? [] : [CONNECTED]),
      'DELETE /api/connections/swedebeat': () => {
        gone = true
        return new Response(null, { status: 204 })
      },
    })
    renderAt('/account')
    const swedebeat = await card()
    fireEvent.click(await swedebeat.findByRole('button', { name: 'Disconnect Swedebeat' }))
    expect(api.to('DELETE', '/api/connections/swedebeat')).toHaveLength(0) // asks first
    fireEvent.click(swedebeat.getByRole('button', { name: 'Yes, disconnect Swedebeat' }))
    await waitFor(() => {
      expect(api.to('DELETE', '/api/connections/swedebeat')).toHaveLength(1)
    })
    expect(await swedebeat.findByText(/Swedebeat is disconnected/)).toBeInTheDocument()
    expect(swedebeat.queryByTestId('swedebeat-status')).toBeNull()
  })

  it('keeps the connection when the user backs out of the confirmation', async () => {
    const api = fakeApi({ ...ACCOUNT_ROUTES, 'GET /api/connections': list(ON, [CONNECTED]) })
    renderAt('/account')
    const swedebeat = await card()
    fireEvent.click(await swedebeat.findByRole('button', { name: 'Disconnect Swedebeat' }))
    fireEvent.click(swedebeat.getByRole('button', { name: 'Keep it connected' }))
    expect(swedebeat.getByRole('button', { name: 'Disconnect Swedebeat' })).toBeInTheDocument()
    expect(api.to('DELETE', '/api/connections/swedebeat')).toHaveLength(0)
  })

  it('never has a field for anything but the Swedebeat email and password', async () => {
    fakeApi({ ...ACCOUNT_ROUTES, 'GET /api/connections': list(ON) })
    renderAt('/account')
    const swedebeat = await card()
    await swedebeat.findByLabelText('Swedebeat email')
    expect(swedebeat.getAllByRole('textbox')).toHaveLength(1)
    expect(swedebeat.getByLabelText('Swedebeat password')).toHaveAttribute('type', 'password')
    expect(swedebeat.getByLabelText('Swedebeat password')).toHaveAttribute('autocomplete', 'current-password')
  })
})
