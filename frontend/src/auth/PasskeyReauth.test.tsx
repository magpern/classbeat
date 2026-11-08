import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ApiError } from '../api/client'
import { SIGNED_IN, fakeApi } from '../test/fakeApi'
import { REQUEST_OPTIONS, credential, installPasskeys, uninstallPasskeys } from '../test/passkeyMock'
import { GuardProvider } from './GuardProvider'
import { useGuard } from './prompts'
import { SessionProvider } from './SessionContext'

afterEach(uninstallPasskeys)

function Runner({ grant }: { grant: { ok: boolean } }) {
  const guard = useGuard()
  return (
    <button
      type="button"
      onClick={() => {
        guard(() => (grant.ok ? Promise.resolve('x') : Promise.reject(new ApiError(403, 'reauth_required', null, undefined)))).catch(
          () => undefined,
        )
      }}
    >
      go
    </button>
  )
}

function routes(extra: { flag: boolean; passkeys: unknown[]; grant: { ok: boolean } }) {
  return fakeApi({
    'GET /api/auth/session': { ...SIGNED_IN, passkeys: extra.flag },
    'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
    'GET /api/auth/passkeys': { passkeys: extra.passkeys, enabled: extra.flag, max: 10 },
    'POST /api/auth/passkeys/reauth/start': { publicKey: REQUEST_OPTIONS },
    'POST /api/auth/passkeys/reauth/finish': () => {
      extra.grant.ok = true
      return { status: 'ok' }
    },
  })
}

function open(grant: { ok: boolean }) {
  render(
    <SessionProvider>
      <GuardProvider>
        <Runner grant={grant} />
      </GuardProvider>
    </SessionProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'go' }))
}

const ONE = [{ id: 'p1', label: 'Work', transports: [], synced: false, created_at: '2026-01-01T00:00:00Z', last_used_at: null }]

describe('passkey option in the re-authentication prompt', () => {
  it('confirms with a passkey through reauth/start and reauth/finish', async () => {
    installPasskeys({ result: credential('platform') })
    const grant = { ok: false }
    const api = routes({ flag: true, passkeys: ONE, grant })
    open(grant)
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Confirm with a passkey' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull()
    })
    expect(api.to('POST', '/api/auth/passkeys/reauth/start')).toHaveLength(1)
    expect(api.to('POST', '/api/auth/passkeys/reauth/finish')[0]?.body).toMatchObject({
      credential: { id: 'cred-1' },
    })
  })

  it.each([
    ['the flag is off', false, ONE, true],
    ['the user has no passkey', true, [], true],
    ['the browser has no WebAuthn', true, ONE, false],
  ])('is not offered when %s', async (_name, flag, passkeys, supported) => {
    if (supported) installPasskeys()
    const grant = { ok: false }
    const api = routes({ flag, passkeys, grant })
    open(grant)
    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByLabelText('Password')
    await waitFor(() => {
      expect(api.to('GET', '/api/auth/identities').length).toBeGreaterThan(0)
    })
    expect(within(dialog).queryByRole('button', { name: 'Confirm with a passkey' })).toBeNull()
  })
})
