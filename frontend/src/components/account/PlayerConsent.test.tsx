import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SIGNED_IN, fakeApi, json } from '../../test/fakeApi'
import { renderAt } from '../../test/render'

// The player's consent on the account page (ADR 0034 sections 1 and 6): when it was given, and a
// button to withdraw it. The consent page the player's sign-in opens is tested in
// pages/playerConsent.test.tsx.

const CONSENT = '/api/auth/player-consent'
const ROUTES = {
  'GET /api/auth/session': SIGNED_IN,
  'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
  'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
}

describe('the player consent on Account', () => {
  it('is not shown while the sign-in provider is off', async () => {
    fakeApi(ROUTES)
    renderAt('/account')
    await screen.findByTestId('connection-mowl')
    expect(screen.queryByText('classbeat-player sign-in')).toBeNull()
  })

  it('says when the player was allowed, and that withdrawing does not disconnect it', async () => {
    fakeApi({ ...ROUTES, [`GET ${CONSENT}`]: { enabled: true, consent_at: '2026-11-12T09:30:00Z' } })
    renderAt('/account')
    expect(await screen.findByTestId('player-consent')).toHaveTextContent(
      /You allowed classbeat-player to sign you in on .*2026/,
    )
    expect(screen.getByText(/does not disconnect the player and does not remove classes from it/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Withdraw permission' })).toBeEnabled()
  })

  it('withdraws on the press only, then says the player asks again', async () => {
    let given: string | null = '2026-11-12T09:30:00Z'
    const api = fakeApi({
      ...ROUTES,
      [`GET ${CONSENT}`]: () => ({ enabled: true, consent_at: given }),
      [`DELETE ${CONSENT}`]: () => {
        given = null
        return { withdrawn: true }
      },
    })
    renderAt('/account')
    const button = await screen.findByRole('button', { name: 'Withdraw permission' })
    expect(api.to('DELETE', CONSENT)).toHaveLength(0)
    fireEvent.click(button)
    expect(await screen.findByText(/Permission withdrawn\. classbeat-player asks again/)).toBeInTheDocument()
    expect(api.to('DELETE', CONSENT)).toHaveLength(1)
    expect(api.to('DELETE', CONSENT)[0]!.headers.get('X-CSRF-Token')).toBe('csrf-session')
    expect(screen.getByTestId('player-consent')).toHaveTextContent(/may not sign you in/)
    expect(screen.queryByRole('button', { name: 'Withdraw permission' })).toBeNull()
  })

  it('has nothing to withdraw before it was given or after it was withdrawn', async () => {
    fakeApi({ ...ROUTES, [`GET ${CONSENT}`]: { enabled: true, consent_at: null } })
    renderAt('/account')
    expect(await screen.findByTestId('player-consent')).toHaveTextContent(/may not sign you in/)
    expect(screen.queryByRole('button', { name: 'Withdraw permission' })).toBeNull()
  })

  it('shows an error when the answer cannot be read, and keeps the rest of the page', async () => {
    fakeApi({ ...ROUTES, [`GET ${CONSENT}`]: () => json({}, 500) })
    renderAt('/account')
    expect(await screen.findByText('That did not work. Try again.')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByTestId('connection-mowl')).toBeInTheDocument()
    })
  })
})
