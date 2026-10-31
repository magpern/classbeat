import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SIGNED_IN, fakeApi, json, reason } from '../../test/fakeApi'
import { renderAt } from '../../test/render'

const ADMIN = { ...SIGNED_IN, user: { ...SIGNED_IN.user, role: 'admin' } }
const USER_ID = '11111111-2222-3333-4444-555555555555'
const USERS = '/api/admin/users?offset=0&limit=50'
const INVITES = '/api/admin/invites?offset=0&limit=50'
const CODES = '/api/admin/redeem-codes?offset=0&limit=50'
const STEPUP_CODE = '424242'
const INVITE_CODE = 'INV1TE7C0DE5H0WN0NCE'

const SUMMARY = {
  id: USER_ID,
  email: 'rider@example.test',
  created_at: '2026-10-01T10:00:00Z',
  role: 'user',
  disabled_at: null,
  plan: { id: 'free', name: 'Free', source: null, ends_at: null },
  features: [],
  classes_held: 1,
  classes_limit: 3,
  cost_micros: 1_250_000,
}

const DETAIL = {
  ...SUMMARY,
  entitlements: [],
  ledger: [],
  connections: [
    { provider: 'mowl', status: 'connected', last_error: null, connected_at: '2026-10-02T00:00:00Z', last_used_at: null },
    { provider: 'spotify', status: 'not_connected', last_error: null, connected_at: null, last_used_at: null },
  ],
}

const INVITE = {
  id: 'aaaaaaaa-0000-0000-0000-000000000001',
  created_by: { id: 'u1', email: 'ann@example.test' },
  used_by: null,
  created_at: '2026-10-31T00:00:00Z',
  expires_at: '2026-11-07T00:00:00Z',
  used_at: null,
  state: 'active',
}

function adminApi(routes: Record<string, unknown> = {}) {
  return fakeApi({
    'GET /api/auth/session': ADMIN,
    'GET /api/admin/whoami': { email: 'ann@example.test' },
    [`GET ${USERS}`]: { users: [SUMMARY], next_offset: null },
    [`GET /api/admin/users/${USER_ID}`]: DETAIL,
    [`GET ${INVITES}`]: { invites: [], next_offset: null },
    [`GET ${CODES}`]: { redeem_codes: [], next_offset: null },
    ...(routes as Record<string, object>),
  })
}

function storageText(): string {
  const parts: string[] = []
  for (const store of [window.localStorage, window.sessionStorage]) {
    for (let i = 0; i < store.length; i++) {
      const key = store.key(i) ?? ''
      parts.push(key, store.getItem(key) ?? '')
    }
  }
  return parts.join('\n')
}

afterEach(() => {
  window.localStorage.clear()
  window.sessionStorage.clear()
})

async function enterStepUpCode(code: string) {
  const dialog = await screen.findByRole('dialog', { name: 'Enter a two-factor code' })
  fireEvent.change(within(dialog).getByLabelText('Authenticator or recovery code'), { target: { value: code } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }))
}

describe('step-up', () => {
  it('asks for a code on stepup_required, repeats the write once and keeps no code', async () => {
    let stepped = false
    const api = adminApi({
      [`POST /api/admin/users/${USER_ID}/disable`]: () =>
        stepped ? { id: USER_ID, disabled_at: '2026-10-31T00:00:00Z', sessions_revoked: 1 } : reason(403, 'stepup_required'),
      'POST /api/auth/mfa/stepup': () => {
        stepped = true
        return { ok: true }
      },
    })
    renderAt(`/admin/users/${USER_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Disable account' }))
    await enterStepUpCode(STEPUP_CODE)
    await waitFor(() => {
      expect(api.to('POST', `/api/admin/users/${USER_ID}/disable`)).toHaveLength(2)
    })
    expect(api.to('POST', '/api/auth/mfa/stepup').map((c) => c.body)).toEqual([{ code: STEPUP_CODE }])
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull()
    })
    expect(document.body.innerHTML).not.toContain(STEPUP_CODE)
    expect(storageText()).not.toContain(STEPUP_CODE)
    expect(window.location.href).not.toContain(STEPUP_CODE)
  })

  it('repeats only once: a second stepup_required is shown, not prompted again', async () => {
    const api = adminApi({
      [`POST /api/admin/users/${USER_ID}/disable`]: reason(403, 'stepup_required'),
      'POST /api/auth/mfa/stepup': { ok: true },
    })
    renderAt(`/admin/users/${USER_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Disable account' }))
    await enterStepUpCode(STEPUP_CODE)
    expect(await screen.findByText('This change needs a two-factor code.')).toBeInTheDocument()
    expect(api.to('POST', `/api/admin/users/${USER_ID}/disable`)).toHaveLength(2)
    expect(api.to('POST', '/api/auth/mfa/stepup')).toHaveLength(1)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('keeps the dialog open on a wrong code and clears the field', async () => {
    adminApi({
      [`POST /api/admin/users/${USER_ID}/disable`]: reason(403, 'stepup_required'),
      'POST /api/auth/mfa/stepup': reason(400, 'invalid_code'),
    })
    renderAt(`/admin/users/${USER_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Disable account' }))
    await enterStepUpCode('000000')
    const dialog = await screen.findByRole('dialog', { name: 'Enter a two-factor code' })
    expect(await within(dialog).findByText('That code is not right. Try again.')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Authenticator or recovery code')).toHaveValue('')
  })

  it('returns to the two-factor check on mfa_required', async () => {
    let expired = false
    adminApi({
      'GET /api/admin/whoami': () => (expired ? reason(403, 'mfa_required') : { email: 'ann@example.test' }),
      'GET /api/auth/mfa': { totp_enabled: true, recovery_codes_left: 9 },
      [`POST /api/admin/users/${USER_ID}/disable`]: () => {
        expired = true
        return reason(403, 'mfa_required')
      },
    })
    renderAt(`/admin/users/${USER_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Disable account' }))
    expect(await screen.findByRole('heading', { name: 'Two-factor check' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Admin' })).toBeNull()
  })

  it('a 401 from the step-up closes the dialog without repeating the write', async () => {
    const api = adminApi({
      [`POST /api/admin/users/${USER_ID}/disable`]: reason(403, 'stepup_required'),
      'POST /api/auth/mfa/stepup': reason(401, 'not_authenticated'),
    })
    renderAt(`/admin/users/${USER_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Disable account' }))
    await enterStepUpCode(STEPUP_CODE)
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull()
    })
    expect(api.to('POST', `/api/admin/users/${USER_ID}/disable`)).toHaveLength(1)
  })
})

describe('who sees the admin area', () => {
  it('a non-admin gets a plain page: no admin navigation, no admin request', async () => {
    const api = fakeApi({ 'GET /api/auth/session': SIGNED_IN })
    renderAt('/admin/users')
    expect(await screen.findByText('Your account is not an admin account.')).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Admin' })).toBeNull()
    expect(api.calls.filter((c) => c.path.startsWith('/api/admin'))).toEqual([])
  })

  it("the server's 403 is a plain page without the admin navigation", async () => {
    adminApi({ 'GET /api/admin/whoami': reason(403, 'forbidden') })
    renderAt('/admin/users')
    expect(await screen.findByText('Your account is not an admin account.')).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Admin' })).toBeNull()
  })

  it('an admin gets the navigation and the users list', async () => {
    adminApi()
    renderAt('/admin')
    const nav = await screen.findByRole('navigation', { name: 'Admin' })
    expect(within(nav).getByRole('link', { name: 'Users' })).toHaveAttribute('aria-current', 'page')
    expect(await screen.findByRole('link', { name: 'rider@example.test' })).toBeInTheDocument()
    expect(screen.getByText('$1.25')).toBeInTheDocument()
  })
})

describe('users', () => {
  it('sends the email prefix only in the API request, never in the page address', async () => {
    const api = adminApi({ 'GET /api/admin/users?q=rid&offset=0&limit=50': { users: [], next_offset: null } })
    renderAt('/admin/users')
    fireEvent.change(await screen.findByLabelText('Email starts with'), { target: { value: 'rid' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByText('No users found.')).toBeInTheDocument()
    expect(api.to('GET', '/api/admin/users?q=rid&offset=0&limit=50')).toHaveLength(1)
    expect(window.location.href).not.toContain('rid')
  })

  it('grants a plan with a note', async () => {
    const api = adminApi({
      [`POST /api/admin/users/${USER_ID}/entitlements`]: () =>
        json(
          {
            id: 'e1', plan_id: 'instructor', features: ['song_picking'], source: 'grant',
            starts_at: '2026-10-31T00:00:00Z', ends_at: null, granted_by: 'u1', note: 'tester',
            revoked_at: null, revoked_by: null, created_at: '2026-10-31T00:00:00Z', active: true,
          },
          201,
        ),
    })
    renderAt(`/admin/users/${USER_ID}`)
    await userEvent.click(await screen.findByLabelText('Song picking'))
    fireEvent.change(screen.getByLabelText('Note (optional, admins only)'), { target: { value: 'tester' } })
    fireEvent.click(screen.getByRole('button', { name: 'Grant' }))
    await waitFor(() => {
      expect(api.to('POST', `/api/admin/users/${USER_ID}/entitlements`)).toHaveLength(1)
    })
    expect(api.to('POST', `/api/admin/users/${USER_ID}/entitlements`)[0]?.body).toEqual({
      plan_id: 'instructor',
      features: ['song_picking'],
      note: 'tester',
    })
  })

  it('deletes only with the typed email, sent in the body and nowhere else', async () => {
    const typed = 'Rider@Example.test'
    const api = adminApi({ [`DELETE /api/admin/users/${USER_ID}`]: () => new Response(null, { status: 204 }) })
    renderAt(`/admin/users/${USER_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Delete account' }))
    const dialog = await screen.findByRole('dialog', { name: 'Delete this account?' })
    const confirm = within(dialog).getByRole('button', { name: 'Delete for good' })
    fireEvent.change(within(dialog).getByLabelText('Email'), { target: { value: 'other@example.test' } })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Email'), { target: { value: typed } })
    fireEvent.click(confirm)
    await waitFor(() => {
      expect(window.location.pathname).toBe('/admin/users')
    })
    const calls = api.to('DELETE', `/api/admin/users/${USER_ID}`)
    expect(calls.map((c) => c.body)).toEqual([{ email: typed }])
    expect(api.calls.some((c) => c.path.toLowerCase().includes('rider@'))).toBe(false)
    expect(storageText().toLowerCase()).not.toContain('rider@')
  })

  it('offers no disable or delete for an admin', async () => {
    adminApi({ [`GET /api/admin/users/${USER_ID}`]: { ...DETAIL, role: 'admin' } })
    renderAt(`/admin/users/${USER_ID}`)
    expect(await screen.findByText(/only be disabled or deleted from the command line/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Disable account' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Delete account' })).toBeNull()
  })

  it('shows an unknown user as gone', async () => {
    adminApi({ [`GET /api/admin/users/${USER_ID}`]: reason(404, 'not_found') })
    renderAt(`/admin/users/${USER_ID}`)
    expect(await screen.findByText(/There is no such user/)).toBeInTheDocument()
  })
})

describe('codes', () => {
  it('shows a created invite once, with a copy button, and not after navigation', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const api = adminApi({ 'POST /api/admin/invites': () => json({ code: INVITE_CODE, invite: INVITE }, 201) })
    renderAt('/admin/codes')
    fireEvent.click(await screen.findByRole('button', { name: 'Create invite' }))
    const panel = await screen.findByTestId('one-time-code')
    expect(within(panel).getByText(INVITE_CODE)).toBeInTheDocument()
    fireEvent.click(within(panel).getByRole('button', { name: 'Copy' }))
    expect(await within(panel).findByText('Copied.')).toBeInTheDocument()
    expect(writeText).toHaveBeenCalledWith(INVITE_CODE)
    expect(api.to('POST', '/api/admin/invites')[0]?.body).toEqual({ expires_days: 7 })

    fireEvent.click(within(screen.getByRole('navigation', { name: 'Admin' })).getByRole('link', { name: 'Users' }))
    await screen.findByRole('heading', { name: 'Users' })
    fireEvent.click(within(screen.getByRole('navigation', { name: 'Admin' })).getByRole('link', { name: 'Codes' }))
    await screen.findByRole('heading', { name: 'Invites' })
    expect(screen.queryByTestId('one-time-code')).toBeNull()
    expect(document.body.innerHTML).not.toContain(INVITE_CODE)
    expect(storageText()).not.toContain(INVITE_CODE)
    expect(window.location.href).not.toContain(INVITE_CODE)
  })

  it('closing the panel drops the code', async () => {
    adminApi({ 'POST /api/admin/invites': () => json({ code: INVITE_CODE, invite: INVITE }, 201) })
    renderAt('/admin/codes')
    fireEvent.click(await screen.findByRole('button', { name: 'Create invite' }))
    fireEvent.click(within(await screen.findByTestId('one-time-code')).getByRole('button', { name: 'Done' }))
    expect(document.body.innerHTML).not.toContain(INVITE_CODE)
  })

  it('creates a redeem code after a step-up and disables one', async () => {
    let stepped = false
    const code = {
      id: 'cccccccc-0000-0000-0000-000000000001', label: 'Beta', plan_id: 'instructor', features: [],
      grant_days: 30, max_redemptions: 10, redeemed_count: 0, expires_at: '2026-12-31T22:59:59Z',
      disabled_at: null, created_by: null, created_at: '2026-10-31T00:00:00Z', state: 'active',
    }
    const api = adminApi({
      [`GET ${CODES}`]: { redeem_codes: [code], next_offset: null },
      'POST /api/admin/redeem-codes': () =>
        stepped ? json({ code: 'REDEEMC0DE0NCE', redeem_code: code }, 201) : reason(403, 'stepup_required'),
      'POST /api/auth/mfa/stepup': () => {
        stepped = true
        return { ok: true }
      },
      [`POST /api/admin/redeem-codes/${code.id}/disable`]: { ...code, disabled_at: '2026-10-31T01:00:00Z', state: 'disabled' },
    })
    renderAt('/admin/codes')
    fireEvent.change(await screen.findByLabelText('Label'), { target: { value: 'Beta' } })
    fireEvent.change(screen.getByLabelText('Can be redeemed until'), { target: { value: '2026-12-31' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create redeem code' }))
    await enterStepUpCode(STEPUP_CODE)
    expect(await screen.findByText('REDEEMC0DE0NCE')).toBeInTheDocument()
    const sent = api.to('POST', '/api/admin/redeem-codes')
    expect(sent).toHaveLength(2)
    expect(sent[1]?.body).toMatchObject({ label: 'Beta', plan_id: 'instructor', grant_days: 30, max_redemptions: 10 })
    fireEvent.click(screen.getByRole('button', { name: 'Disable' }))
    await waitFor(() => {
      expect(api.to('POST', `/api/admin/redeem-codes/${code.id}/disable`)).toHaveLength(1)
    })
  })
})
