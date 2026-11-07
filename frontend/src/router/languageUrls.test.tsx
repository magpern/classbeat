import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { I18nProvider } from '../i18n/I18n'
import { SIGNED_IN, fakeApi, json } from '../test/fakeApi'

// ADR 0027: the language lives in the URL. These tests open the whole app at a URL, like a browser.

const OFFERED = ['en', 'sv', 'da']
const ANON = { authenticated: false, csrf_token: 'csrf-anon', locales: OFFERED }
const signedIn = (locale: string | null, locales = OFFERED) => ({
  ...SIGNED_IN,
  user: { ...SIGNED_IN.user, locale },
  locales,
})
const IDENTITIES = { identities: [], has_password: true, available: [] }
const MFA_OFF = { totp_enabled: false, recovery_codes_left: 0 }

function open(url: string) {
  // Absolute, so that a path of two slashes is a path on this origin, as in the address bar.
  window.history.replaceState(null, '', window.location.origin + url)
  return render(
    <I18nProvider>
      <App />
    </I18nProvider>,
  )
}

function where() {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`
}

function setLanguages(languages: string[]) {
  vi.spyOn(window.navigator, 'languages', 'get').mockReturnValue(languages)
}

beforeEach(() => {
  window.localStorage.clear()
  vi.restoreAllMocks()
  document.documentElement.setAttribute('lang', 'en')
})

describe('an unprefixed URL', () => {
  it('shows only the loading state until the session answers, then redirects once', async () => {
    fakeApi({ 'GET /api/auth/session': ANON })
    let sessionCalls = 0
    // Hold the session answer back to see what is on screen meanwhile.
    let release: (response: Response) => void = () => undefined
    const held = new Promise<Response>((resolve) => {
      release = resolve
    })
    const answer = (globalThis.fetch as unknown as (...args: unknown[]) => Promise<Response>).bind(globalThis)
    vi.stubGlobal('fetch', (input: string, init?: RequestInit) => {
      if (!input.endsWith('/api/auth/session')) return answer(input, init)
      sessionCalls += 1
      return held
    })
    const replace = vi.spyOn(window.history, 'replaceState')
    open('/classes')
    expect(screen.getByText('Loading')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/classes')
    expect(replace).toHaveBeenCalledTimes(1) // only the test's own set-up
    release(json(ANON))
    await waitFor(() => {
      expect(window.location.pathname).toBe('/en/classes')
    })
    expect(replace).toHaveBeenCalledTimes(2)
    expect(sessionCalls).toBe(1)
  })

  it.each([
    ['/reset#token=x', '/en/reset#token=x'],
    ['/unsubscribe#token=x', '/en/unsubscribe#token=x'],
    ['/email-change', '/en/email-change'],
    ['/account?oauth=reauthed', '/en/account?oauth=reauthed'],
    ['/privacy', '/en/privacy'],
    ['/classes/', '/en/classes'],
    ['/', '/en'],
  ])('%s goes to %s with the fragment and search kept', async (from, to) => {
    fakeApi({ 'GET /api/auth/session': ANON })
    open(from)
    await waitFor(() => {
      // Mail pages strip the fragment once they have read it, so compare the path and search.
      expect(window.location.pathname + window.location.search).toBe(to.split('#')[0])
    })
  })

  it('keeps a mail token readable through the redirect (the page reads it after the redirect)', async () => {
    const api = fakeApi({
      'GET /api/auth/session': ANON,
      'POST /api/mail/unsubscribe/confirm': { status: 'ok' },
    })
    open('/unsubscribe#token=cat&all=allt')
    fireEvent.click(await screen.findByRole('button', { name: 'Stop this kind of email' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/mail/unsubscribe/confirm')[0]?.body).toEqual({ token: 'cat' })
    })
    expect(window.location.pathname).toBe('/en/unsubscribe')
  })

  it('uses the order users.locale, cb-locale, navigator.languages, en', async () => {
    setLanguages(['da-DK'])
    fakeApi({ 'GET /api/auth/session': ANON })
    open('/classes')
    await waitFor(() => {
      expect(where()).toBe('/da/classes')
    })
    window.localStorage.setItem('cb-locale', 'sv')
    open('/classes')
    await waitFor(() => {
      expect(where()).toBe('/sv/classes')
    })
    fakeApi({ 'GET /api/auth/session': signedIn('da'), 'GET /api/classes': { classes: [] } })
    open('/classes')
    await waitFor(() => {
      expect(where()).toBe('/da/classes')
    })
    expect(window.localStorage.getItem('cb-locale')).toBe('sv') // nothing was written
  })

  it('goes to English when nothing else is known', async () => {
    setLanguages(['fr-FR'])
    fakeApi({ 'GET /api/auth/session': ANON })
    open('/help')
    await waitFor(() => {
      expect(where()).toBe('/en/help')
    })
  })

  it.each(['//evil.example', '/\\evil.example', '/%2F%2Fevil.example', '/%5Cevil.example', '///evil.example/x'])(
    'never leaves the origin for %s',
    async (url) => {
      fakeApi({ 'GET /api/auth/session': ANON })
      const origin = window.location.origin
      open(url)
      await waitFor(() => {
        expect(window.location.pathname.startsWith('/en')).toBe(true)
      })
      expect(window.location.origin).toBe(origin)
      expect(window.location.pathname.startsWith('/en/')).toBe(true)
    },
  )

  it('leaves /api and /admin alone and sends /<lang>/admin to /admin', async () => {
    fakeApi({ 'GET /api/auth/session': ANON })
    open('/admin/users')
    await screen.findByText('Sign in first')
    expect(where()).toBe('/admin/users')
    open('/sv/admin/users?x=1#y')
    await waitFor(() => {
      expect(where()).toBe('/admin/users?x=1#y')
    })
  })
})

describe('a prefixed URL', () => {
  it('shows its language, sets <html lang>, and writes nothing', async () => {
    window.localStorage.setItem('cb-locale', 'da')
    fakeApi({ 'GET /api/auth/session': signedIn('da'), 'GET /api/classes': { classes: [] } })
    open('/sv/classes')
    await waitFor(() => {
      expect(document.documentElement.getAttribute('lang')).toBe('sv')
    })
    expect(where()).toBe('/sv/classes')
    expect(window.localStorage.getItem('cb-locale')).toBe('da')
  })

  it('removes a trailing slash', async () => {
    fakeApi({ 'GET /api/auth/session': ANON })
    open('/sv/privacy/')
    await waitFor(() => {
      expect(where()).toBe('/sv/privacy')
    })
  })

  it('replaces a supported language the server does not offer with the best offered one', async () => {
    window.localStorage.setItem('cb-locale', 'sv')
    fakeApi({ 'GET /api/auth/session': { ...ANON, locales: ['en', 'sv'] } })
    open('/da/privacy?x=1#y')
    await waitFor(() => {
      expect(window.location.pathname).toBe('/sv/privacy')
    })
  })

  it('is English for a disabled language when English is all that is offered', async () => {
    fakeApi({ 'GET /api/auth/session': { ...ANON, locales: ['en'] } })
    open('/sv/privacy')
    await waitFor(() => {
      expect(where()).toBe('/en/privacy')
    })
  })

  it('shows the not-found card for an unknown two-letter code, without redirecting', async () => {
    fakeApi({ 'GET /api/auth/session': ANON })
    open('/fr/privacy')
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    expect(where()).toBe('/fr/privacy')
  })

  it('shows the not-found card in the best language for an unknown code', async () => {
    window.localStorage.setItem('cb-locale', 'sv')
    fakeApi({ 'GET /api/auth/session': ANON })
    open('/fr')
    await waitFor(() => {
      expect(document.documentElement.getAttribute('lang')).toBe('sv')
    })
    expect(where()).toBe('/fr')
  })
})

describe('the picker', () => {
  it('goes to the same page in the new language, keeping search and fragment, and saves the choice', async () => {
    fakeApi({ 'GET /api/auth/session': ANON })
    open('/en/account?tab=x#data')
    // Signed out the account page asks for a sign-in; use a public page with a fragment instead.
    window.history.replaceState(null, '', '/en/privacy?a=1#section')
    window.dispatchEvent(new PopStateEvent('popstate'))
    const picker = await screen.findByRole('combobox', { name: 'Language' })
    await userEvent.selectOptions(picker, 'sv')
    await waitFor(() => {
      expect(where()).toBe('/sv/privacy?a=1#section')
    })
    expect(window.localStorage.getItem('cb-locale')).toBe('sv')
    await waitFor(() => {
      expect(document.documentElement.getAttribute('lang')).toBe('sv')
    })
  })
})

describe('deep link, sign-in and back', () => {
  it('returns to the same page with exactly one prefix, also after a language switch in the middle', async () => {
    let signed = false
    const api = fakeApi({
      'GET /api/auth/session': () => (signed ? signedIn(null) : ANON),
      'GET /api/auth/oauth/providers': { providers: [] },
      'POST /api/auth/login': () => {
        signed = true
        return { id: 'u1', email: 'ann@example.test', role: 'user' }
      },
      'GET /api/auth/identities': IDENTITIES,
      'GET /api/auth/mfa': MFA_OFF,
      'GET /api/classes': { classes: [] },
    })
    open('/account')
    // Deep link, signed out: the redirect, then the sign-in prompt whose return_to is neutral.
    await screen.findByText('Sign in first')
    const signIn = screen.getAllByRole('link', { name: 'Sign in' }).find((a) => a.classList.contains('button')) as HTMLElement
    expect(window.location.pathname).toBe('/en/account')
    expect(signIn).toHaveAttribute('href', `/en/signin?return_to=${encodeURIComponent('/account')}`)
    // Switch the language in the middle of the round trip.
    fireEvent.click(signIn)
    await screen.findByLabelText('Email')
    expect(where()).toBe(`/en/signin?return_to=${encodeURIComponent('/account')}`)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Language' }), 'sv')
    await waitFor(() => {
      expect(where()).toBe(`/sv/signin?return_to=${encodeURIComponent('/account')}`)
    })
    fireEvent.change(screen.getByLabelText('E-post'), { target: { value: 'ann@example.test' } })
    fireEvent.change(screen.getByLabelText('Lösenord'), { target: { value: 'correct horse battery' } })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Logga in' })).toBeEnabled()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Logga in' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe('/sv/account')
    })
    expect(api.to('POST', '/api/auth/login')).toHaveLength(1)
    expect(window.location.pathname.match(/\/(en|sv|da)\//g)).toHaveLength(1)
  })

  it('does not double the prefix of a return_to that already carries one', async () => {
    let signed = false
    fakeApi({
      'GET /api/auth/session': () => (signed ? signedIn(null) : ANON),
      'GET /api/auth/oauth/providers': { providers: [] },
      'POST /api/auth/login': () => {
        signed = true
        return { id: 'u1', email: 'ann@example.test', role: 'user' }
      },
      'GET /api/auth/identities': IDENTITIES,
      'GET /api/auth/mfa': MFA_OFF,
      'GET /api/classes': { classes: [] },
    })
    open(`/da/signin?return_to=${encodeURIComponent('/sv/classes?x=1#y')}`)
    fireEvent.change(await screen.findByLabelText('Mail'), { target: { value: 'ann@example.test' } })
    fireEvent.change(screen.getByLabelText('Adgangskode'), { target: { value: 'correct horse battery' } })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Log ind' })).toBeEnabled()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Log ind' }))
    await waitFor(() => {
      expect(window.location.pathname).toBe('/da/classes')
    })
    expect(window.location.search).toBe('?x=1')
  })
})
