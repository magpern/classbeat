import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SIGNED_IN, fakeApi, json, reason } from '../test/fakeApi'
import { setWidth } from '../test/layout'
import { renderAt } from '../test/render'

const goToProvider = vi.hoisted(() => vi.fn<(url: string) => void>())
vi.mock('../lib/navigation', () => ({ goToProvider }))

const IDENTITIES = { identities: [], has_password: true, available: [] }
const MFA_OFF = { totp_enabled: false, recovery_codes_left: 0 }
const MFA_ON = { totp_enabled: true, recovery_codes_left: 8 }
const DEVICES = [
  {
    id: 'd1',
    created_at: '2026-10-01T08:00:00Z',
    last_seen_at: '2026-10-31T08:00:00Z',
    expires_at: '2026-11-30T08:00:00Z',
    user_agent: 'Firefox on Linux',
    ip: '192.0.2.10',
    current: true,
  },
  {
    id: 'd2',
    created_at: '2026-10-02T08:00:00Z',
    last_seen_at: '2026-10-30T08:00:00Z',
    expires_at: '2026-11-29T08:00:00Z',
    user_agent: 'Safari on iPhone',
    ip: '198.51.100.7',
    current: false,
  },
]
const CONSENT_TEXT =
  'classbeat manages one private working playlist in your Spotify account, named classbeat working copy, to ask for song candidates and have MOWL analyse them. It is overwritten on each pick; anyone with its link could open it.'

const base = {
  'GET /api/auth/session': SIGNED_IN,
  'GET /api/auth/identities': IDENTITIES,
  'GET /api/auth/mfa': MFA_OFF,
  'GET /api/auth/sessions': DEVICES,
}

const TABS = ['Connections', 'Plan and usage', 'Sign-in and security', 'Your data', 'Appearance']

async function confirmWithPassword(dialog: HTMLElement, password: string) {
  fireEvent.change(await within(dialog).findByLabelText('Password'), { target: { value: password } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm with password' }))
}

describe('account tabs', () => {
  it('are a Radix tab list with the five tabs, Connections open by default', async () => {
    fakeApi(base)
    renderAt('/account')
    const list = await screen.findByRole('tablist', { name: 'Account' })
    const tabs = within(list).getAllByRole('tab')
    expect(tabs.map((tab) => tab.textContent)).toEqual(TABS)
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    const panel = screen.getByRole('tabpanel')
    expect(panel).toHaveAttribute('aria-labelledby', tabs[0]?.id)
    expect(within(panel).getByRole('heading', { name: 'Swedebeat' })).toBeInTheDocument()
    expect(await within(panel).findByText('Swedebeat is coming soon.')).toBeInTheDocument()
  })

  it('move with the arrow keys and keep the open tab in the address', async () => {
    fakeApi(base)
    renderAt('/account')
    const list = await screen.findByRole('tablist', { name: 'Account' })
    const tabs = within(list).getAllByRole('tab')
    tabs[0]?.focus()
    fireEvent.keyDown(tabs[0] as HTMLElement, { key: 'ArrowRight' })
    await waitFor(() => {
      expect(tabs[1]).toHaveFocus()
    })
    await waitFor(() => {
      expect(tabs[1]).toHaveAttribute('aria-selected', 'true')
    })
    expect(window.location.hash).toBe('#plan')
    fireEvent.keyDown(tabs[1] as HTMLElement, { key: 'End' })
    await waitFor(() => {
      expect(tabs[4]).toHaveFocus()
    })
    await waitFor(() => {
      expect(tabs[4]).toHaveAttribute('aria-selected', 'true')
    })
    expect(screen.getByRole('radiogroup', { name: /theme/i })).toBeInTheDocument()
    expect(window.location.hash).toBe('#appearance')
    fireEvent.keyDown(tabs[4] as HTMLElement, { key: 'ArrowRight' })
    await waitFor(() => {
      expect(tabs[0]).toHaveFocus()
    })
  })

  it('open Sign-in and security when coming back from a provider', async () => {
    fakeApi(base)
    renderAt('/account?oauth=linked')
    expect(await screen.findByRole('tab', { name: 'Sign-in and security' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText(/sign-in method is linked/)).toBeInTheDocument()
  })

  it.each([390, 1440])('lay out at %i px with the profile header and every tab reachable', async (width) => {
    setWidth(width)
    fakeApi(base)
    renderAt('/account')
    expect(await screen.findByRole('heading', { name: 'Your account' })).toBeInTheDocument()
    expect(screen.getByTestId('account-email')).toHaveTextContent('ann@example.test')
    expect(screen.getByText('ann', { selector: 'p' })).toBeInTheDocument()
    expect(screen.getByLabelText('New email address')).toBeInTheDocument()
    const list = screen.getByRole('tablist', { name: 'Account' })
    // The tab list scrolls sideways on a phone instead of wrapping or widening the page.
    expect(list).toHaveClass('overflow-x-auto')
    for (const tab of within(list).getAllByRole('tab')) expect(tab).toHaveClass('whitespace-nowrap')
  })
})

describe('plan and usage tab', () => {
  it('keeps the token and cost details behind Show usage details', async () => {
    fakeApi(base)
    renderAt('/account#plan')
    const summary = await screen.findByText('Show usage details')
    expect(summary.closest('details')).not.toHaveAttribute('open')
  })
})

describe('signed-in devices', () => {
  it('lists the sessions without their address and signs out another device', async () => {
    let devices = DEVICES
    const api = fakeApi({
      ...base,
      'GET /api/auth/sessions': () => devices,
      'DELETE /api/auth/sessions/d2': () => {
        devices = DEVICES.slice(0, 1)
        return new Response(null, { status: 204 })
      },
    })
    renderAt('/account#security')
    const list = await screen.findByTestId('devices')
    expect(list).toHaveTextContent('Firefox on Linux')
    expect(list).toHaveTextContent('This device')
    expect(list).not.toHaveTextContent('192.0.2.10')
    expect(list).not.toHaveTextContent('198.51.100.7')
    expect(within(list).getAllByRole('button', { name: 'Sign out this device' })).toHaveLength(1)
    fireEvent.click(within(list).getByRole('button', { name: 'Sign out this device' }))
    await waitFor(() => {
      expect(screen.getByTestId('devices')).not.toHaveTextContent('Safari on iPhone')
    })
    expect(api.to('DELETE', '/api/auth/sessions/d2')[0]?.headers.get('X-CSRF-Token')).toBe('csrf-session')
  })
})

describe('the consent switch', () => {
  it('sends the same PUT through recent authentication and shows the versioned sentence', async () => {
    let fresh = false
    let given = false
    const spotify = () => ({
      status: 'connected',
      client_id: '0123456789abcdef0123456789abcdef',
      last_error: null,
      last_error_message: null,
      callback_url: 'http://127.0.0.1:8000/oauth/spotify/callback',
      pool_consent: given,
      consent_version: given ? 1 : null,
      consent_text: CONSENT_TEXT,
      song_picking_enabled: true,
    })
    const api = fakeApi({
      ...base,
      'GET /api/connections/spotify': spotify,
      'PUT /api/connections/spotify/pool-consent': () => {
        if (!fresh) return reason(403, 'reauth_required')
        given = true
        return new Response(null, { status: 204 })
      },
      'POST /api/auth/reauth': () => {
        fresh = true
        return { status: 'ok' }
      },
    })
    renderAt('/account')
    const toggle = await screen.findByRole('switch', { name: 'Allow the working playlist' })
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByTestId('spotify-consent-text').textContent).toBe(CONSENT_TEXT)
    fireEvent.click(toggle)
    const dialog = await screen.findByRole('dialog')
    await confirmWithPassword(dialog, 'my password here')
    expect(await screen.findByText(/classbeat may use the working playlist/)).toBeInTheDocument()
    const puts = api.to('PUT', '/api/connections/spotify/pool-consent')
    expect(puts).toHaveLength(2)
    // The same request as the old "I agree" button: no body, the server records the version of
    // the sentence it sent, and the CSRF token goes along.
    for (const call of puts) {
      expect(call.body).toBeUndefined()
      expect(call.headers.get('X-CSRF-Token')).toBe('csrf-session')
    }
    expect(api.to('POST', '/api/auth/reauth')[0]?.body).toEqual({ password: 'my password here' })
    expect(screen.getByRole('switch', { name: 'Allow the working playlist' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByTestId('spotify-consent-text').textContent).toBe(CONSENT_TEXT)
  })
})

describe('delete my account', () => {
  it('needs the typed email, then recent authentication, and sends the typed email once confirmed', async () => {
    let fresh = false
    let deleted = false
    const api = fakeApi({
      ...base,
      'GET /api/auth/session': () => (deleted ? { authenticated: false, csrf_token: 'a' } : SIGNED_IN),
      'POST /api/account/delete': () => {
        if (!fresh) return reason(403, 'reauth_required')
        deleted = true
        return new Response(null, { status: 204 })
      },
      'POST /api/auth/reauth': () => {
        fresh = true
        return { status: 'ok' }
      },
    })
    renderAt('/account#data')
    fireEvent.click(await screen.findByRole('button', { name: 'Delete my account' }))
    const confirm = await screen.findByRole('dialog', { name: 'Delete your account?' })
    const go = within(confirm).getByRole('button', { name: 'Delete for good' })
    expect(go).toBeDisabled()
    fireEvent.change(within(confirm).getByLabelText('Your email'), { target: { value: 'someone@else.test' } })
    expect(go).toBeDisabled()
    fireEvent.change(within(confirm).getByLabelText('Your email'), { target: { value: 'Ann@Example.test' } })
    expect(go).toBeEnabled()
    expect(api.to('POST', '/api/account/delete')).toHaveLength(0)
    fireEvent.click(go)

    const prompt = await screen.findByRole('dialog')
    await confirmWithPassword(prompt, 'my password here')
    await waitFor(() => {
      expect(window.location.pathname).toBe('/')
    })
    const calls = api.to('POST', '/api/account/delete')
    expect(calls).toHaveLength(2)
    // The server checks the typed address; it goes in the body of the delete, never in an address.
    for (const call of calls) expect(call.body).toEqual({ confirm_email: 'Ann@Example.test' })
    expect(api.calls.some((call) => call.path.toLowerCase().includes('ann@'))).toBe(false)
  })

  it('asks for a two-factor code when the server wants a step-up', async () => {
    let stepped = false
    const api = fakeApi({
      ...base,
      'GET /api/auth/mfa': MFA_ON,
      'POST /api/account/delete': () =>
        stepped ? new Response(null, { status: 204 }) : reason(403, 'stepup_required'),
      'POST /api/auth/mfa/stepup': () => {
        stepped = true
        return { ok: true }
      },
    })
    renderAt('/account#data')
    fireEvent.click(await screen.findByRole('button', { name: 'Delete my account' }))
    const confirm = await screen.findByRole('dialog', { name: 'Delete your account?' })
    fireEvent.change(within(confirm).getByLabelText('Your email'), { target: { value: 'ann@example.test' } })
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete for good' }))
    const prompt = await screen.findByRole('dialog')
    fireEvent.change(within(prompt).getByLabelText('Authenticator or recovery code'), { target: { value: '123456' } })
    fireEvent.click(within(prompt).getByRole('button', { name: 'Continue' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/account/delete')).toHaveLength(2)
    })
    expect(api.to('POST', '/api/auth/mfa/stepup')[0]?.body).toEqual({ code: '123456' })
  })

  it('does nothing when kept', async () => {
    const api = fakeApi(base)
    renderAt('/account#data')
    fireEvent.click(await screen.findByRole('button', { name: 'Delete my account' }))
    const confirm = await screen.findByRole('dialog', { name: 'Delete your account?' })
    fireEvent.click(within(confirm).getByRole('button', { name: 'Keep my account' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull()
    })
    expect(api.to('POST', '/api/account/delete')).toHaveLength(0)
  })
})

describe('delete my account, refused', () => {
  it('shows the answer of the server when it does not accept the typed address', async () => {
    fakeApi({ ...base, 'POST /api/account/delete': () => reason(400, 'confirmation_mismatch') })
    renderAt('/account#data')
    fireEvent.click(await screen.findByRole('button', { name: 'Delete my account' }))
    const confirm = await screen.findByRole('dialog', { name: 'Delete your account?' })
    fireEvent.change(within(confirm).getByLabelText('Your email'), { target: { value: 'ann@example.test' } })
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete for good' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Nothing was deleted')
    expect(window.location.pathname).toBe('/account')
  })
})

describe('download my data', () => {
  it('asks for recent authentication, then saves the export as a file', async () => {
    let fresh = false
    const saved: Blob[] = []
    const create = vi.fn((blob: Blob) => {
      saved.push(blob)
      return 'blob:export'
    })
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const api = fakeApi({
      ...base,
      'POST /api/account/export': () => (fresh ? { format: 'classbeat-export', version: 1 } : reason(403, 'reauth_required')),
      'POST /api/auth/reauth': () => {
        fresh = true
        return { status: 'ok' }
      },
    })
    renderAt('/account#data')
    fireEvent.click(await screen.findByRole('button', { name: 'Download my data' }))
    const prompt = await screen.findByRole('dialog')
    await confirmWithPassword(prompt, 'my password here')
    await waitFor(() => {
      expect(click).toHaveBeenCalledTimes(1)
    })
    expect(api.to('POST', '/api/account/export')).toHaveLength(2)
    expect(JSON.parse((await saved[0]?.text()) ?? '')).toEqual({ format: 'classbeat-export', version: 1 })
    expect(await screen.findByText('Your data was saved to a file.')).toBeInTheDocument()
    click.mockRestore()
  })

  it('says so when too many exports were asked for', async () => {
    fakeApi({ ...base, 'POST /api/account/export': () => json({ detail: { reason: 'rate_limited' } }, 429) })
    renderAt('/account#data')
    fireEvent.click(await screen.findByRole('button', { name: 'Download my data' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Too many attempts')
  })
})

describe('removing two-factor', () => {
  it('needs a confirmation, then the step-up the server asks for', async () => {
    let stepped = false
    let removed = false
    const api = fakeApi({
      ...base,
      'GET /api/auth/mfa': () => (removed ? MFA_OFF : MFA_ON),
      'DELETE /api/auth/mfa/totp': () => {
        if (!stepped) return reason(403, 'stepup_required')
        removed = true
        return new Response(null, { status: 204 })
      },
      'POST /api/auth/mfa/stepup': () => {
        stepped = true
        return { ok: true }
      },
    })
    renderAt('/account#security')
    fireEvent.click(await screen.findByRole('button', { name: 'Remove two-factor' }))
    const group = screen.getByRole('group', { name: 'Confirm removing two-factor' })
    fireEvent.click(within(group).getByRole('button', { name: 'Keep two-factor' }))
    expect(api.to('DELETE', '/api/auth/mfa/totp')).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: 'Remove two-factor' }))
    fireEvent.click(
      within(screen.getByRole('group', { name: 'Confirm removing two-factor' })).getByRole('button', {
        name: 'Yes, remove two-factor',
      }),
    )
    const prompt = await screen.findByRole('dialog')
    fireEvent.change(within(prompt).getByLabelText('Authenticator or recovery code'), { target: { value: '654321' } })
    fireEvent.click(within(prompt).getByRole('button', { name: 'Continue' }))
    expect(await screen.findByRole('button', { name: 'Set up an authenticator app' })).toBeInTheDocument()
    expect(api.to('DELETE', '/api/auth/mfa/totp')).toHaveLength(2)
    expect(api.to('POST', '/api/auth/mfa/stepup')[0]?.body).toEqual({ code: '654321' })
  })
})
