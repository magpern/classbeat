import { useEffect } from 'react'
import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SessionState } from '../api/auth'
import { SessionProvider, useSession } from './SessionContext'

const getSession = vi.hoisted(() => vi.fn())
vi.mock('../api/auth', () => ({ getSession }))

const IN = { authenticated: true } as unknown as SessionState
const OUT = { authenticated: false } as unknown as SessionState

function defer() {
  let resolve!: (value: SessionState) => void
  const promise = new Promise<SessionState>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

const held: { refresh: () => Promise<SessionState | null> } = { refresh: () => Promise.resolve(null) }
function Probe() {
  const value = useSession()
  useEffect(() => {
    held.refresh = value.refresh
  })
  return <p data-testid="state">{value.status === 'ready' ? String(value.session?.authenticated) : value.status}</p>
}

afterEach(() => {
  getSession.mockReset()
})

describe('overlapping session reads', () => {
  it('keeps the newest answer when an older read answers late (Sign out must not be undone)', async () => {
    const first = defer()
    const second = defer()
    getSession.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    )
    let read!: Promise<SessionState | null>
    act(() => {
      read = held.refresh() // the read after sign-out
    })
    await act(async () => {
      second.resolve(OUT)
      await read
    })
    expect(screen.getByTestId('state').textContent).toBe('false')
    await act(async () => {
      first.resolve(IN) // the older read, answering after the newer one
      await first.promise
    })
    expect(screen.getByTestId('state').textContent).toBe('false')
  })

  it('still applies a read that nothing newer overtook', async () => {
    getSession.mockResolvedValueOnce(OUT).mockResolvedValueOnce(IN)
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    )
    expect(await screen.findByText('false')).toBeTruthy()
    await act(async () => {
      await held.refresh()
    })
    expect(screen.getByTestId('state').textContent).toBe('true')
  })
})
