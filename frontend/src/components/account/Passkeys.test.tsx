import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { SIGNED_IN, fakeApi, reason } from '../../test/fakeApi'
import { CREATION_OPTIONS, credential, error, installPasskeys, uninstallPasskeys } from '../../test/passkeyMock'
import { renderAt } from '../../test/render'

afterEach(uninstallPasskeys)

const WORK = {
  id: 'p1',
  label: 'Work laptop',
  transports: ['internal'],
  synced: false,
  created_at: '2026-10-01T10:00:00Z',
  last_used_at: null,
}
const PHONE = { ...WORK, id: 'p2', label: 'Phone', synced: true, last_used_at: '2026-11-01T10:00:00Z' }

function routes(state: { passkeys: unknown[]; enabled: boolean; max?: number }, more: Record<string, unknown> = {}) {
  return {
    'GET /api/auth/session': { ...SIGNED_IN, passkeys: state.enabled },
    'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
    'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
    'GET /api/auth/sessions': { sessions: [] },
    'GET /api/auth/passkeys': () => ({ passkeys: state.passkeys, enabled: state.enabled, max: state.max ?? 10 }),
    ...more,
  }
}

async function open() {
  renderAt('/account#security')
  await screen.findByRole('heading', { name: 'Sign-in methods' })
}

describe('Passkeys card', () => {
  it('is not there at all without PublicKeyCredential', async () => {
    const api = fakeApi(routes({ passkeys: [WORK], enabled: true }))
    await open()
    expect(screen.queryByRole('heading', { name: 'Passkeys' })).toBeNull()
    expect(api.to('GET', '/api/auth/passkeys')).toHaveLength(0)
  })

  it('lists the passkeys with where they live and when they were used', async () => {
    installPasskeys()
    fakeApi(routes({ passkeys: [WORK, PHONE], enabled: true }))
    await open()
    const card = (await screen.findByRole('heading', { name: 'Passkeys' })).closest('section')!
    expect(within(card).getAllByText(/Work laptop/).length).toBeGreaterThan(0)
    expect(within(card).getByText(/This device only/)).toBeInTheDocument()
    expect(within(card).getByText(/Never used/)).toBeInTheDocument()
    expect(within(card).getByText(/Synced/)).toBeInTheDocument()
    expect(within(card).getByText(/Last used/)).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Add a passkey' })).toBeInTheDocument()
  })

  it('with the flag off says so, and offers remove but no add or rename', async () => {
    installPasskeys()
    const api = fakeApi(
      routes({ passkeys: [WORK], enabled: false }, { 'DELETE /api/auth/passkeys/p1': new Response(null, { status: 204 }) }),
    )
    await open()
    expect(await screen.findByText('Passkeys are switched off; you can still remove these.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add a passkey' })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Rename/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Remove Work laptop' }))
    await waitFor(() => {
      expect(api.to('DELETE', '/api/auth/passkeys/p1')).toHaveLength(1)
    })
  })

  it('with the flag off and no passkey shows no card', async () => {
    installPasskeys()
    const api = fakeApi(routes({ passkeys: [], enabled: false }))
    await open()
    await waitFor(() => {
      expect(api.to('GET', '/api/auth/passkeys')).toHaveLength(1)
    })
    expect(screen.queryByRole('heading', { name: 'Passkeys' })).toBeNull()
  })

  it('registers a passkey: options, the browser, then finish with the credential and the name', async () => {
    const browser = installPasskeys({ signals: true })
    const state = { passkeys: [] as unknown[], enabled: true }
    const api = fakeApi(
      routes(state, {
        'POST /api/auth/passkeys/register/start': { publicKey: CREATION_OPTIONS },
        'POST /api/auth/passkeys/register/finish': () => {
          state.passkeys = [WORK]
          return new Response(JSON.stringify(WORK), { status: 201, headers: { 'Content-Type': 'application/json' } })
        },
      }),
    )
    await open()
    fireEvent.change(await screen.findByLabelText('Name (optional)'), { target: { value: 'Work laptop' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add a passkey' }))
    expect(await screen.findByRole('button', { name: 'Remove Work laptop' })).toBeInTheDocument()
    expect(browser.create).toHaveBeenCalledTimes(1)
    expect(api.to('POST', '/api/auth/passkeys/register/finish')[0]?.body).toEqual({
      credential: credential().toJSON?.(),
      label: 'Work laptop',
    })
    expect(browser.signalCurrentUserDetails).toHaveBeenCalledWith({
      rpId: 'classbeat.test',
      userId: 'dXNlcg',
      name: 'ann@example.test',
      displayName: 'ann@example.test',
    })
  })

  it('stays quiet when the user closes the browser dialog', async () => {
    installPasskeys({ result: error('NotAllowedError') })
    const api = fakeApi(routes({ passkeys: [], enabled: true }, { 'POST /api/auth/passkeys/register/start': { publicKey: CREATION_OPTIONS } }))
    await open()
    fireEvent.click(await screen.findByRole('button', { name: 'Add a passkey' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/auth/passkeys/register/start')).toHaveLength(1)
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add a passkey' })).toBeEnabled()
    })
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('says so when this device already has a passkey', async () => {
    installPasskeys({ result: error('InvalidStateError') })
    fakeApi(routes({ passkeys: [], enabled: true }, { 'POST /api/auth/passkeys/register/start': { publicKey: CREATION_OPTIONS } }))
    await open()
    fireEvent.click(await screen.findByRole('button', { name: 'Add a passkey' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('This device already has a passkey for classbeat.')
  })

  it('words the server limit and a duplicate in plain language', async () => {
    installPasskeys()
    fakeApi(
      routes(
        { passkeys: [], enabled: true },
        {
          'POST /api/auth/passkeys/register/start': { publicKey: CREATION_OPTIONS },
          'POST /api/auth/passkeys/register/finish': reason(409, 'passkey_exists'),
        },
      ),
    )
    await open()
    fireEvent.click(await screen.findByRole('button', { name: 'Add a passkey' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('That passkey is already added.')
  })

  it('hides the add form at the limit', async () => {
    installPasskeys()
    fakeApi(routes({ passkeys: [WORK], enabled: true, max: 1 }))
    await open()
    expect(await screen.findByText(/reached the limit of passkeys/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add a passkey' })).toBeNull()
  })

  it('renames a passkey', async () => {
    installPasskeys()
    const api = fakeApi(
      routes({ passkeys: [WORK], enabled: true }, { 'PATCH /api/auth/passkeys/p1': { ...WORK, label: 'Desk' } }),
    )
    await open()
    fireEvent.click(await screen.findByRole('button', { name: 'Rename Work laptop' }))
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Desk' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => {
      expect(api.to('PATCH', '/api/auth/passkeys/p1')[0]?.body).toEqual({ label: 'Desk' })
    })
  })

  it('has labelled controls and no inline script or style (the CSP allows neither)', async () => {
    installPasskeys()
    fakeApi(routes({ passkeys: [WORK], enabled: true }))
    await open()
    const card = (await screen.findByRole('heading', { name: 'Passkeys' })).closest('section')!
    expect(card).toHaveAttribute('aria-labelledby')
    for (const control of card.querySelectorAll('input, button')) {
      const named = control.getAttribute('aria-label') ?? control.textContent ?? ''
      const labelled = control instanceof HTMLInputElement ? (control.labels?.length ?? 0) > 0 : named.trim() !== ''
      expect(labelled).toBe(true)
    }
    expect(card.querySelector('[style], script, style')).toBeNull()
  })
})
