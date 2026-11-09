import { fireEvent, screen, waitFor, within, type BoundFunctions, type queries } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SIGNED_IN, fakeApi, reason } from '../../../test/fakeApi'
import { setWidth } from '../../../test/layout'
import { renderAt } from '../../../test/render'

// Connect and disconnect MOWL from the Connections list (ADR 0028 section 3, ADR 0004).

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
const PASSWORD = 'mowl-secret-password'

async function openDialog(name = 'Connect MOWL') {
  fireEvent.click(await screen.findByRole('button', { name }))
  return within(await screen.findByRole('dialog', { name: 'Connect MOWL' }))
}

type Dialog = BoundFunctions<typeof queries>

async function fill(dialog: Dialog, password = PASSWORD) {
  fireEvent.change(await dialog.findByLabelText('MOWL email'), { target: { value: ' rider@example.test ' } })
  fireEvent.change(dialog.getByLabelText('MOWL password'), { target: { value: password } })
}

describe('connect MOWL', () => {
  it('lists MOWL first, as needed, with one Connect button', async () => {
    fakeApi({ ...ACCOUNT_ROUTES, 'GET /api/connections': { connections: [] } })
    renderAt('/account')
    const row = await screen.findByTestId('connection-mowl')
    expect(row).toHaveTextContent('Needed to save classes as workouts')
    expect(row).not.toHaveTextContent('Optional')
    expect(within(row).getByRole('button', { name: 'Connect MOWL' })).toHaveTextContent('Connect')
  })

  it('sends the password once in the body, shows the status, and keeps it off the page', async () => {
    let connected = false
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': () => ({ connections: connected ? [CONNECTED] : [] }),
      'POST /api/connections/mowl': () => {
        connected = true
        return CONNECTED
      },
    })
    renderAt('/account')
    const dialog = await openDialog()
    await fill(dialog)
    fireEvent.click(dialog.getByRole('button', { name: 'Connect MOWL' }))
    expect(await screen.findByText('MOWL is connected.')).toBeInTheDocument()
    expect(screen.getByTestId('mowl-status')).toHaveTextContent('Connected')
    expect(screen.queryByRole('dialog')).toBeNull()
    const [call] = api.to('POST', '/api/connections/mowl')
    expect(call?.body).toEqual({ email: 'rider@example.test', password: PASSWORD })
    expect(call?.headers.get('X-CSRF-Token')).toBe('csrf-session')
    expect(api.calls.every((c) => !c.path.includes(PASSWORD))).toBe(true)
    expect(document.body.innerHTML).not.toContain(PASSWORD)
    expect(JSON.stringify({ ...localStorage })).not.toContain(PASSWORD)
  })

  it('explains a refused sign-in and clears the password field', async () => {
    fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': { connections: [] },
      'POST /api/connections/mowl': reason(400, 'invalid_credentials'),
    })
    renderAt('/account')
    const dialog = await openDialog()
    await fill(dialog)
    fireEvent.click(dialog.getByRole('button', { name: 'Connect MOWL' }))
    expect(await dialog.findByRole('alert')).toHaveTextContent('MOWL did not accept that email and password.')
    expect(dialog.getByLabelText('MOWL password')).toHaveValue('')
    expect(dialog.getByLabelText('MOWL email')).toHaveValue('rider@example.test')
  })

  it('opens on the scan tab when the QR is on, and on the password form on a phone', async () => {
    const routes = {
      ...ACCOUNT_ROUTES,
      'GET /api/connections': { connections: [], mowl_qr_enabled: true },
      'POST /api/connections/mowl/qr': reason(503, 'upstream_unavailable'),
    }
    setWidth(1280)
    fakeApi(routes)
    const wide = renderAt('/account')
    const dialog = await openDialog()
    expect(dialog.getByRole('tab', { name: 'Scan code' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.mouseDown(dialog.getByRole('tab', { name: 'Use password' })) // Radix tabs open on mouse down
    expect(await dialog.findByLabelText('MOWL password')).toBeInTheDocument()
    wide.unmount()

    setWidth(390)
    fakeApi(routes)
    renderAt('/account')
    const phone = await openDialog()
    expect(await phone.findByLabelText('MOWL password')).toBeInTheDocument()
    expect(phone.queryByRole('tab')).toBeNull()
  })

  it('asks before disconnecting, says what is forgotten, then offers Connect again', async () => {
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
    fireEvent.click(await screen.findByRole('button', { name: 'Disconnect MOWL' }))
    const confirm = within(await screen.findByRole('dialog', { name: 'Disconnect MOWL?' }))
    expect(confirm.getByText(/two copies/)).toBeInTheDocument() // the warning of ADR 0006 section 3
    expect(api.to('DELETE', '/api/connections/mowl')).toHaveLength(0)
    fireEvent.click(confirm.getByRole('button', { name: 'Yes, disconnect MOWL' }))
    expect(await screen.findByText(/MOWL is disconnected/)).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Connect MOWL' })).toBeInTheDocument()
    })
    expect(api.to('DELETE', '/api/connections/mowl')).toHaveLength(1)
  })

  it('says so when the stored sign-in no longer works and offers to connect again', async () => {
    fakeApi({
      ...ACCOUNT_ROUTES,
      'GET /api/connections': {
        connections: [{ ...CONNECTED, status: 'needs_reconnect', last_error: 'needs_reconnect' }],
      },
    })
    renderAt('/account')
    const status = await screen.findByTestId('mowl-status')
    expect(status).toHaveTextContent('Needs reconnecting')
    expect(status).toHaveTextContent('MOWL no longer accepts the stored sign-in')
    expect(screen.getByRole('button', { name: 'Connect MOWL again' })).toBeInTheDocument()
  })
})
