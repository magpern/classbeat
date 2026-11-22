import { useCallback, useEffect, useState } from 'react'
import { SilentTransport } from '../../preview/silentTransport'
import type { PreviewTransport, TransportState } from '../../preview/transport'

const POLL_MS = 200
const IDLE: TransportState = { status: 'idle', observedAt: 0, uncertaintyMs: 0 }
const clock = (): number => performance.now()

function start(totalMs: number): { totalMs: number; transport: PreviewTransport; state: TransportState } {
  const transport = new SilentTransport({ totalMs, now: clock })
  return { totalMs, transport, state: transport.observe() }
}

/**
 * The editor's transport: the silent one, a clock-driven cursor with no audio. The cursor the
 * page draws is `state`, which is only ever what `transport.observe()` returned: a poll while the
 * clock runs and one read after each command, never the position a command asked for.
 */
export function useSilentCursor(totalMs: number): {
  state: TransportState
  audition: (piece: number, sourceMs: number, classMs: number) => void
  pause: () => void
} {
  const [held, setHeld] = useState(() => start(totalMs))
  // A new class length is a new transport: its cursor starts again (state reset while rendering).
  if (held.totalMs !== totalMs) setHeld({ totalMs, transport: new SilentTransport({ totalMs, now: clock }), state: IDLE })
  const { transport, state } = held
  const setState = useCallback(
    (next: TransportState) => {
      setHeld((previous) => (previous.transport === transport ? { ...previous, state: next } : previous))
    },
    [transport],
  )
  useEffect(() => {
    if (state.status !== 'playing') return
    const id = window.setInterval(() => {
      setState(transport.observe())
    }, POLL_MS)
    return () => {
      window.clearInterval(id)
    }
  }, [transport, state.status, setState])
  const audition = useCallback(
    (piece: number, sourceMs: number, classMs: number) => {
      void transport.audition({ piece, sourceMs, classMs }).then(() => {
        setState(transport.observe())
      })
    },
    [transport, setState],
  )
  const pause = useCallback(() => {
    void transport.pause().then(() => {
      setState(transport.observe())
    })
  }, [transport, setState])
  return { state, audition, pause }
}
