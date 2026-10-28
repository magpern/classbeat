import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ApiError } from '../api/client'
import { SIGNED_IN, fakeApi } from '../test/fakeApi'
import { GuardProvider } from './GuardProvider'
import { PromptCancelled, useGuard } from './prompts'
import { SessionProvider } from './SessionContext'

type Outcome = 'pending' | 'done' | 'cancelled' | 'failed'

/** Three guarded actions at once: two need re-authentication, one a step-up. */
function setup(grants: { reauth: boolean; stepup: boolean }) {
  const outcomes: Record<string, Outcome> = {}
  function need(level: 'reauth' | 'stepup') {
    return () =>
      grants[level]
        ? Promise.resolve(level)
        : Promise.reject(new ApiError(403, `${level}_required`, null, undefined))
  }
  function Runner() {
    const guard = useGuard()
    const run = (name: string, action: () => Promise<string>) => {
      outcomes[name] = 'pending'
      guard(action).then(
        () => {
          outcomes[name] = 'done'
        },
        (error: unknown) => {
          outcomes[name] = error instanceof PromptCancelled ? 'cancelled' : 'failed'
        },
      )
    }
    return (
      <button
        type="button"
        onClick={() => {
          run('a', need('reauth'))
          run('b', need('reauth'))
          run('c', need('stepup'))
        }}
      >
        go
      </button>
    )
  }
  render(
    <SessionProvider>
      <GuardProvider>
        <Runner />
      </GuardProvider>
    </SessionProvider>,
  )
  return outcomes
}

describe('GuardProvider with several prompts', () => {
  const routes = {
    'GET /api/auth/session': SIGNED_IN,
    'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
  }

  it('shares one prompt between actions of the same kind and queues the other kind', async () => {
    const grants = { reauth: false, stepup: false }
    const api = fakeApi({
      ...routes,
      'POST /api/auth/reauth': () => {
        grants.reauth = true
        return { status: 'ok' }
      },
      'POST /api/auth/mfa/stepup': () => {
        grants.stepup = true
        return { ok: true }
      },
    })
    const outcomes = setup(grants)
    fireEvent.click(screen.getByRole('button', { name: 'go' }))

    let dialog = await screen.findByRole('dialog')
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    fireEvent.change(await within(dialog).findByLabelText('Password'), { target: { value: 'pw' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm with password' }))

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toHaveTextContent('Enter a two-factor code')
    })
    expect(outcomes.a).toBe('done')
    expect(outcomes.b).toBe('done')
    expect(api.to('POST', '/api/auth/reauth')).toHaveLength(1)

    dialog = screen.getByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Authenticator or recovery code'), {
      target: { value: '123456' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }))
    await waitFor(() => {
      expect(outcomes.c).toBe('done')
    })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('cancelling rejects every waiting action, queued ones included, and leaves nothing open', async () => {
    fakeApi(routes)
    const outcomes = setup({ reauth: false, stepup: false })
    fireEvent.click(screen.getByRole('button', { name: 'go' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toHaveTextContent('Enter a two-factor code')
    })
    expect(outcomes.a).toBe('cancelled')
    expect(outcomes.b).toBe('cancelled')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => {
      expect(outcomes.c).toBe('cancelled')
    })
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
