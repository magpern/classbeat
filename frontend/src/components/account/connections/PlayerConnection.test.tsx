import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SIGNED_IN, fakeApi, reason } from '../../../test/fakeApi'
import { renderAt } from '../../../test/render'

// The classbeat-player row on Account, Connections and its dialog (ADR 0034 sections 3 and 6):
// connect, status, reconnect, disconnect with its "may still be listed" note, the callback's
// result on /account/player, and the undecided removal. Connecting only leaves when it is pressed.

const goToProvider = vi.hoisted(() => vi.fn<(url: string) => void>())
vi.mock('../../../lib/navigation', () => ({ goToProvider }))

const PLAYER = '/api/connections/player'
const OP = 'f3333333-3333-4333-8333-333333333333'
const ACCOUNT_ROUTES = {
  'GET /api/auth/session': SIGNED_IN,
  'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
  'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
}

function status(value: string, linkEnabled = true) {
  return {
    status: value,
    last_error: value === 'needs_reconnect' ? 'needs_reconnect' : null,
    connected_at: value === 'not_connected' ? null : '2026-11-10T10:00:00Z',
    link_enabled: linkEnabled,
  }
}

function playerApi(current: Record<string, unknown>, routes: Record<string, unknown> = {}) {
  return fakeApi({ ...ACCOUNT_ROUTES, [`GET ${PLAYER}`]: current, ...(routes as Record<string, never>) })
}

const dialog = () => screen.findByRole('dialog', { name: 'classbeat-player' })

beforeEach(() => {
  goToProvider.mockClear()
})

describe('the classbeat-player row', () => {
  it('is left out while the player is off and nothing is connected', async () => {
    playerApi(status('not_connected', false))
    renderAt('/account')
    await screen.findByTestId('connection-mowl')
    await waitFor(() => {
      expect(screen.queryByTestId('connection-player')).toBeNull()
    })
  })

  it('says what the player is for, is optional, and opens an explanation, not the link', async () => {
    const api = playerApi(status('not_connected'))
    renderAt('/account')
    const row = await screen.findByTestId('connection-player')
    expect(row).toHaveTextContent('Optional')
    expect(row).toHaveTextContent('Runs your classes on a screen in the room')
    fireEvent.click(within(row).getByRole('button', { name: 'Connect classbeat-player' }))
    const open = within(await dialog())
    expect(await open.findByText(/Nothing is sent until you press Send to player on a class/)).toBeInTheDocument()
    expect(open.getByRole('button', { name: 'Connect classbeat-player' })).toBeInTheDocument()
    expect(open.queryByRole('button', { name: 'Disconnect classbeat-player' })).toBeNull()
    expect(api.to('POST', `${PLAYER}/start`)).toHaveLength(0)
    expect(goToProvider).not.toHaveBeenCalled()
  })

  it('Connect starts the link and leaves for the address the server named, only when pressed', async () => {
    const api = playerApi(status('not_connected'), {
      [`POST ${PLAYER}/start`]: { link_url: 'https://player.example.test/link/source?state=s' },
    })
    renderAt('/account')
    fireEvent.click(await screen.findByRole('button', { name: 'Connect classbeat-player' }))
    fireEvent.click(within(await dialog()).getByRole('button', { name: 'Connect classbeat-player' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalledWith('https://player.example.test/link/source?state=s')
    })
    expect(api.to('POST', `${PLAYER}/start`)).toHaveLength(1)
    expect(api.to('POST', `${PLAYER}/start`)[0]!.headers.get('X-CSRF-Token')).toBe('csrf-session')
  })

  it('words a refused start in plain text and stays', async () => {
    playerApi(status('not_connected'), { [`POST ${PLAYER}/start`]: () => reason(429, 'rate_limited') })
    renderAt('/account')
    fireEvent.click(await screen.findByRole('button', { name: 'Connect classbeat-player' }))
    fireEvent.click(within(await dialog()).getByRole('button', { name: 'Connect classbeat-player' }))
    expect(await within(await dialog()).findByRole('alert')).toBeInTheDocument()
    expect(goToProvider).not.toHaveBeenCalled()
  })

  it('shows Connected, and offers Reconnect and Disconnect', async () => {
    playerApi(status('connected'))
    renderAt('/account')
    expect(await screen.findByTestId('player-status')).toHaveTextContent('Connected')
    fireEvent.click(screen.getByRole('button', { name: 'Manage classbeat-player' }))
    const open = within(await dialog())
    expect(await open.findByText(/Classes you send appear in your classbeat-player library/)).toBeInTheDocument()
    expect(open.getByRole('button', { name: 'Reconnect classbeat-player' })).toBeInTheDocument()
    expect(open.getByRole('button', { name: 'Disconnect classbeat-player' })).toBeInTheDocument()
  })

  it('says the link stopped working when it needs a reconnect', async () => {
    playerApi(status('needs_reconnect'))
    renderAt('/account')
    expect(await screen.findByTestId('player-status')).toHaveTextContent('Needs reconnecting')
    fireEvent.click(screen.getByRole('button', { name: 'Manage classbeat-player' }))
    expect(await within(await dialog()).findByText(/link to classbeat-player stopped working/)).toBeInTheDocument()
  })

  it('Disconnect asks first, then ends the link and says it may still be listed', async () => {
    let current = status('connected')
    const api = playerApi(current, {
      [`GET ${PLAYER}`]: () => current,
      [`DELETE ${PLAYER}`]: () => {
        current = status('not_connected')
        return { disconnected: true, link_may_remain: true }
      },
    })
    renderAt('/account')
    fireEvent.click(await screen.findByRole('button', { name: 'Manage classbeat-player' }))
    fireEvent.click(within(await dialog()).getByRole('button', { name: 'Disconnect classbeat-player' }))
    const ask = await screen.findByRole('dialog', { name: 'Disconnect classbeat-player?' })
    expect(ask).toHaveTextContent(/Classes already on the player stay there/)
    expect(api.to('DELETE', PLAYER)).toHaveLength(0)
    fireEvent.click(within(ask).getByRole('button', { name: 'Yes, disconnect' }))
    const note = await within(await dialog()).findByText(/Disconnected from classbeat-player\./)
    expect(note).toHaveTextContent('The link may still be listed on the player’s site. You can remove it there.')
    expect(api.to('DELETE', PLAYER)).toHaveLength(1)
  })

  it('says nothing about a lingering link when the player confirmed it was removed', async () => {
    playerApi(status('connected'), { [`DELETE ${PLAYER}`]: { disconnected: true, link_may_remain: false } })
    renderAt('/account')
    fireEvent.click(await screen.findByRole('button', { name: 'Manage classbeat-player' }))
    fireEvent.click(within(await dialog()).getByRole('button', { name: 'Disconnect classbeat-player' }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Disconnect classbeat-player?' })).getByRole('button', { name: 'Yes, disconnect' }))
    const note = await within(await dialog()).findByText(/Disconnected from classbeat-player\./)
    expect(note).not.toHaveTextContent('may still be listed')
  })
})

describe('/account/player, where the player link returns', () => {
  it('opens the dialog on Connections and says it is connected', async () => {
    playerApi(status('connected'))
    renderAt('/account/player?result=connected')
    expect(await within(await dialog()).findByText('classbeat-player is connected.')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Connections' })).toHaveAttribute('aria-selected', 'true')
  })

  it('adds the note when the earlier link may remain', async () => {
    playerApi(status('connected'))
    renderAt('/account/player?result=connected&note=old_link_may_remain')
    expect(await within(await dialog()).findByText(/earlier link|may still be listed/)).toBeInTheDocument()
  })

  it.each([
    ['wrong_account', /belongs to someone else\. Nothing was connected/],
    ['access_denied', /link was not completed/],
    ['upstream_unavailable', /did not answer/],
    ['rate_limited', /Too many attempts/],
    ['invalid_response', /link was not completed/],
    ['link_in_progress', /finished in another window/],
    ['something_new', /link was not completed/],
  ])('words the result %s in plain text', async (result, sentence) => {
    playerApi(status('not_connected'))
    renderAt(`/account/player?result=${result}`)
    const alert = await within(await dialog()).findByRole('alert')
    expect(alert).toHaveTextContent(sentence)
    expect(alert).not.toHaveTextContent(result)
  })

  it('closing the dialog goes back to Connections, so the result is not shown twice', async () => {
    playerApi(status('connected'))
    renderAt('/account/player?result=connected')
    fireEvent.keyDown(await dialog(), { key: 'Escape' })
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'classbeat-player' })).toBeNull()
    })
    expect(window.location.pathname).toBe('/en/account')
    expect(window.location.search).toBe('')
  })
})

describe('an undecided removal', () => {
  const removal = (state = 'uncertain') => ({
    id: OP,
    kind: 'player_remove',
    state,
    job_id: 'e4444444-4444-4444-8444-444444444444',
    resolved_by: null,
    class_id: null,
    actionable_at: null,
  })

  it('is shown on the account page, since its class is gone', async () => {
    playerApi(status('connected'), { 'GET /api/external-operations': { operations: [removal()] } })
    renderAt('/account')
    fireEvent.click(await screen.findByRole('button', { name: 'Manage classbeat-player' }))
    const panel = await screen.findByTestId('uncertain-player')
    expect(panel).toHaveAttribute('data-kind', 'remove')
    expect(panel).toHaveTextContent('We could not confirm the player removed a deleted class')
    expect(panel).toHaveTextContent('Nothing is sent again by itself.')
  })

  it('Do it anyway says it will not send the removal again', async () => {
    const api = playerApi(status('connected'), {
      'GET /api/external-operations': { operations: [removal()] },
      [`POST /api/external-operations/${OP}/do-it-anyway`]: removal('failed'),
    })
    renderAt('/account')
    fireEvent.click(await screen.findByRole('button', { name: 'Manage classbeat-player' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Do it anyway' }))
    const confirmBox = screen.getByRole('group', { name: 'Confirm' })
    expect(confirmBox).toHaveTextContent('We will not send the removal again.')
    fireEvent.click(within(confirmBox).getByRole('button', { name: 'Yes, leave it' }))
    expect(await screen.findByText(/Left as it is\. Remove the class on the player/)).toBeInTheDocument()
    expect(api.to('POST', PLAYER)).toHaveLength(0)
  })

  it('a send of some class is not listed here', async () => {
    playerApi(status('connected'), {
      'GET /api/external-operations': { operations: [{ ...removal(), kind: 'player_send', class_id: 'c4444444-4444-4444-8444-444444444444' }] },
    })
    renderAt('/account')
    fireEvent.click(await screen.findByRole('button', { name: 'Manage classbeat-player' }))
    await dialog()
    expect(screen.queryByTestId('uncertain-player')).toBeNull()
  })
})

