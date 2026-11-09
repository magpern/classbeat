import { fireEvent, screen, waitFor, within, type BoundFunctions, type queries } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SIGNED_IN, fakeApi, json, reason } from '../../../test/fakeApi'
import { renderAt } from '../../../test/render'

// Swedebeat in the Connections list (ADR 0028 section 1, item 2; ADR 0015 and its #525 amendment):
// always listed, optional; Connect stores the preference then connects; a dialog that ends without
// a connection puts the preference back.

const ACCOUNT_ROUTES = {
  'GET /api/auth/session': SIGNED_IN,
  'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
  'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
}
const PREFERENCE = 'PUT /api/connections/swedebeat/preference'
const POST = 'POST /api/connections/swedebeat'
const ON = { enabled: true, available: true, uses_swedebeat: false }
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

const list = (swedebeat: unknown, connections: unknown[] = []) => ({ connections, swedebeat })
const stored = (body: unknown) => ({ uses_swedebeat: (body as { uses_swedebeat: boolean }).uses_swedebeat })

async function openDialog() {
  fireEvent.click(await screen.findByRole('button', { name: 'Connect Swedebeat' }))
  return within(await screen.findByRole('dialog', { name: 'Connect Swedebeat' }))
}

type Dialog = BoundFunctions<typeof queries>

async function fill(dialog: Dialog, password = PASSWORD) {
  fireEvent.change(await dialog.findByLabelText('Swedebeat email'), { target: { value: ' rider@example.test ' } })
  fireEvent.change(dialog.getByLabelText('Swedebeat password'), { target: { value: password } })
}

describe('the Swedebeat row', () => {
  it('is always listed as optional, with Connect, even when the user has not opted in', async () => {
    fakeApi({ ...ACCOUNT_ROUTES, 'GET /api/connections': list(ON) })
    renderAt('/account')
    const row = await screen.findByTestId('connection-swedebeat')
    expect(row).toHaveTextContent('Optional')
    expect(row).toHaveTextContent('Matches songs to your Swedebeat mixes')
    expect(within(row).getByRole('button', { name: 'Connect Swedebeat' })).toBeInTheDocument()
    expect(screen.queryByRole('switch', { name: 'I use Swedebeat' })).toBeNull()
  })

  it.each([
    ['the flag is off', { enabled: false, available: true, uses_swedebeat: false }],
    ['the server holds no app key', { enabled: true, available: false, uses_swedebeat: false }],
  ])('says it is not available, with no button, when %s', async (_name, access) => {
    const api = fakeApi({ ...ACCOUNT_ROUTES, 'GET /api/connections': list(access) })
    renderAt('/account')
    const row = await screen.findByTestId('connection-swedebeat')
    expect(row).toHaveTextContent('Not available right now')
    expect(within(row).queryByRole('button')).toBeNull()
    expect(api.to('PUT', '/api/connections/swedebeat/preference')).toHaveLength(0)
  })

  it('keeps Disconnect for a stored connection even when the flag is off', async () => {
    fakeApi({ ...ACCOUNT_ROUTES, 'GET /api/connections': list({ ...ON, enabled: false }, [CONNECTED]) })
    renderAt('/account')
    const row = await screen.findByTestId('connection-swedebeat')
    expect(within(row).getByRole('button', { name: 'Disconnect Swedebeat' })).toBeInTheDocument()
    expect(within(row).getByTestId('swedebeat-account')).toHaveTextContent('rider@example.test')
  })
})

describe('connect Swedebeat', () => {
  it('stores the preference, then sends the password once in the body, and shows the account', async () => {
    let connected = false
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': () => list(ON, connected ? [CONNECTED] : []),
      [PREFERENCE]: stored,
      [POST]: () => {
        connected = true
        return CONNECTED
      },
    })
    renderAt('/account')
    const dialog = await openDialog()
    await fill(dialog)
    fireEvent.click(dialog.getByRole('button', { name: 'Connect Swedebeat' }))
    expect(await screen.findByText('Swedebeat is connected.')).toBeInTheDocument()
    expect(screen.getByTestId('swedebeat-account')).toHaveTextContent('rider@example.test')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(api.to('PUT', '/api/connections/swedebeat/preference').map((c) => c.body)).toEqual([{ uses_swedebeat: true }])
    const [call] = api.to('POST', '/api/connections/swedebeat')
    expect(call?.body).toEqual({ email: 'rider@example.test', password: PASSWORD, force: false })
    expect(call?.headers.get('X-CSRF-Token')).toBe('csrf-session')
    expect(api.calls.every((c) => !c.path.includes(PASSWORD))).toBe(true)
    expect(document.body.innerHTML).not.toContain(PASSWORD)
  })

  it('explains a refusal, clears the password, and puts the preference back when the dialog closes', async () => {
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': list(ON),
      [PREFERENCE]: stored,
      [POST]: reason(400, 'invalid_credentials'),
    })
    renderAt('/account')
    const dialog = await openDialog()
    await fill(dialog)
    fireEvent.click(dialog.getByRole('button', { name: 'Connect Swedebeat' }))
    expect(await dialog.findByRole('alert')).toHaveTextContent('Swedebeat did not accept that email and password.')
    expect(dialog.getByLabelText('Swedebeat password')).toHaveValue('')
    fireEvent.click(dialog.getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(api.to('PUT', '/api/connections/swedebeat/preference').map((c) => c.body)).toEqual([
        { uses_swedebeat: true },
        { uses_swedebeat: false },
      ])
    })
  })

  it('puts the preference back after a failed try that follows a connect and a disconnect in the same visit', async () => {
    let connected = false
    let accept = true
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': () => list({ ...ON, uses_swedebeat: connected }, connected ? [CONNECTED] : []),
      'DELETE /api/connections/swedebeat': () => {
        connected = false
        return new Response(null, { status: 204 })
      },
      [PREFERENCE]: stored,
      [POST]: () => {
        if (!accept) return reason(400, 'invalid_credentials')
        connected = true
        return CONNECTED
      },
    })
    renderAt('/account')
    let dialog = await openDialog()
    await fill(dialog)
    fireEvent.click(dialog.getByRole('button', { name: 'Connect Swedebeat' }))
    expect(await screen.findByText('Swedebeat is connected.')).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: 'Disconnect Swedebeat' }))
    const confirm = within(await screen.findByRole('dialog', { name: 'Disconnect Swedebeat?' }))
    fireEvent.click(confirm.getByRole('button', { name: 'Yes, disconnect Swedebeat' }))
    expect(await screen.findByText(/Swedebeat is disconnected/)).toBeInTheDocument()
    accept = false
    dialog = await openDialog()
    await fill(dialog)
    fireEvent.click(dialog.getByRole('button', { name: 'Connect Swedebeat' }))
    expect(await dialog.findByRole('alert')).toBeInTheDocument()
    fireEvent.click(dialog.getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(api.to('PUT', '/api/connections/swedebeat/preference').map((c) => c.body)).toEqual([
        { uses_swedebeat: true },
        { uses_swedebeat: false }, // the disconnect
        { uses_swedebeat: true },
        { uses_swedebeat: false }, // the rollback of the failed try
      ])
    })
  })

  it('waits for a slow connect before it decides about the rollback when the dialog is closed meanwhile', async () => {
    let connected = false
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': () => list({ ...ON, uses_swedebeat: connected }, connected ? [CONNECTED] : []),
      [PREFERENCE]: stored,
      [POST]: () => {
        connected = true
        return CONNECTED
      },
    })
    // Hold the connect answer back until the test lets it through.
    const fast = globalThis.fetch
    let release: () => void = () => undefined
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      const response = await fast(input, init)
      const path = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url
      if (init?.method === 'POST' && path === '/api/connections/swedebeat') await gate
      return response
    })
    renderAt('/account')
    const dialog = await openDialog()
    await fill(dialog)
    fireEvent.click(dialog.getByRole('button', { name: 'Connect Swedebeat' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/connections/swedebeat')).toHaveLength(1)
    })
    fireEvent.click(dialog.getByRole('button', { name: 'Close' }))
    release()
    expect(await screen.findByText('Swedebeat is connected.')).toBeInTheDocument()
    // The connection exists, so the preference stays on: no rollback was sent.
    expect(api.to('PUT', '/api/connections/swedebeat/preference').map((c) => c.body)).toEqual([{ uses_swedebeat: true }])
  })

  it('leaves the preference alone when the user already had it on', async () => {
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': list({ ...ON, uses_swedebeat: true }),
      [PREFERENCE]: stored,
    })
    renderAt('/account')
    const dialog = await openDialog()
    fireEvent.click(dialog.getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull()
    })
    expect(api.to('PUT', '/api/connections/swedebeat/preference')).toHaveLength(0)
  })

  it.each([
    [reason(503, 'upstream_unavailable'), 'Swedebeat could not be reached.'],
    [reason(503, 'app_key_unavailable'), 'Swedebeat sign-in is not available on this server.'],
    [reason(502, 'unknown'), 'Swedebeat answered in an unexpected way.'],
    [json({ detail: { reason: 'rate_limited' } }, 429, { 'Retry-After': '600' }), 'Try again in 10 minutes.'],
  ])('shows the error category in plain words (%#)', async (answer, text) => {
    fakeApi({ ...ACCOUNT_ROUTES, 'GET /api/connections': list(ON), [PREFERENCE]: stored, [POST]: answer })
    renderAt('/account')
    const dialog = await openDialog()
    await fill(dialog)
    fireEvent.click(dialog.getByRole('button', { name: 'Connect Swedebeat' }))
    expect(await dialog.findByRole('alert')).toHaveTextContent(text)
  })

  it('asks before signing another device out, and sends force only after the user chose it', async () => {
    let attempts = 0
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': list(ON),
      [PREFERENCE]: stored,
      [POST]: () => {
        attempts += 1
        return attempts === 1 ? reason(409, 'device_in_use') : CONNECTED
      },
    })
    renderAt('/account')
    const dialog = await openDialog()
    await fill(dialog)
    fireEvent.click(dialog.getByRole('button', { name: 'Connect Swedebeat' }))
    expect(await dialog.findByText(/signed in on another device/)).toBeInTheDocument()
    expect(dialog.getByLabelText('Swedebeat password')).toHaveValue('')
    fireEvent.change(dialog.getByLabelText('Swedebeat password'), { target: { value: PASSWORD } })
    fireEvent.click(dialog.getByRole('button', { name: 'Sign in anyway' }))
    expect(await screen.findByText('Swedebeat is connected.')).toBeInTheDocument()
    expect(api.to('POST', '/api/connections/swedebeat').map((c) => (c.body as { force: boolean }).force)).toEqual([
      false,
      true,
    ])
    // The preference stayed on through the question and was not put back.
    expect(api.to('PUT', '/api/connections/swedebeat/preference').map((c) => c.body)).toEqual([
      { uses_swedebeat: true },
      { uses_swedebeat: true },
    ])
  })
})

describe('disconnect Swedebeat', () => {
  it('asks first, deletes the sign-in, and turns the preference off', async () => {
    let connected = true
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': () => list({ ...ON, uses_swedebeat: connected }, connected ? [CONNECTED] : []),
      'DELETE /api/connections/swedebeat': () => {
        connected = false
        return new Response(null, { status: 204 })
      },
      [PREFERENCE]: stored,
    })
    renderAt('/account')
    fireEvent.click(await screen.findByRole('button', { name: 'Disconnect Swedebeat' }))
    const confirm = within(await screen.findByRole('dialog', { name: 'Disconnect Swedebeat?' }))
    expect(api.to('DELETE', '/api/connections/swedebeat')).toHaveLength(0)
    fireEvent.click(confirm.getByRole('button', { name: 'Yes, disconnect Swedebeat' }))
    expect(await screen.findByText(/Swedebeat is disconnected/)).toBeInTheDocument()
    expect(api.to('DELETE', '/api/connections/swedebeat')).toHaveLength(1)
    expect(api.to('PUT', '/api/connections/swedebeat/preference').map((c) => c.body)).toEqual([{ uses_swedebeat: false }])
    expect(await screen.findByRole('button', { name: 'Connect Swedebeat' })).toBeInTheDocument()
  })
})
