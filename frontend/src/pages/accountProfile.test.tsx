import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PLAN_FREE, SIGNED_IN, fakeApi } from '../test/fakeApi'
import { renderAt } from '../test/render'

// ADR 0013 Afterburn A3: the profile card's tiles, the "One step left" strip and the plan hero.

const ACCOUNT = {
  'GET /api/auth/session': SIGNED_IN,
  'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
  'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
}
const MOWL = {
  id: 'c0000000-0000-4000-8000-000000000001',
  provider: 'mowl',
  status: 'connected',
  last_error: null,
  connected_at: '2026-11-06T10:00:00Z',
  last_used_at: null,
  expires_at: null,
}
function spotify(status: string) {
  return {
    status,
    client_id: null,
    last_error: null,
    last_error_message: null,
    callback_url: 'http://127.0.0.1:8000/oauth/spotify/callback',
    pool_consent: false,
    consent_version: null,
    consent_text: 'The consent sentence.',
    song_picking_enabled: true,
  }
}

describe('the profile card', () => {
  it('shows the plan, the classes this month and the required connections linked', async () => {
    fakeApi({
      ...ACCOUNT,
      'GET /api/account/plan': PLAN_FREE,
      'GET /api/connections': { connections: [MOWL] },
      'GET /api/connections/spotify': spotify('not_connected'),
    })
    renderAt('/account')
    expect(await screen.findByTestId('account-stat-plan')).toHaveTextContent('Free')
    expect(screen.getByTestId('account-stat-classes')).toHaveTextContent('Classes this month')
    expect(await screen.findByTestId('account-stat-linked')).toHaveTextContent('1 of 2 linked')
  })

  it('counts none linked when nothing is', async () => {
    fakeApi({ ...ACCOUNT, 'GET /api/connections': { connections: [] } })
    renderAt('/account')
    expect(await screen.findByTestId('account-stat-linked')).toHaveTextContent('0 of 2 linked')
  })
})

describe('One step left', () => {
  it('asks for MOWL first, with the meter and the same dialog as the tile', async () => {
    fakeApi({
      ...ACCOUNT,
      'GET /api/connections': { connections: [] },
      'GET /api/connections/spotify': spotify('connected'),
    })
    renderAt('/account')
    const strip = await screen.findByTestId('one-step-left')
    expect(strip).toHaveTextContent('One step left: link MOWL')
    await waitFor(() => {
      expect(within(strip).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50')
    })
    fireEvent.click(within(strip).getByRole('button', { name: 'Link MOWL' }))
    expect(await screen.findByRole('dialog', { name: 'Connect MOWL' })).toBeInTheDocument()
  })

  it('names Spotify once MOWL is linked, and goes away when both are', async () => {
    fakeApi({
      ...ACCOUNT,
      'GET /api/connections': { connections: [MOWL] },
      'GET /api/connections/spotify': spotify('not_connected'),
    })
    const { unmount } = renderAt('/account')
    expect(await screen.findByTestId('one-step-left')).toHaveTextContent('link Spotify')
    unmount()
    fakeApi({
      ...ACCOUNT,
      'GET /api/connections': { connections: [MOWL] },
      'GET /api/connections/spotify': spotify('connected'),
    })
    renderAt('/account')
    await screen.findByTestId('connection-spotify')
    await waitFor(() => {
      expect(screen.queryByTestId('one-step-left')).toBeNull()
    })
  })

  it('marks MOWL and Spotify Required and the rest Optional', async () => {
    fakeApi({
      ...ACCOUNT,
      'GET /api/connections': { connections: [] },
      'GET /api/connections/spotify': spotify('connected'),
    })
    renderAt('/account')
    expect(await screen.findByTestId('connection-mowl')).toHaveTextContent('Required')
    expect(await screen.findByTestId('connection-spotify')).toHaveTextContent('Required')
    expect(screen.getByTestId('connection-swedebeat')).toHaveTextContent('Optional')
    expect(screen.getByTestId('connection-mowl')).toHaveTextContent('Not connected')
  })
})

describe('the plan hero', () => {
  it('shows the usage ring and the Your plan badge', async () => {
    fakeApi({ ...ACCOUNT, 'GET /api/account/plan': PLAN_FREE })
    renderAt('/account#plan')
    const ring = await screen.findByTestId('usage-ring')
    expect(ring).toHaveAttribute('role', 'img')
    expect(ring.getAttribute('aria-label')).toMatch(/% of this month’s classes used/)
    expect(screen.getByTestId('plan-name')).toHaveTextContent('Your plan: Free')
  })
})
