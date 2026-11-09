import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SIGNED_IN, fakeApi, json, reason } from '../../test/fakeApi'
import { setWidth } from '../../test/layout'
import { renderAt } from '../../test/render'
import { POLL_MS } from './MowlQr'

// ADR 0024, ADR 0028: connect MOWL by scanning a QR code in the dialog. The page talks to classbeat's API only.

const ACCOUNT_ROUTES = {
  'GET /api/auth/session': SIGNED_IN,
  'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
  'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
}
const ID = 'w-abc_123'
const QR = '/api/connections/mowl/qr'
const TICKET = { ticket_id: ID, image: `${QR}/${ID}/image`, expires_at: '2030-01-01T00:15:00Z' }
const CONNECTED = {
  id: 'c0000000-0000-4000-8000-000000000001',
  provider: 'mowl',
  status: 'connected',
  last_error: null,
  connected_at: '2026-11-06T10:00:00Z',
  last_used_at: null,
  expires_at: null,
}
const ON = { 'GET /api/connections': { connections: [], mowl_qr_enabled: true } }

function status(value: string, extra: object = {}) {
  return { status: value, reason: null, expires_at: TICKET.expires_at, connection: null, ...extra }
}

/** Opens the Connect MOWL dialog from its row; the QR tab starts its code on its own. */
async function openMowl() {
  fireEvent.click(await screen.findByRole('button', { name: 'Connect MOWL' }))
  return within(await screen.findByRole('dialog', { name: 'Connect MOWL' }))
}

async function nextPoll() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(POLL_MS)
  })
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true, now: new Date('2030-01-01T00:00:00Z') })
  setWidth(1280)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('Scan with the MOWL app', () => {
  it('shows the code from our origin, polls, and shows the connection once scanned', async () => {
    let polls = 0
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      ...ON,
      [`POST ${QR}`]: TICKET,
      [`GET ${QR}/${ID}`]: () => (++polls < 2 ? status('waiting') : status('connected', { connection: CONNECTED })),
    })
    renderAt('/account')
    const mowl = await openMowl()
    const image = await mowl.findByRole('img', { name: 'MOWL QR code' })
    expect(image).toHaveAttribute('src', `${QR}/${ID}/image`)
    expect(mowl.getByTestId('mowl-qr-countdown')).toHaveTextContent('15:00')
    // The password form stays as the other tab.
    expect(mowl.getByRole('tab', { name: 'Use password' })).toBeInTheDocument()
    await nextPoll()
    expect(mowl.getByTestId('mowl-qr-countdown')).toHaveTextContent('14:58')
    await nextPoll()
    expect(await screen.findByText('MOWL is connected.')).toBeInTheDocument()
    expect(screen.getByTestId('mowl-status')).toHaveTextContent('Connected')
    expect(screen.queryByTestId('mowl-qr')).toBeNull() // the dialog closed
    expect(api.to('GET', `${QR}/${ID}`)).toHaveLength(2)
    expect(api.to('POST', QR)[0]?.headers.get('X-CSRF-Token')).toBe('csrf-session')
    expect(api.to('DELETE', `${QR}/${ID}`)).toHaveLength(0) // a finished wait is not cancelled
  })

  it('offers a new code after the code expires', async () => {
    fakeApi({ ...ACCOUNT_ROUTES, ...ON, [`POST ${QR}`]: TICKET, [`GET ${QR}/${ID}`]: status('expired') })
    renderAt('/account')
    const mowl = await openMowl()
    await mowl.findByRole('img', { name: /QR code/ })
    await nextPoll()
    expect(await mowl.findByText(/The code expired/)).toBeInTheDocument()
    expect(mowl.queryByRole('img', { name: /QR code/ })).toBeNull()
    expect(mowl.getByRole('button', { name: 'New code' })).toBeInTheDocument()
  })

  it('says so when the scan worked but the connection is gone', async () => {
    fakeApi({ ...ACCOUNT_ROUTES, ...ON, [`POST ${QR}`]: TICKET, [`GET ${QR}/${ID}`]: status('connected') })
    renderAt('/account')
    const mowl = await openMowl()
    await mowl.findByRole('img', { name: /QR code/ })
    await nextPoll()
    expect(await mowl.findByText(/connection could not be found/)).toBeInTheDocument()
    expect(mowl.queryByText(/did not work/)).toBeNull()
  })

  it('explains a failed wait in plain words and never shows a raw code', async () => {
    fakeApi({
      ...ACCOUNT_ROUTES,
      ...ON,
      [`POST ${QR}`]: TICKET,
      [`GET ${QR}/${ID}`]: status('failed', { reason: 'upstream_unavailable' }),
    })
    renderAt('/account')
    const mowl = await openMowl()
    await mowl.findByRole('img', { name: /QR code/ })
    await nextPoll()
    expect(await mowl.findByText(/MOWL could not be reached/)).toBeInTheDocument()
    expect(mowl.queryByText(/upstream_unavailable/)).toBeNull()
  })

  it.each([
    [reason(503, 'upstream_unavailable'), 'MOWL could not be reached.'],
    [json({ detail: { reason: 'rate_limited' } }, 429, { 'Retry-After': '600' }), 'Try again in 10 minutes.'],
  ])('shows a refused start (%#)', async (answer, text) => {
    fakeApi({ ...ACCOUNT_ROUTES, ...ON, [`POST ${QR}`]: answer })
    renderAt('/account')
    const mowl = await openMowl()
    expect(await mowl.findByRole('alert')).toHaveTextContent(text)
  })

  it('cancels the code on the server when stopped', async () => {
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      ...ON,
      [`POST ${QR}`]: TICKET,
      [`GET ${QR}/${ID}`]: status('waiting'),
      [`DELETE ${QR}/${ID}`]: () => new Response(null, { status: 204 }),
    })
    renderAt('/account')
    const mowl = await openMowl()
    await mowl.findByRole('img', { name: /QR code/ })
    fireEvent.click(mowl.getByRole('button', { name: 'Stop' }))
    await waitFor(() => {
      expect(api.to('DELETE', `${QR}/${ID}`)).toHaveLength(1)
    })
    expect(mowl.queryByRole('img', { name: /QR code/ })).toBeNull()
    await nextPoll()
    expect(api.to('GET', `${QR}/${ID}`)).toHaveLength(0) // no poll after the stop
  })

  it('cancels a code that arrives after the dialog was closed', async () => {
    const api = fakeApi({
      ...ACCOUNT_ROUTES,
      ...ON,
      [`POST ${QR}`]: TICKET,
      [`DELETE ${QR}/${ID}`]: () => new Response(null, { status: 204 }),
    })
    // Hold the start answer back until the dialog is gone.
    const fast = globalThis.fetch
    let release: () => void = () => undefined
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      const response = await fast(input, init)
      const path = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url
      if (init?.method === 'POST' && path === QR) await gate
      return response
    })
    renderAt('/account')
    const mowl = await openMowl()
    await waitFor(() => {
      expect(api.to('POST', QR)).toHaveLength(1)
    })
    fireEvent.click(mowl.getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull()
    })
    release()
    await waitFor(() => {
      expect(api.to('DELETE', `${QR}/${ID}`)).toHaveLength(1)
    })
  })

  it('is not offered in the phone layout, where the password form remains', async () => {
    setWidth(390)
    fakeApi({ ...ACCOUNT_ROUTES, ...ON })
    renderAt('/account')
    const mowl = await openMowl()
    expect(await mowl.findByLabelText('MOWL password')).toBeInTheDocument()
    expect(mowl.queryByTestId('mowl-qr')).toBeNull()
    expect(mowl.queryByRole('tab')).toBeNull()
  })

  it('is not offered while the server switch is off', async () => {
    fakeApi({ ...ACCOUNT_ROUTES, 'GET /api/connections': { connections: [] } })
    renderAt('/account')
    const mowl = await openMowl()
    expect(await mowl.findByLabelText('MOWL password')).toBeInTheDocument()
    expect(mowl.queryByTestId('mowl-qr')).toBeNull()
    expect(mowl.queryByRole('tab')).toBeNull()
  })

  it('is not offered when MOWL is already connected', async () => {
    fakeApi({ ...ACCOUNT_ROUTES, 'GET /api/connections': { connections: [CONNECTED], mowl_qr_enabled: true } })
    renderAt('/account')
    expect(await screen.findByTestId('mowl-status')).toHaveTextContent('Connected')
    expect(screen.queryByTestId('mowl-qr')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Connect MOWL' })).toBeNull()
  })
})

describe('the QR ticket answer', () => {
  it('refuses an image that is not our own QR route', async () => {
    fakeApi({
      ...ACCOUNT_ROUTES,
      ...ON,
      [`POST ${QR}`]: { ...TICKET, image: 'https://evil.example/qr.png' },
    })
    renderAt('/account')
    const mowl = await openMowl()
    expect(await mowl.findByRole('alert')).toHaveTextContent('That did not work. Try again.')
    expect(mowl.queryByRole('img', { name: /QR code/ })).toBeNull()
  })
})
