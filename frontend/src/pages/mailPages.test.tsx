import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SIGNED_IN, fakeApi, reason } from '../test/fakeApi'
import { renderAt } from '../test/render'

function type(label: RegExp | string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
}

describe('sign-up', () => {
  it('posts email, password and invite, then asks the user to check the inbox', async () => {
    const api = fakeApi({ 'POST /api/auth/signup': new Response(null, { status: 202 }) })
    renderAt('/signup')
    type('Invite code', 'abc')
    type('Email', 'ann@example.test')
    type('Password', 'correct horse battery')
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByText('Check your inbox')).toBeInTheDocument()
    expect(api.to('POST', '/api/auth/signup')[0]?.body).toEqual({
      email: 'ann@example.test',
      password: 'correct horse battery',
      invite_code: 'abc',
    })
  })

  it('shows the invite error in plain words', async () => {
    fakeApi({ 'POST /api/auth/signup': reason(400, 'invalid_invite') })
    renderAt('/signup')
    type('Email', 'ann@example.test')
    type('Password', 'correct horse battery')
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/invite code is not valid/)
  })
})

describe('sign-up confirmation page', () => {
  it('strips the token from the address bar, posts it with the password and never signs in', async () => {
    const api = fakeApi({ 'POST /api/auth/signup/confirm': { status: 'created' } })
    renderAt('/signup/confirm#token=secret-token')
    expect(window.location.hash).toBe('')
    expect(window.location.pathname).toBe('/signup/confirm')

    type('Password', 'correct horse battery')
    fireEvent.click(screen.getByRole('button', { name: 'Confirm account' }))

    expect(await screen.findByText('Account created')).toBeInTheDocument()
    expect(api.to('POST', '/api/auth/signup/confirm')[0]?.body).toEqual({
      token: 'secret-token',
      password: 'correct horse battery',
    })
    expect(api.to('POST', '/api/auth/login')).toHaveLength(0)
    // the session is only read once, on start: the browser stays signed out
    expect(screen.getByRole('navigation', { name: 'Account' })).toHaveTextContent('Sign up')
  })

  it('explains a wrong password and keeps the form', async () => {
    fakeApi({ 'POST /api/auth/signup/confirm': reason(400, 'wrong_password') })
    renderAt('/signup/confirm#token=t')
    type('Password', 'nope')
    fireEvent.click(screen.getByRole('button', { name: 'Confirm account' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/not the password you chose/)
    expect(screen.getByRole('button', { name: 'Confirm account' })).toBeInTheDocument()
  })

  it('tells a provider sign-up in the wrong browser to finish where it started', async () => {
    fakeApi({ 'POST /api/auth/signup/confirm': reason(400, 'wrong_browser') })
    renderAt('/signup/confirm#token=t')
    fireEvent.click(screen.getByRole('button', { name: 'Confirm account' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/device and browser where you started/)
  })

  it('asks for the mail link when there is no token', () => {
    fakeApi({})
    renderAt('/signup/confirm')
    expect(screen.getByRole('alert')).toHaveTextContent(/needs the link/)
  })
})

describe('provider email page', () => {
  it('sends only the address, then tells a mistyper to start the provider sign-in again', async () => {
    const api = fakeApi({
      'GET /api/auth/oauth/pending': { pending: true, provider: 'microsoft', awaiting_email: true },
      'POST /api/auth/oauth/pending/email': new Response(JSON.stringify({}), { status: 202 }),
    })
    renderAt('/signin/email')
    await screen.findByLabelText("Email")
    type('Email', 'ann@example.test')
    fireEvent.click(await screen.findByRole('button', { name: 'Send confirmation link' }))

    expect(await screen.findByText(/a second try would not send anything/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Start the provider sign-in again/ })).toHaveAttribute(
      'href',
      '/signin',
    )
    expect(screen.queryByRole('button', { name: 'Send confirmation link' })).toBeNull()
    expect(api.to('POST', '/api/auth/oauth/pending/email')[0]?.body).toEqual({
      email: 'ann@example.test',
    })
  })

  it('says so when no sign-in is waiting', async () => {
    fakeApi({ 'GET /api/auth/oauth/pending': { pending: false, provider: null, awaiting_email: false } })
    renderAt('/signin/email')
    expect(await screen.findByText('Nothing to finish here')).toBeInTheDocument()
  })

  it('is where the callback sends ?oauth=email_needed', async () => {
    fakeApi({ 'GET /api/auth/oauth/pending': { pending: true, provider: 'microsoft', awaiting_email: true } })
    renderAt('/signin?oauth=email_needed')
    expect(await screen.findByText('Confirm your email address')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/signin/email')
  })
})

describe('reset page', () => {
  it('requests a link for an address', async () => {
    const api = fakeApi({ 'POST /api/auth/password/reset/request': new Response(null, { status: 202 }) })
    renderAt('/reset')
    type('Email', 'ann@example.test')
    fireEvent.click(screen.getByRole('button', { name: 'Send reset link' }))
    expect(await screen.findByText('Check your inbox')).toBeInTheDocument()
    expect(api.to('POST', '/api/auth/password/reset/request')[0]?.body).toEqual({
      email: 'ann@example.test',
    })
  })

  it('lists linked methods and removes the ticked ones in the same step', async () => {
    const api = fakeApi({
      'POST /api/auth/password/reset/info': { methods: [{ id: 'm1', provider: 'github' }, { id: 'm2', provider: 'google' }] },
      'POST /api/auth/password/reset/complete': { status: 'ok' },
    })
    renderAt('/reset#token=reset-token')
    expect(window.location.hash).toBe('')
    fireEvent.click(await screen.findByLabelText('Remove GitHub'))
    type('New password', 'a brand new passphrase')
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }))

    expect(await screen.findByText('Password changed')).toBeInTheDocument()
    expect(api.to('POST', '/api/auth/password/reset/info')[0]?.body).toEqual({ token: 'reset-token' })
    expect(api.to('POST', '/api/auth/password/reset/complete')[0]?.body).toEqual({
      token: 'reset-token',
      password: 'a brand new passphrase',
      remove_methods: ['m1'],
    })
    expect(api.to('POST', '/api/auth/login')).toHaveLength(0)
  })

  it('refreshes the session after a completed reset so the nav updates', async () => {
    let reset = false
    const api = fakeApi({
      'GET /api/auth/session': () => (reset ? { authenticated: false, csrf_token: 'a' } : SIGNED_IN),
      'POST /api/auth/password/reset/info': { methods: [] },
      'POST /api/auth/password/reset/complete': () => {
        reset = true
        return { status: 'ok' }
      },
    })
    renderAt('/reset#token=reset-token')
    expect(await screen.findByRole('link', { name: 'ann@example.test' })).toBeInTheDocument()
    type('New password', 'a brand new passphrase')
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText('Password changed')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('link', { name: 'ann@example.test' })).toBeNull()
    })
    const nav = screen.getByRole('navigation', { name: 'Account' })
    expect(within(nav).getByRole('link', { name: 'Sign in' })).toBeInTheDocument()
    expect(api.to('GET', '/api/auth/session').length).toBeGreaterThanOrEqual(2)
  })

  it('reports a dead link', async () => {
    fakeApi({ 'POST /api/auth/password/reset/info': reason(400, 'invalid_token') })
    renderAt('/reset#token=old')
    expect(await screen.findByRole('alert')).toHaveTextContent(/not valid any more/)
  })
})

describe('email change page', () => {
  it('needs a signed-in session and confirms on a button press', async () => {
    const api = fakeApi({
      'GET /api/auth/session': SIGNED_IN,
      'POST /api/auth/email/confirm': { status: 'changed', email: 'new@example.test' },
    })
    renderAt('/email-change#token=tok')
    expect(window.location.hash).toBe('')
    const button = await screen.findByRole('button', { name: 'Confirm new email' })
    expect(api.to('POST', '/api/auth/email/confirm')).toHaveLength(0)
    fireEvent.click(button)
    expect(await screen.findByText(/now uses new@example.test/)).toBeInTheDocument()
    expect(api.to('POST', '/api/auth/email/confirm')[0]?.body).toEqual({ token: 'tok' })
  })

  it('asks anonymous visitors to sign in first', async () => {
    fakeApi({})
    renderAt('/email-change#token=tok')
    expect(await screen.findByText(/Sign in to the account that asked/)).toBeInTheDocument()
  })
})


describe('mail links opened in an already open tab', () => {
  it('reads and strips a token that arrives as a fragment-only change', async () => {
    fakeApi({ 'POST /api/auth/password/reset/info': { methods: [] } })
    renderAt('/reset')
    expect(screen.getByRole('button', { name: 'Send reset link' })).toBeInTheDocument()

    window.history.pushState(null, '', '/reset#token=later')
    window.dispatchEvent(new HashChangeEvent('hashchange'))

    expect(await screen.findByRole('button', { name: 'Change password' })).toBeInTheDocument()
    expect(window.location.hash).toBe('')
  })
})
