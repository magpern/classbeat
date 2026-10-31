import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SIGNED_IN, fakeApi, reason } from '../test/fakeApi'
import { renderAt } from '../test/render'

const goToProvider = vi.hoisted(() => vi.fn<(url: string) => void>())
vi.mock('../lib/navigation', () => ({ goToProvider }))

function type(label: RegExp | string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
}

const IDENTITIES = {
  identities: [{ id: 'i1', provider: 'github', email_at_link: null, created_at: '2026-01-01T00:00:00Z' }],
  has_password: true,
  available: ['github', 'google'],
}
const MFA_OFF = { totp_enabled: false, recovery_codes_left: 0 }

describe('sign-in page', () => {
  it('signs in with email and password and goes to the account page', async () => {
    let signedIn = false
    const api = fakeApi({
      'GET /api/auth/session': () => (signedIn ? SIGNED_IN : { authenticated: false, csrf_token: 'a' }),
      'GET /api/auth/oauth/providers': { providers: [] },
      'POST /api/auth/login': () => {
        signedIn = true
        return { id: 'u1', email: 'ann@example.test', role: 'user' }
      },
      'GET /api/auth/identities': IDENTITIES,
      'GET /api/auth/mfa': MFA_OFF,
    })
    renderAt('/signin')
    type('Email', 'ann@example.test')
    type('Password', 'correct horse battery')
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('heading', { name: 'Your account' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/account')
    expect(api.to('POST', '/api/auth/login')[0]?.body).toEqual({
      email: 'ann@example.test',
      password: 'correct horse battery',
    })
    expect(api.to('POST', '/api/auth/login')[0]?.headers.get('X-CSRF-Token')).toBe('a')
  })

  it('shows buttons for the enabled providers and starts sign-in with the invite', async () => {
    goToProvider.mockClear()
    const api = fakeApi({
      'GET /api/auth/oauth/providers': { providers: ['github', 'google'] },
      'POST /api/auth/oauth/google/start': { authorization_url: 'https://idp.example/authorize?x=1' },
    })
    renderAt('/signin')
    expect(await screen.findByRole('button', { name: 'Continue with GitHub' })).toBeInTheDocument()
    type(/Invite code/, ' inv-1 ')
    fireEvent.click(screen.getByRole('button', { name: 'Continue with Google' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalledWith('https://idp.example/authorize?x=1')
    })
    expect(api.to('POST', '/api/auth/oauth/google/start')[0]?.body).toEqual({
      purpose: 'sign_in',
      invite_code: 'inv-1',
      return_to: null,
    })
  })

  it('explains the outcome code of a failed provider sign-in', async () => {
    fakeApi({ 'GET /api/auth/oauth/providers': { providers: [] } })
    renderAt('/signin?oauth=account_exists')
    expect(await screen.findByRole('alert')).toHaveTextContent(/account with that email already exists/)
  })

  it('does not show unknown error codes', async () => {
    fakeApi({ 'GET /api/auth/oauth/providers': { providers: [] } })
    renderAt('/signin?oauth=<script>')
    expect(await screen.findByRole('alert')).not.toHaveTextContent('script')
  })

  it('sends admins to the admin page', async () => {
    fakeApi({
      'GET /api/auth/oauth/providers': { providers: [] },
      'POST /api/auth/login': { id: 'u', email: 'a@example.test', role: 'admin' },
      'GET /api/admin/whoami': reason(403, 'mfa_required'),
      'GET /api/auth/mfa': { totp_enabled: true, recovery_codes_left: 10 },
    })
    renderAt('/signin')
    type('Email', 'a@example.test')
    type('Password', 'correct horse battery')
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe('/admin')
    })
  })
})

describe('account page', () => {
  const base = {
    'GET /api/auth/session': SIGNED_IN,
    'GET /api/auth/identities': IDENTITIES,
    'GET /api/auth/mfa': MFA_OFF,
  }

  it('asks anonymous visitors to sign in', async () => {
    fakeApi({})
    renderAt('/account')
    expect(await screen.findByText('Sign in first')).toBeInTheDocument()
  })

  it('lists methods and unlinks one', async () => {
    let unlinked = false
    const api = fakeApi({
      ...base,
      'GET /api/auth/identities': () => (unlinked ? { ...IDENTITIES, identities: [] } : IDENTITIES),
      'DELETE /api/auth/identities/i1': () => {
        unlinked = true
        return new Response(null, { status: 204 })
      },
    })
    renderAt('/account#security')
    fireEvent.click(await screen.findByRole('button', { name: 'Unlink GitHub' }))
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Unlink GitHub' })).toBeNull()
    })
    expect(api.to('DELETE', '/api/auth/identities/i1')).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Link GitHub' })).toBeInTheDocument()
  })

  it('refuses to unlink the last method with a clear message', async () => {
    fakeApi({ ...base, 'DELETE /api/auth/identities/i1': reason(409, 'last_sign_in_method') })
    renderAt('/account#security')
    fireEvent.click(await screen.findByRole('button', { name: 'Unlink GitHub' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/only way to sign in/)
  })

  it('prompts for the password on reauth_required, then repeats the change', async () => {
    let fresh = false
    const api = fakeApi({
      ...base,
      'POST /api/auth/password/change': () => (fresh ? { status: 'ok' } : reason(403, 'reauth_required')),
      'POST /api/auth/reauth': () => {
        fresh = true
        return { status: 'ok' }
      },
    })
    renderAt('/account#security')
    await screen.findByRole('button', { name: 'Unlink GitHub' })
    type('New password', 'a brand new passphrase')
    type('Repeat the new password', 'a brand new passphrase')
    fireEvent.click(screen.getByRole('button', { name: 'Save password' }))

    const dialog = await screen.findByRole('dialog')
    fireEvent.change(await within(dialog).findByLabelText('Password'), { target: { value: 'old password here' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm with password' }))

    expect(await screen.findByText(/Password saved/)).toBeInTheDocument()
    expect(api.to('POST', '/api/auth/reauth')[0]?.body).toEqual({ password: 'old password here' })
    expect(api.to('POST', '/api/auth/password/change')).toHaveLength(2)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('returns from a provider re-authentication to a page that says to repeat the change', async () => {
    goToProvider.mockClear()
    const api = fakeApi({
      ...base,
      'POST /api/auth/password/change': reason(403, 'reauth_required'),
      'POST /api/auth/oauth/github/start': { authorization_url: 'https://idp.example/a' },
    })
    renderAt('/account#security')
    await screen.findByRole('button', { name: 'Unlink GitHub' })
    type('New password', 'a brand new passphrase')
    type('Repeat the new password', 'a brand new passphrase')
    fireEvent.click(screen.getByRole('button', { name: 'Save password' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Confirm with GitHub' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalledWith('https://idp.example/a')
    })
    const sent = api.to('POST', '/api/auth/oauth/github/start')[0]?.body as {
      purpose: string
      return_to: string
    }
    expect(sent.purpose).toBe('reauth')
    expect(sent.return_to).toBe('/account?oauth=reauthed')

    // The page the provider sends the user back to shows the hint.
    cleanup()
    renderAt(sent.return_to)
    expect(await screen.findByText(/Repeat the change you were making/)).toBeInTheDocument()
  })

  it('does not treat inherited object keys as outcome codes', async () => {
    fakeApi({
      'GET /api/auth/session': SIGNED_IN,
      'GET /api/auth/identities': IDENTITIES,
      'GET /api/auth/mfa': MFA_OFF,
    })
    renderAt('/account?oauth=toString')
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be completed/)
    expect(screen.queryByText(/Repeat the change/)).toBeNull()
  })

  it('prompts for a two-factor code on stepup_required', async () => {
    let stepped = false
    const api = fakeApi({
      ...base,
      'POST /api/auth/email/change': () =>
        stepped ? new Response(null, { status: 202 }) : reason(403, 'stepup_required'),
      'POST /api/auth/mfa/stepup': () => {
        stepped = true
        return { ok: true }
      },
    })
    renderAt('/account#security')
    await screen.findByRole('button', { name: 'Unlink GitHub' })
    type('New email address', 'new@example.test')
    fireEvent.click(screen.getByRole('button', { name: 'Change email' }))
    const dialog = await screen.findByRole('dialog')
    type('Authenticator or recovery code', '123456')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }))
    expect(await screen.findByText(/confirmation link is on its way/)).toBeInTheDocument()
    expect(api.to('POST', '/api/auth/mfa/stepup')[0]?.body).toEqual({ code: '123456' })
  })

  it('cancelling the prompt leaves the change undone without an error', async () => {
    fakeApi({ ...base, 'POST /api/auth/password/change': reason(403, 'reauth_required') })
    renderAt('/account#security')
    await screen.findByRole('button', { name: 'Unlink GitHub' })
    type('New password', 'a brand new passphrase')
    type('Repeat the new password', 'a brand new passphrase')
    fireEvent.click(screen.getByRole('button', { name: 'Save password' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull()
    })
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByText(/Password saved/)).toBeNull()
  })

  it('signs out everywhere', async () => {
    const api = fakeApi({ ...base, 'POST /api/auth/logout-all': new Response(null, { status: 204 }) })
    renderAt('/account#security')
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out everywhere' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/auth/logout-all')).toHaveLength(1)
    })
    // the CSRF token of the session goes along
    expect(api.to('POST', '/api/auth/logout-all')[0]?.headers.get('X-CSRF-Token')).toBe('csrf-session')
  })

  it('enrols an authenticator app and shows the recovery codes once', async () => {
    const api = fakeApi({
      ...base,
      'POST /api/auth/mfa/totp/start': {
        secret: 'JBSWY3DPEHPK3PXP',
        otpauth_uri: 'otpauth://totp/x',
        qr_svg:
          '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12"><g transform="scale(4)">' +
          '<path onload="alert(1)" fill="#fff" d="M0 0h3v3h-3z"/><script>alert(1)</script>' +
          '<image href="https://evil.example/x.png"/><a href="https://evil.example"><path d="M1 1"/></a></g></svg>',
        pending: 'sealed',
      },
      'POST /api/auth/mfa/totp/confirm': { recovery_codes: ['AAAAA11111', 'BBBBB22222'] },
    })
    renderAt('/account#security')
    fireEvent.click(await screen.findByRole('button', { name: 'Set up an authenticator app' }))
    // Drawn inline (no data: URL, which the CSP's img-src does not allow), shapes only.
    const qr = await screen.findByRole('img', { name: 'QR code for your authenticator app' })
    expect(qr.tagName.toLowerCase()).toBe('svg')
    expect(qr.getAttribute('viewBox')).toBe('0 0 12 12')
    expect(qr.querySelector('g')?.getAttribute('transform')).toBe('scale(4)')
    expect(qr.querySelector('path')?.getAttribute('d')).toBe('M0 0h3v3h-3z')
    expect(qr.querySelector('script, image, foreignObject, a')).toBeNull()
    expect(qr.querySelector('path')?.getAttribute('onload')).toBeNull()
    expect(screen.getByText('JBSWY3DPEHPK3PXP')).toBeInTheDocument()
    type('Code from the app', '123456')
    fireEvent.click(screen.getByRole('button', { name: 'Turn on two-factor' }))
    expect(await screen.findByText('AAAAA11111')).toBeInTheDocument()
    expect(api.to('POST', '/api/auth/mfa/totp/confirm')[0]?.body).toEqual({
      pending: 'sealed',
      code: '123456',
    })
  })
})

const ADMIN_SESSION = { ...SIGNED_IN, user: { ...SIGNED_IN.user, role: 'admin' } }

describe('admin page', () => {
  it('asks an enrolled admin for a code and then shows the area', async () => {
    let verified = false
    const api = fakeApi({
      'GET /api/auth/session': ADMIN_SESSION,
      'GET /api/admin/users?offset=0&limit=50': { users: [], next_offset: null },
      'GET /api/admin/whoami': () =>
        verified ? { id: 'u1', email: 'ann@example.test', role: 'admin' } : reason(403, 'mfa_required'),
      'GET /api/auth/mfa': { totp_enabled: true, recovery_codes_left: 9 },
      'POST /api/auth/mfa/verify': () => {
        verified = true
        return { ok: true }
      },
    })
    renderAt('/admin')
    fireEvent.change(await screen.findByLabelText('Authenticator or recovery code'), { target: { value: '654321' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))
    expect(await screen.findByText(/Signed in as admin: ann@example.test/)).toBeInTheDocument()
    expect(api.to('POST', '/api/auth/mfa/verify')[0]?.body).toEqual({ code: '654321' })
  })

  it('offers enrolment to an admin without a second factor', async () => {
    fakeApi({
      'GET /api/auth/session': ADMIN_SESSION,
      'GET /api/admin/whoami': reason(403, 'mfa_required'),
      'GET /api/auth/mfa': MFA_OFF,
    })
    renderAt('/admin')
    expect(await screen.findByRole('button', { name: 'Set up an authenticator app' })).toBeInTheDocument()
  })

  it('turns non-admins away', async () => {
    fakeApi({ 'GET /api/auth/session': ADMIN_SESSION, 'GET /api/admin/whoami': reason(403, 'forbidden') })
    renderAt('/admin')
    expect(await screen.findByText(/not an admin account/)).toBeInTheDocument()
  })
})
