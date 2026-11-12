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
  let reject!: (reason: Error) => void
  const promise = new Promise<SessionState>((r, j) => {
    resolve = r
    reject = j
  })
  return { promise, resolve, reject }
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

  async function mountedWith(first: SessionState) {
    getSession.mockResolvedValueOnce(first)
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    )
    await screen.findByText(String(first.authenticated))
  }

  it('keeps the sign-out when an older refresh (the sign-in one) answers after the newer refresh', async () => {
    await mountedWith(IN)
    const older = defer()
    const newer = defer()
    getSession.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise)
    let a!: Promise<SessionState | null>
    let b!: Promise<SessionState | null>
    act(() => {
      a = held.refresh()
      b = held.refresh()
    })
    await act(async () => {
      newer.resolve(OUT)
      await b
    })
    await act(async () => {
      older.resolve(IN)
      await a
    })
    expect(screen.getByTestId('state').textContent).toBe('false')
    expect(await a).toBe(IN) // each caller still gets its own read
  })

  it('keeps a newer sign-in when an older refresh answers signed out afterwards', async () => {
    await mountedWith(OUT)
    const older = defer()
    const newer = defer()
    getSession.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise)
    let a!: Promise<SessionState | null>
    let b!: Promise<SessionState | null>
    act(() => {
      a = held.refresh()
      b = held.refresh()
    })
    await act(async () => {
      newer.resolve(IN)
      await b
    })
    await act(async () => {
      older.resolve(OUT)
      await a
    })
    expect(screen.getByTestId('state').textContent).toBe('true')
  })

  it('does not show an error for an older read that fails after a newer one answered', async () => {
    await mountedWith(IN)
    let failOlder!: (reason: Error) => void
    const older = new Promise<SessionState>((_, reject) => {
      failOlder = reject
    })
    const newer = defer()
    getSession.mockReturnValueOnce(older).mockReturnValueOnce(newer.promise)
    let a!: Promise<SessionState | null>
    let b!: Promise<SessionState | null>
    act(() => {
      a = held.refresh()
      b = held.refresh()
    })
    await act(async () => {
      newer.resolve(OUT)
      await b
    })
    await act(async () => {
      failOlder(new Error('late'))
      await a
    })
    expect(screen.getByTestId('state').textContent).toBe('false')
  })

  it('shows the error of the newest read, and a mount read that answers late does not replace it', async () => {
    const mount = defer()
    getSession.mockReturnValueOnce(mount.promise)
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    )
    getSession.mockRejectedValueOnce(new Error('down'))
    await act(async () => {
      await held.refresh()
    })
    expect(screen.getByTestId('state').textContent).toBe('error')
    await act(async () => {
      mount.resolve(IN) // older than the failed refresh: ignored
      await mount.promise
    })
    expect(screen.getByTestId('state').textContent).toBe('error')
  })

  it('does not show an error for a mount read that fails after a newer read answered', async () => {
    const mount = defer()
    getSession.mockReturnValueOnce(mount.promise)
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    )
    getSession.mockResolvedValueOnce(OUT)
    await act(async () => {
      await held.refresh()
    })
    expect(screen.getByTestId('state').textContent).toBe('false')
    await act(async () => {
      mount.reject(new Error('late')) // older than the refresh: its failure is ignored (#999)
      await mount.promise.catch(() => undefined)
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
