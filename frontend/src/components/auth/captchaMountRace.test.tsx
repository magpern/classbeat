import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRoot } from 'react-dom/client'
import { useCaptcha } from './Turnstile'

// A user (or a fast test) can click the check in the moment between React committing it and its
// passive effects running. The click's token must survive that.

const session = vi.hoisted<{ value: unknown }>(() => ({ value: null }))
vi.mock('../../auth/SessionContext', () => ({ useSession: () => session.value }))

const FAKE = { authenticated: false, csrf_token: 'a', captcha: { kind: 'fake' } }

function Probe() {
  const captcha = useCaptcha('reset')
  return (
    <div>
      {captcha.element}
      <span data-testid="ready">{String(captcha.ready)}</span>
    </div>
  )
}

let host: HTMLElement | null = null
afterEach(() => {
  host?.remove()
  host = null
  Reflect.deleteProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT')
})

describe('captcha token and the mount effects', () => {
  it('keeps a token given before the passive effects of the mount have run', async () => {
    session.value = { status: 'ready', session: FAKE, refresh: vi.fn() }
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: false })
    host = document.body.appendChild(document.createElement('div'))
    const root = createRoot(host)
    const ready = new Promise<void>((resolve) => {
      const seen = new MutationObserver(() => {
        const box = host?.querySelector<HTMLInputElement>('[data-testid="captcha-fake"]')
        if (box === null || box === undefined) return
        seen.disconnect()
        box.click() // right after the commit, before the scheduler runs the passive effects
        resolve()
      })
      seen.observe(host!, { childList: true, subtree: true })
    })
    root.render(<Probe />)
    await ready
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(host.querySelector('[data-testid="ready"]')?.textContent).toBe('true')
    root.unmount()
  })
})
