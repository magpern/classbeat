import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SIGNED_IN, fakeApi, json, reason } from '../../test/fakeApi'
import { REQUEST_OPTIONS, credential, error, installPasskeys, uninstallPasskeys } from '../../test/passkeyMock'
import { renderAt } from '../../test/render'
import { isPhoneSignIn, setPhoneSignIn } from '../../auth/phoneSignIn'
import { AUTOFILL_MAX_FAILURES, AUTOFILL_RETRY_MS } from './usePasskeyAutofill'

afterEach(() => {
  uninstallPasskeys()
  vi.useRealTimers()
  setPhoneSignIn(false)
})

const ANON_ON = { authenticated: false, csrf_token: 'a', passkeys: true }
const START = { 'POST /api/auth/passkeys/signin/start': { publicKey: REQUEST_OPTIONS } }

function page(extra: Record<string, unknown> = {}, signedIn: { value: boolean } = { value: false }) {
  return fakeApi({
    'GET /api/auth/session': () => (signedIn.value ? { ...SIGNED_IN, passkeys: true } : ANON_ON),
    'GET /api/auth/oauth/providers': { providers: [] },
    'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
    'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
    'GET /api/auth/sessions': { sessions: [] },
    'GET /api/auth/passkeys': { passkeys: [], enabled: true, max: 10 },
    ...START,
    ...extra,
  })
}

describe('passkey sign-in', () => {
  it('offers nothing without PublicKeyCredential, even with the flag on', async () => {
    page()
    renderAt('/signin')
    await screen.findByRole('button', { name: 'Sign in' })
    expect(screen.queryByRole('button', { name: 'Use a passkey' })).toBeNull()
    expect(screen.getByLabelText('Email')).toHaveAttribute('autocomplete', 'username')
  })

  it('offers nothing with the flag off', async () => {
    installPasskeys({ conditional: true })
    const api = fakeApi({
      'GET /api/auth/session': { authenticated: false, csrf_token: 'a', passkeys: false },
      'GET /api/auth/oauth/providers': { providers: [] },
    })
    renderAt('/signin')
    await screen.findByRole('button', { name: 'Sign in' })
    expect(screen.queryByRole('button', { name: 'Use a passkey' })).toBeNull()
    expect(api.to('POST', '/api/auth/passkeys/signin/start')).toHaveLength(0)
  })

  it('signs in from the button and goes to the account page', async () => {
    const signedIn = { value: false }
    const browser = installPasskeys({ result: credential('platform') })
    const api = page(
      {
        'POST /api/auth/passkeys/signin/finish': () => {
          signedIn.value = true
          return { id: 'u1', email: 'ann@example.test', role: 'user' }
        },
      },
      signedIn,
    )
    renderAt('/signin')
    fireEvent.click(await screen.findByRole('button', { name: 'Use a passkey' }))
    expect(await screen.findByTestId('account-email')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/en/account')
    expect(browser.get.mock.calls[0]?.[0]).not.toHaveProperty('mediation')
    expect(api.to('POST', '/api/auth/passkeys/signin/finish')[0]?.body).toEqual({ credential: credential().toJSON?.() })
    expect(isPhoneSignIn()).toBe(false)
    expect(screen.queryByText(/Signed in with your phone or security key/)).toBeNull()
  })

  it('shows the shared-computer notice after a cross-platform sign-in, and signing out clears it', async () => {
    const signedIn = { value: false }
    installPasskeys({ result: credential('cross-platform') })
    page(
      {
        'POST /api/auth/passkeys/signin/finish': () => {
          signedIn.value = true
          return { id: 'u1', email: 'ann@example.test', role: 'user' }
        },
        'POST /api/auth/logout': () => {
          signedIn.value = false
          return { status: 'ok' }
        },
      },
      signedIn,
    )
    renderAt('/signin')
    fireEvent.click(await screen.findByRole('button', { name: 'Use a passkey' }))
    expect(await screen.findByText('Signed in with your phone or security key; sign out when you are done.')).toBeInTheDocument()
    expect(isPhoneSignIn()).toBe(true)
    const notice = screen.getByText('Signed in with your phone or security key; sign out when you are done.').closest('div')!
    fireEvent.click(notice.querySelector('button')!)
    await waitFor(() => {
      expect(isPhoneSignIn()).toBe(false)
    })
  })

  it('a password sign-in after a phone sign-in shows no notice', async () => {
    setPhoneSignIn(true)
    const signedIn = { value: false }
    installPasskeys()
    page(
      {
        'POST /api/auth/login': () => {
          signedIn.value = true
          return { id: 'u1', email: 'ann@example.test', role: 'user' }
        },
      },
      signedIn,
    )
    renderAt('/signin')
    // The sign-in page found the session signed out: the flag is gone already.
    await screen.findByRole('button', { name: 'Use a passkey' })
    expect(isPhoneSignIn()).toBe(false)
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'ann@example.test' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'correct horse battery' } })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByTestId('account-email')).toBeInTheDocument()
    expect(screen.queryByText(/Signed in with your phone or security key/)).toBeNull()
  })

  it('stays quiet when the browser dialog is closed', async () => {
    installPasskeys({ result: error('NotAllowedError') })
    page()
    renderAt('/signin')
    fireEvent.click(await screen.findByRole('button', { name: 'Use a passkey' }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Use a passkey' })).toBeEnabled()
    })
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('words the busy server (503 passkey_busy) and the rate limit (429)', async () => {
    installPasskeys()
    page({ 'POST /api/auth/passkeys/signin/start': reason(503, 'passkey_busy') })
    renderAt('/signin')
    fireEvent.click(await screen.findByRole('button', { name: 'Use a passkey' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Passkey sign-in is busy. Use your password or a provider, or try the passkey again in a few minutes.',
    )
  })

  it('says to wait on a 429', async () => {
    installPasskeys()
    page({
      'POST /api/auth/passkeys/signin/start': () =>
        json({ detail: { reason: 'rate_limited' } }, 429, { 'Retry-After': '120' }),
    })
    renderAt('/signin')
    fireEvent.click(await screen.findByRole('button', { name: 'Use a passkey' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Too many attempts/)
  })

  it('tells the passkey manager about a credential the server does not know', async () => {
    const browser = installPasskeys({ result: credential(), signals: true })
    page({ 'POST /api/auth/passkeys/signin/finish': reason(400, 'passkey_unknown') })
    renderAt('/signin')
    fireEvent.click(await screen.findByRole('button', { name: 'Use a passkey' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('not registered here any more')
    expect(browser.signalUnknownCredential).toHaveBeenCalledWith({ rpId: 'classbeat.test', credentialId: 'cred-1' })
  })
})

describe('passkey autofill', () => {
  it('marks the email field for autofill and starts a conditional ceremony', async () => {
    const browser = installPasskeys({ conditional: true })
    page()
    renderAt('/signin')
    await screen.findByRole('button', { name: 'Use a passkey' })
    expect(screen.getByLabelText('Email')).toHaveAttribute('autocomplete', 'username webauthn')
    await waitFor(() => {
      expect(browser.get).toHaveBeenCalledTimes(1)
    })
    expect(browser.get.mock.calls[0]?.[0]).toMatchObject({ mediation: 'conditional' })
  })

  it('signs in from the autofill choice', async () => {
    const signedIn = { value: false }
    installPasskeys({ conditional: true, result: credential() })
    page(
      {
        'POST /api/auth/passkeys/signin/finish': () => {
          signedIn.value = true
          return { id: 'u1', email: 'ann@example.test', role: 'user' }
        },
      },
      signedIn,
    )
    renderAt('/signin')
    expect(await screen.findByTestId('account-email')).toBeInTheDocument()
  })

  it('starts nothing where the browser has no conditional mediation', async () => {
    const browser = installPasskeys({ conditional: false })
    page()
    renderAt('/signin')
    await screen.findByRole('button', { name: 'Use a passkey' })
    expect(screen.getByLabelText('Email')).toHaveAttribute('autocomplete', 'username webauthn')
    expect(browser.get).not.toHaveBeenCalled()
  })

  it('stops on 429 and shows the message, leaving the button', async () => {
    const browser = installPasskeys({ conditional: true })
    const api = page({
      'POST /api/auth/passkeys/signin/start': reason(503, 'passkey_busy'),
    })
    renderAt('/signin')
    expect(await screen.findByRole('alert')).toHaveTextContent(/is busy/)
    expect(browser.get).not.toHaveBeenCalled()
    expect(api.to('POST', '/api/auth/passkeys/signin/start')).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Use a passkey' })).toBeEnabled()
  })

  it('pauses while the button runs its own ceremony', async () => {
    const browser = installPasskeys({ conditional: true })
    page()
    renderAt('/signin')
    await waitFor(() => {
      expect(browser.get).toHaveBeenCalledTimes(1)
    })
    const conditional = browser.get.mock.calls[0]?.[0] as { signal: AbortSignal }
    browser.get.mockImplementationOnce(() => new Promise(() => undefined))
    fireEvent.click(screen.getByRole('button', { name: 'Use a passkey' }))
    await waitFor(() => {
      expect(conditional.signal.aborted).toBe(true)
    })
    expect(browser.get).toHaveBeenCalledTimes(2)
    expect(browser.get.mock.calls[1]?.[0]).not.toHaveProperty('mediation')
  })
})

describe('passkey autofill restarts', () => {
  function setup(visibility: 'visible' | 'hidden') {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const browser = installPasskeys({ conditional: true })
    // The first ceremony ends the way an expired challenge does; the next ones wait.
    browser.get.mockImplementationOnce(() => Promise.reject(error('NotAllowedError')))
    const state = { value: visibility }
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => state.value)
    page()
    renderAt('/signin')
    return { browser, state }
  }

  it('restarts after a lapsed challenge while the page is visible', async () => {
    const { browser } = setup('visible')
    await waitFor(() => {
      expect(browser.get).toHaveBeenCalledTimes(1)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTOFILL_RETRY_MS + 50)
    })
    await waitFor(() => {
      expect(browser.get).toHaveBeenCalledTimes(2)
    })
  })

  it('waits for the page to be visible again before it restarts', async () => {
    const { browser, state } = setup('visible')
    await waitFor(() => {
      expect(browser.get).toHaveBeenCalledTimes(1)
    })
    state.value = 'hidden'
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTOFILL_RETRY_MS + 50)
    })
    expect(browser.get).toHaveBeenCalledTimes(1)
    state.value = 'visible'
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
      await vi.advanceTimersByTimeAsync(10)
    })
    await waitFor(() => {
      expect(browser.get).toHaveBeenCalledTimes(2)
    })
  })

  it('does not start at all while the page is hidden', async () => {
    const { browser } = setup('hidden')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(browser.get).not.toHaveBeenCalled()
  })

  it('leaves no visibility listener behind when the page goes away while hidden', async () => {
    const added = vi.spyOn(document, 'addEventListener')
    const removed = vi.spyOn(document, 'removeEventListener')
    const { browser } = setup('hidden')
    await waitFor(() => {
      expect(added.mock.calls.some(([type]) => type === 'visibilitychange')).toBe(true)
    })
    const [, listener] = added.mock.calls.find(([type]) => type === 'visibilitychange') ?? []
    cleanup()
    expect(removed).toHaveBeenCalledWith('visibilitychange', listener)
    expect(browser.get).not.toHaveBeenCalled()
  })

  it('gives up after a few failures in a row', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const browser = installPasskeys({ conditional: true })
    browser.get.mockImplementation(() => Promise.reject(error('NotAllowedError')))
    page()
    renderAt('/signin')
    for (let i = 0; i < AUTOFILL_MAX_FAILURES + 3; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(AUTOFILL_RETRY_MS * 2 ** i + 50)
      })
    }
    expect(browser.get).toHaveBeenCalledTimes(AUTOFILL_MAX_FAILURES)
  })
})
