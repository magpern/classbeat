import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useCaptcha } from './Turnstile'

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

function Probe() {
  const captcha = useCaptcha('signin')
  return (
    <div>
      {captcha.element}
      <span data-testid="token">{captcha.token ?? 'none'}</span>
      <span data-testid="ready">{String(captcha.ready)}</span>
    </div>
  )
}

afterEach(cleanup)

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
})
