import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fakeApi, reason } from '../../test/fakeApi'
import { renderAt } from '../../test/render'
import { forgetTurnstileForTests, type TurnstileApi } from './Turnstile'

// ADR 0025 section 11: the Turnstile check on the sign-in, sign-up, provider-email and reset
// forms. Cloudflare's script is never fetched here: jsdom does not load scripts, and the tests
// play its part by setting `window.turnstile` and firing the element's load or error event.

const goToProvider = vi.hoisted(() => vi.fn<(url: string) => void>())
vi.mock('../../lib/navigation', () => ({ goToProvider }))

const SCRIPT = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'
const SITE_KEY = '0x4AAAAAAA-test-site-key'
const OFF = { authenticated: false, csrf_token: 'a', captcha: null }
const REAL = { authenticated: false, csrf_token: 'a', captcha: { site_key: SITE_KEY, fake: false } }
const FAKE = { authenticated: false, csrf_token: 'a', captcha: { site_key: null, fake: true } }
const PENDING = { pending: true, provider: 'microsoft', awaiting_email: true }
const ACCEPTED = () => new Response(null, { status: 202 })

function type(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label, { exact: true }), { target: { value } })
}

function scripts(): HTMLScriptElement[] {
  return [...document.querySelectorAll('script')].filter((s) => s.src.includes('challenges.cloudflare.com'))
}

type Options = Parameters<TurnstileApi['render']>[1]

/** A stand-in for Cloudflare's `window.turnstile` that records every call. */
function stubTurnstile() {
  const rendered: Options[] = []
  const api = {
    render: vi.fn((_box: HTMLElement, options: Options) => {
      rendered.push(options)
      return `w${rendered.length}`
    }),
    reset: vi.fn<(id: string) => void>(),
    remove: vi.fn<(id: string) => void>(),
  }
  return { api, rendered, last: () => rendered[rendered.length - 1]! }
}

/** Plays the script's arrival: defines `window.turnstile`, then fires the load event. */
async function loadScript(api: TurnstileApi) {
  const [script] = scripts()
  expect(script).toBeDefined()
  window.turnstile = api
  await act(async () => {
    script!.dispatchEvent(new Event('load'))
    await Promise.resolve()
  })
}

beforeEach(() => {
  forgetTurnstileForTests()
  delete window.turnstile
  for (const script of scripts()) script.remove()
  document.documentElement.setAttribute('data-theme', 'dark')
})

afterEach(() => {
  cleanup()
  for (const script of scripts()) script.remove()
  delete window.turnstile
})

describe('Turnstile off (captcha: null)', () => {
  it('changes nothing: no check, no script, no token, the submit is enabled', async () => {
    const api = fakeApi({
      'GET /api/auth/session': OFF,
      'GET /api/auth/oauth/providers': { providers: [] },
      'POST /api/auth/password/reset/request': ACCEPTED,
    })
    renderAt('/reset')
    type('Email', 'ann@example.test')
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Send reset link' })).toBeEnabled()
    })
    expect(screen.queryByRole('group', { name: 'Security check' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Send reset link' }))
    expect(await screen.findByText('Check your inbox')).toBeInTheDocument()
    expect(api.to('POST', '/api/auth/password/reset/request')[0]?.body).toEqual({ email: 'ann@example.test' })
    expect(scripts()).toEqual([])
  })
})

describe('Turnstile while the session is not known', () => {
  it('keeps the submit disabled with the waiting text until the session has loaded', async () => {
    const api = fakeApi({
      'GET /api/auth/session': OFF,
      'GET /api/auth/oauth/providers': { providers: [] },
      'POST /api/auth/password/reset/request': ACCEPTED,
    })
    renderAt('/reset')
    const submit = screen.getByRole('button', { name: 'Send reset link' })
    type('Email', 'ann@example.test')
    expect(submit).toBeDisabled()
    const why = document.getElementById(submit.getAttribute('aria-describedby') ?? '')
    expect(why).toHaveTextContent('Checking that you are a person…')
    fireEvent.submit(submit.closest('form')!)
    expect(api.to('POST', '/api/auth/password/reset/request')).toHaveLength(0)

    // The session says the check is off: the form is usable and the waiting text is gone.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Send reset link' })).toBeEnabled()
    })
    expect(screen.getByRole('button', { name: 'Send reset link' })).not.toHaveAttribute('aria-describedby')
  })

  it('does not treat a failed session fetch as the check being off', async () => {
    const api = fakeApi({
      'GET /api/auth/session': () => new Response('boom', { status: 500 }),
      'GET /api/auth/oauth/providers': { providers: [] },
      'POST /api/auth/password/reset/request': ACCEPTED,
    })
    renderAt('/reset')
    type('Email', 'ann@example.test')
    await waitFor(() => {
      expect(api.to('GET', '/api/auth/session').length).toBeGreaterThan(0)
    })
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    const submit = screen.getByRole('button', { name: 'Send reset link' })
    expect(submit).toBeDisabled()
    // It says so, with the way out, instead of "checking" for ever.
    const why = document.getElementById(submit.getAttribute('aria-describedby') ?? '')
    expect(why).toHaveTextContent('Reload the page to try again.')
    fireEvent.submit(submit.closest('form')!)
    expect(api.to('POST', '/api/auth/password/reset/request')).toHaveLength(0)
  })
})

describe('the fake stand-in (captcha.fake)', () => {
  it('waits for the box, sends a fake-pass token, and needs a new one after a failed submit', async () => {
    let answer = (): Response => reason(400, 'captcha_failed')
    const api = fakeApi({
      'GET /api/auth/session': FAKE,
      'GET /api/auth/oauth/providers': { providers: [] },
      'POST /api/auth/login': () => answer(),
    })
    renderAt('/signin')
    const box = await screen.findByRole('checkbox', { name: 'I am a person (test check)' })
    const submit = screen.getByRole('button', { name: 'Sign in' })
    type('Email', 'ann@example.test')
    type('Password', 'correct horse battery')

    // No token yet: disabled, with the visible reason linked to the button.
    expect(submit).toBeDisabled()
    const why = document.getElementById(submit.getAttribute('aria-describedby') ?? '')
    expect(why).toHaveTextContent('Checking that you are a person…')
    expect(why).toHaveAttribute('role', 'status')
    fireEvent.submit(submit.closest('form')!)
    expect(api.to('POST', '/api/auth/login')).toHaveLength(0)

    fireEvent.click(box)
    expect(submit).toBeEnabled()
    expect(submit).not.toHaveAttribute('aria-describedby')
    fireEvent.click(submit)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The security check did not pass. Please try again.',
    )
    const first = (api.to('POST', '/api/auth/login')[0]?.body as { captcha_token: string }).captcha_token
    expect(first).toMatch(/^fake-pass-[0-9a-f]{24}$/)

    // The sent token is spent: the box is cleared and the form waits again.
    const again = await screen.findByRole('checkbox', { name: 'I am a person (test check)' })
    expect(again).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeDisabled()

    answer = () => reason(401, 'invalid_credentials')
    fireEvent.click(again)
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/auth/login')).toHaveLength(2)
    })
    const second = (api.to('POST', '/api/auth/login')[1]?.body as { captcha_token: string }).captcha_token
    expect(second).toMatch(/^fake-pass-/)
    expect(second).not.toBe(first)
    // Any failure spends it, not only a captcha one.
    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: 'I am a person (test check)' })).not.toBeChecked()
    })
    expect(scripts()).toEqual([])
  })

  it('shows the unavailable message from a 503', async () => {
    fakeApi({
      'GET /api/auth/session': FAKE,
      'POST /api/auth/password/reset/request': () => reason(503, 'captcha_unavailable'),
    })
    renderAt('/reset')
    type('Email', 'ann@example.test')
    fireEvent.click(await screen.findByRole('checkbox', { name: 'I am a person (test check)' }))
    // The token arrives through state: submit only once the button shows it is enabled.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Send reset link' })).toBeEnabled()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send reset link' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The security check is not reachable right now. Try again in a minute.',
    )
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Send reset link' })).toBeDisabled()
    })
  })

  it.each([
    ['sign-up', '/signup', 'Create account', 'POST /api/auth/signup'],
    ['provider email', '/signin/email', 'Send confirmation link', 'POST /api/auth/oauth/pending/email'],
    ['reset request', '/reset', 'Send reset link', 'POST /api/auth/password/reset/request'],
  ])('the %s form waits for the box and sends its token', async (_name, path, button, route) => {
    const api = fakeApi({
      'GET /api/auth/session': FAKE,
      'GET /api/auth/oauth/providers': { providers: [] },
      'GET /api/auth/oauth/pending': PENDING,
      [route]: ACCEPTED,
    })
    renderAt(path)
    const box = await screen.findByRole('checkbox', { name: 'I am a person (test check)' })
    if (path === '/signup') {
      type('Invite code', 'invite')
      type('Password', 'correct horse battery')
    }
    type('Email', 'ann@example.test')
    expect(screen.getByRole('button', { name: button })).toBeDisabled()
    fireEvent.click(box)
    fireEvent.click(screen.getByRole('button', { name: button }))
    expect(await screen.findByText('Check your inbox')).toBeInTheDocument()
    const [method, url] = route.split(' ') as [string, string]
    expect(api.to(method, url)).toHaveLength(1)
    const body = api.to(method, url)[0]?.body as { captcha_token?: unknown }
    expect(body.captcha_token).toMatch(/^fake-pass-/)
  })

  it('resets the check after a failed provider-email submit', async () => {
    const api = fakeApi({
      'GET /api/auth/session': FAKE,
      'GET /api/auth/oauth/pending': PENDING,
      'POST /api/auth/oauth/pending/email': () => reason(400, 'captcha_failed'),
    })
    renderAt('/signin/email')
    const box = await screen.findByRole('checkbox', { name: 'I am a person (test check)' })
    type('Email', 'ann@example.test')
    fireEvent.click(box)
    fireEvent.click(screen.getByRole('button', { name: 'Send confirmation link' }))
    await waitFor(() => {
      expect(api.to('POST', '/api/auth/oauth/pending/email')).toHaveLength(1)
    })
    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: 'I am a person (test check)' })).not.toBeChecked()
    })
    expect(screen.getByRole('button', { name: 'Send confirmation link' })).toBeDisabled()
  })

  it('leaves the provider buttons usable without a token', async () => {
    goToProvider.mockClear()
    const api = fakeApi({
      'GET /api/auth/session': FAKE,
      'GET /api/auth/oauth/providers': { providers: ['google'] },
      'POST /api/auth/oauth/google/start': { authorization_url: 'https://accounts.google.com/x' },
    })
    renderAt('/signin')
    fireEvent.click(await screen.findByRole('button', { name: 'Continue with Google' }))
    await waitFor(() => {
      expect(goToProvider).toHaveBeenCalledWith('https://accounts.google.com/x')
    })
    expect(api.to('POST', '/api/auth/oauth/google/start')[0]?.body).not.toHaveProperty('captcha_token')
  })
})

describe('Cloudflare Turnstile (captcha.site_key)', () => {
  it('loads api.js once, renders with the ADR options, sends the token, resets after a failure and removes on leave', async () => {
    const turnstile = stubTurnstile()
    const api = fakeApi({
      'GET /api/auth/session': REAL,
      'GET /api/auth/oauth/providers': { providers: [] },
      'POST /api/auth/login': () => reason(400, 'captcha_failed'),
    })
    renderAt('/signin')
    await screen.findByRole('group', { name: 'Security check' })
    // The group can show before the effect that adds api.js has run (#911, #787).
    await waitFor(() => {
      expect(scripts().map((s) => s.src)).toEqual([SCRIPT])
    })
    expect(scripts()[0]?.parentElement).toBe(document.head)
    await loadScript(turnstile.api)

    await waitFor(() => {
      expect(turnstile.api.render).toHaveBeenCalledTimes(1)
    })
    expect(turnstile.last()).toMatchObject({
      sitekey: SITE_KEY,
      action: 'signin',
      language: 'en',
      theme: 'dark',
      size: 'flexible',
      appearance: 'always',
      execution: 'render',
      'refresh-expired': 'auto',
      'feedback-enabled': false,
      'response-field': false,
    })
    expect(turnstile.last()).not.toHaveProperty('cData')
    const submit = screen.getByRole('button', { name: 'Sign in' })
    expect(submit).toBeDisabled()

    type('Email', 'ann@example.test')
    type('Password', 'correct horse battery')
    act(() => {
      turnstile.last().callback('cf-token-1')
    })
    await waitFor(() => {
      expect(submit).toBeEnabled()
    })
    fireEvent.click(submit)
    expect(await screen.findByRole('alert')).toHaveTextContent('The security check did not pass.')
    expect(api.to('POST', '/api/auth/login')[0]?.body).toEqual({
      email: 'ann@example.test',
      password: 'correct horse battery',
      captcha_token: 'cf-token-1',
    })
    await waitFor(() => {
      expect(turnstile.api.reset).toHaveBeenCalledWith('w1')
      expect(screen.getByRole('button', { name: 'Sign in' })).toBeDisabled()
    })

    // Leaving the form removes the widget; coming back renders again without a second script.
    fireEvent.click(screen.getByRole('link', { name: 'Forgot your password?' }))
    await screen.findByRole('heading', { name: 'Reset your password' })
    await waitFor(() => {
      expect(turnstile.api.remove).toHaveBeenCalledWith('w1')
      expect(turnstile.last().action).toBe('reset')
    })
    expect(scripts()).toHaveLength(1)
  })

  it.each([
    ['/signup', 'signup'],
    ['/signin/email', 'signup_email'],
    ['/reset', 'reset'],
  ])('%s renders with action %s', async (path, action) => {
    const turnstile = stubTurnstile()
    window.turnstile = turnstile.api
    fakeApi({
      'GET /api/auth/session': REAL,
      'GET /api/auth/oauth/providers': { providers: [] },
      'GET /api/auth/oauth/pending': PENDING,
    })
    renderAt(path)
    await waitFor(() => {
      expect(turnstile.api.render).toHaveBeenCalled()
    })
    expect(turnstile.last().action).toBe(action)
  })

  it('shows our own text for expiry, errors and an unsupported browser', async () => {
    const turnstile = stubTurnstile()
    window.turnstile = turnstile.api
    fakeApi({ 'GET /api/auth/session': REAL })
    renderAt('/reset')
    await waitFor(() => {
      expect(turnstile.api.render).toHaveBeenCalled()
    })
    const status = () =>
      screen.getByRole('group', { name: 'Security check' }).querySelector('[role="status"]')
    act(() => {
      turnstile.last().callback('cf-token')
    })
    expect(status()).toBeEmptyDOMElement()
    act(() => {
      turnstile.last()['expired-callback']()
    })
    expect(status()).toHaveTextContent('The security check expired. It is running again.')
    expect(screen.getByRole('button', { name: 'Send reset link' })).toBeDisabled()
    act(() => {
      expect(turnstile.last()['error-callback']()).toBe(true)
    })
    expect(status()).toHaveTextContent('The security check ran into a problem')
    act(() => {
      turnstile.last()['unsupported-callback']()
    })
    expect(status()).toHaveTextContent('This browser cannot run the security check.')
  })

  it('says the check could not load when api.js fails, and tries again on the next form', async () => {
    fakeApi({ 'GET /api/auth/session': REAL, 'GET /api/auth/oauth/providers': { providers: [] } })
    renderAt('/reset')
    await screen.findByRole('group', { name: 'Security check' })
    // The group can show before the effect that adds api.js has run (a flake under load, #787).
    await waitFor(() => {
      expect(scripts()).toHaveLength(1)
    })
    await act(async () => {
      scripts()[0]!.dispatchEvent(new Event('error'))
      await Promise.resolve()
    })
    expect(await screen.findByText(/could not load\. Allow challenges\.cloudflare\.com/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send reset link' })).toBeDisabled()
    expect(scripts()).toEqual([])

    fireEvent.click(screen.getByRole('link', { name: 'Back to sign in' }))
    await screen.findByRole('heading', { name: 'Sign in' })
    expect(scripts()).toHaveLength(1)
  })

  it('renders again in the new theme when the theme changes', async () => {
    const turnstile = stubTurnstile()
    window.turnstile = turnstile.api
    fakeApi({ 'GET /api/auth/session': REAL })
    renderAt('/reset')
    await waitFor(() => {
      expect(turnstile.last().theme).toBe('dark')
    })
    act(() => {
      document.documentElement.setAttribute('data-theme', 'light')
    })
    await waitFor(() => {
      expect(turnstile.last().theme).toBe('light')
    })
    expect(turnstile.api.remove).toHaveBeenCalledWith('w1')
  })

  it('is compact where flexible (300 px) does not fit', async () => {
    const turnstile = stubTurnstile()
    window.turnstile = turnstile.api
    const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(260)
    try {
      fakeApi({ 'GET /api/auth/session': REAL })
      renderAt('/reset')
      await waitFor(() => {
        expect(turnstile.api.render).toHaveBeenCalled()
      })
      expect(turnstile.last().size).toBe('compact')
    } finally {
      width.mockRestore()
    }
  })

  it('switches between flexible and compact when the window is resized', async () => {
    const turnstile = stubTurnstile()
    window.turnstile = turnstile.api
    let px = 400
    const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(() => px)
    try {
      fakeApi({ 'GET /api/auth/session': REAL })
      renderAt('/reset')
      await waitFor(() => {
        expect(turnstile.api.render).toHaveBeenCalled()
      })
      expect(turnstile.last().size).toBe('flexible')
      px = 260
      act(() => {
        window.dispatchEvent(new Event('resize'))
      })
      await waitFor(() => {
        expect(turnstile.last().size).toBe('compact')
      })
      expect(turnstile.api.remove).toHaveBeenCalledWith('w1')
    } finally {
      width.mockRestore()
    }
  })

  it.each(['/', '/privacy', '/help', '/signup/confirm'])('never inserts the script on %s', async (path) => {
    fakeApi({ 'GET /api/auth/session': REAL, 'GET /api/auth/oauth/providers': { providers: [] } })
    renderAt(path)
    await screen.findAllByRole('heading')
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(scripts()).toEqual([])
  })

  it('does not insert the script in fake mode or on the new-password step of a reset', async () => {
    fakeApi({ 'GET /api/auth/session': FAKE })
    renderAt('/reset')
    await screen.findByRole('checkbox', { name: 'I am a person (test check)' })
    expect(scripts()).toEqual([])
    cleanup()
    fakeApi({
      'GET /api/auth/session': REAL,
      'POST /api/auth/password/reset/info': { methods: [], passkeys: [] },
    })
    renderAt('/reset#token=abc')
    await screen.findByLabelText('New password')
    expect(screen.queryByRole('group', { name: 'Security check' })).toBeNull()
    expect(scripts()).toEqual([])
  })
})
