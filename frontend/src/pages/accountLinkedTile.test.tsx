import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SIGNED_IN, fakeApi, json } from '../test/fakeApi'
import { renderAt } from '../test/render'

// The "N of 2 linked" tile of the Account header (#1232): the count, and its update after the MOWL
// dialog links the account, without a reload.

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
const SPOTIFY = {
  status: 'connected',
  client_id: null,
  last_error: null,
  last_error_message: null,
  callback_url: 'http://127.0.0.1:8000/oauth/spotify/callback',
  pool_consent: false,
  consent_version: null,
  consent_text: 'The consent sentence.',
  song_picking_enabled: true,
}

// The tile shows the Connections tab's first count, then settles once both services have answered.
async function expectLinked(text: string) {
  await waitFor(() => {
    expect(screen.getByTestId('account-stat-linked')).toHaveTextContent(text)
  })
}

describe('the "N of 2 linked" tile', () => {
  it('counts two when MOWL and Spotify are both linked', async () => {
    fakeApi({ ...ACCOUNT, 'GET /api/connections': { connections: [MOWL] }, 'GET /api/connections/spotify': SPOTIFY })
    renderAt('/account')
    await expectLinked('2 of 2 linked')
  })

  it('counts Spotify alone as one, and a Spotify that is switched off as not linked', async () => {
    fakeApi({ ...ACCOUNT, 'GET /api/connections': { connections: [] }, 'GET /api/connections/spotify': SPOTIFY })
    const first = renderAt('/account')
    await expectLinked('1 of 2 linked')
    first.unmount()
    fakeApi({
      ...ACCOUNT,
      'GET /api/connections': { connections: [MOWL] },
      'GET /api/connections/spotify': () => json({ detail: { reason: 'feature_off' } }, 404),
    })
    renderAt('/account')
    await expectLinked('1 of 2 linked')
  })

  it('ignores a MOWL connection that is not in the connected state', async () => {
    fakeApi({
      ...ACCOUNT,
      'GET /api/connections': { connections: [{ ...MOWL, status: 'needs_reauth' }] },
      'GET /api/connections/spotify': SPOTIFY,
    })
    renderAt('/account')
    await expectLinked('1 of 2 linked')
  })

  it('rises from 0 to 1 when MOWL is linked in the dialog, without a reload', async () => {
    let linked = false
    const api = fakeApi({
      ...ACCOUNT,
      'GET /api/connections': () => ({ connections: linked ? [MOWL] : [] }),
      'POST /api/connections/mowl': () => {
        linked = true
        return MOWL
      },
    })
    renderAt('/account')
    await expectLinked('0 of 2 linked')
    fireEvent.click(await screen.findByRole('button', { name: 'Connect MOWL' }))
    const dialog = within(await screen.findByRole('dialog', { name: 'Connect MOWL' }))
    fireEvent.change(await dialog.findByLabelText('MOWL email'), { target: { value: 'rider@example.test' } })
    fireEvent.change(dialog.getByLabelText('MOWL password'), { target: { value: 'pw' } })
    fireEvent.click(dialog.getByRole('button', { name: 'Connect MOWL' }))
    await waitFor(() => {
      expect(screen.getByTestId('account-stat-linked')).toHaveTextContent('1 of 2 linked')
    })
    expect(api.to('POST', '/api/connections/mowl')).toHaveLength(1)
  })
})
