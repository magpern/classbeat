import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { forgetTurnstileForTests, useCaptcha, type TurnstileApi } from './Turnstile'

// ADR 0025 section 11: a token is only ever returned while the session is known. After a failed
// session refresh the held token must not be sent, because the check's configuration is unknown.

interface Held {
  status: string
  session: unknown
  refresh: () => void
}
const session = vi.hoisted(() => ({ value: null as unknown as Held }))
vi.mock('../../auth/SessionContext', () => ({ useSession: () => session.value }))

const FAKE = { authenticated: false, csrf_token: 'a', captcha: { kind: 'fake' } }

/** `ready` of every render, to see a render an effect would otherwise hide. */
const renders: boolean[] = []

function Probe() {
  const captcha = useCaptcha('signin')
  renders.push(captcha.ready)
  return (
    <div>
      {captcha.element}
      <span data-testid="token">{captcha.token ?? 'none'}</span>
      <span data-testid="ready">{String(captcha.ready)}</span>
    </div>
  )
}

beforeEach(() => {
  renders.length = 0
})

afterEach(() => {
  cleanup()
  delete window.turnstile
  forgetTurnstileForTests()
})

describe('useCaptcha token', () => {
  it('returns no token and is not ready while the session is unknown, even with a held token', () => {
    session.value = { status: 'ready', session: FAKE, refresh: vi.fn() }
    const { rerender } = render(<Probe />)
    fireEvent.click(screen.getByTestId('captcha-fake'))
    expect(screen.getByTestId('token').textContent).toMatch(/^fake-pass-/)
    expect(screen.getByTestId('ready').textContent).toBe('true')

    session.value = { status: 'error', session: FAKE, refresh: vi.fn() }
    rerender(<Probe />)
    expect(screen.getByTestId('token').textContent).toBe('none')
    expect(screen.getByTestId('ready').textContent).toBe('false')
    expect(screen.getByRole('status')).toHaveTextContent('Reload the page to try again.')
  })

  it('says it is checking while the session loads', () => {
    session.value = { status: 'loading', session: null, refresh: vi.fn() }
    render(<Probe />)
    expect(screen.getByRole('status')).toHaveTextContent('Checking that you are a person…')
  })

  it('starts a new configuration without the old token', () => {
    session.value = { status: 'ready', session: FAKE, refresh: vi.fn() }
    const { rerender } = render(<Probe />)
    fireEvent.click(screen.getByTestId('captcha-fake'))
    expect(screen.getByTestId('ready').textContent).toBe('true')

    const real = { authenticated: false, csrf_token: 'a', captcha: { kind: 'turnstile', siteKey: 'site-b' } }
    renders.length = 0
    session.value = { status: 'ready', session: real, refresh: vi.fn() }
    rerender(<Probe />)
    expect(screen.getByTestId('token').textContent).toBe('none')
    expect(screen.getByTestId('ready').textContent).toBe('false')
    // Not even for one render: the widget's own effect would clear a stale token afterwards.
    expect(renders).not.toContain(true)
  })

  it('starts without a token when the session read fails and then succeeds again', () => {
    session.value = { status: 'ready', session: FAKE, refresh: vi.fn() }
    const { rerender } = render(<Probe />)
    fireEvent.click(screen.getByTestId('captcha-fake'))
    expect(screen.getByTestId('ready').textContent).toBe('true')

    session.value = { status: 'error', session: FAKE, refresh: vi.fn() }
    rerender(<Probe />)
    session.value = { status: 'ready', session: FAKE, refresh: vi.fn() }
    rerender(<Probe />)
    expect(screen.getByTestId('token').textContent).toBe('none')
    expect(screen.getByTestId('ready').textContent).toBe('false')
    expect(screen.getByTestId('captcha-fake')).not.toBeChecked()
  })

  it('shows the waiting text, not an old error, for a new configuration', async () => {
    // A real Turnstile error state: the widget's own error callback fires for the first site key.
    const options: Parameters<TurnstileApi['render']>[1][] = []
    window.turnstile = {
      render: vi.fn((_box: HTMLElement, o: Parameters<TurnstileApi['render']>[1]) => {
        options.push(o)
        return `w${options.length}`
      }),
      reset: vi.fn(),
      remove: vi.fn(),
    }
    const turnstile = (siteKey: string) => ({
      status: 'ready',
      session: { authenticated: false, csrf_token: 'a', captcha: { kind: 'turnstile', siteKey } },
      refresh: vi.fn(),
    })
    session.value = turnstile('site-a')
    const { rerender } = render(<Probe />)
    await waitFor(() => {
      expect(options).toHaveLength(1)
    })
    act(() => {
      options[0]!['error-callback']()
    })
    expect(screen.getByRole('status')).toHaveTextContent('The security check ran into a problem')

    session.value = turnstile('site-b')
    rerender(<Probe />)
    expect(screen.getByRole('status')).toHaveTextContent('Checking that you are a person…')
  })
})
