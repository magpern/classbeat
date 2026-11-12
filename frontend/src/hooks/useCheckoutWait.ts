import { useCallback, useEffect, useRef, useState } from 'react'
import type { Billing } from '../api/billing'
import { useRouter } from '../router/Router'
import { GIVE_UP_MS, POLL_MS, forget, landed, recall, remember, returnOf, withoutReturn, type Pending } from '../lib/checkoutWait'

/**
 * `redirecting`: on the way to the hosted page. `waiting`: back, the plan is read until the notification lands.
 * `slow`: two minutes passed; the plan will update by itself. `landed`: the plan changed. `failed`: the payment
 * failed and the plan is unchanged.
 */
export type CheckoutStatus = 'idle' | 'redirecting' | 'waiting' | 'slow' | 'landed' | 'failed'

export interface CheckoutWait {
  status: CheckoutStatus
  pending: Pending | null
  /** Starts the wait for `pending` (an upgrade that only asked Paddle). */
  begin: (pending: Pending) => void
  /** Goes to the hosted page: remembers what is paid for first. */
  leave: (pending: Pending) => void
  /** Looks again after the wait gave up. */
  again: () => void
  dismiss: () => void
}

/**
 * Reads the plan every `POLL_MS` for up to `GIVE_UP_MS` until a payment lands. On mount it reads the return
 * address: `?checkout=return` resumes the wait for what was remembered before leaving, `?checkout=failed`
 * shows the failure. `reload` is the billing hook's: it never shows a loading state.
 */
export function useCheckoutWait(reload: () => Promise<Billing | null>, onLanded: (paid: Pending) => void): CheckoutWait {
  const { path, search, navigate } = useRouter()
  // Read while rendering, not in the effect: a strict-mode remount sees the address already cleaned.
  const [arrival] = useState(() => {
    const back = returnOf(search)
    const wanted = recall()
    const status: CheckoutStatus = back === 'failed' ? 'failed' : back === 'return' && wanted !== null ? 'waiting' : 'idle'
    return { back, wanted, status }
  })
  const [status, setStatus] = useState<CheckoutStatus>(arrival.status)
  const [pending, setPending] = useState<Pending | null>(arrival.status === 'idle' ? null : arrival.wanted)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const alive = useRef(true)
  const landedRef = useRef(onLanded)
  useEffect(() => {
    landedRef.current = onLanded
  })

  const stop = useCallback(() => {
    if (timer.current !== null) clearTimeout(timer.current)
    timer.current = null
  }, [])

  /** The loop itself: reads now, then every `POLL_MS`, until it lands or `GIVE_UP_MS` passed. */
  const run = useCallback(
    (wanted: Pending) => {
      stop()
      const started = Date.now()
      const look = async () => {
        const billing = await reload()
        if (!alive.current) return
        if (billing !== null && landed(billing, wanted)) {
          forget()
          setStatus('landed')
          landedRef.current(wanted)
        } else if (Date.now() - started >= GIVE_UP_MS) {
          setStatus('slow')
        } else {
          timer.current = setTimeout(() => void look(), POLL_MS)
        }
      }
      void look()
    },
    [reload, stop],
  )

  useEffect(() => {
    alive.current = true
    if (arrival.back !== null) {
      navigate(`${path}${withoutReturn(search)}${window.location.hash}`, { replace: true, scroll: false })
    }
    if (arrival.back === 'failed') forget() // nothing is left to wait for
    if (arrival.status === 'waiting' && arrival.wanted !== null) run(arrival.wanted)
    return () => {
      alive.current = false
      stop()
    }
    // The address is cleaned once, on mount; `path` and `navigate` are the mount's.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [arrival, run, stop])

  const begin = useCallback(
    (wanted: Pending) => {
      setPending(wanted)
      setStatus('waiting')
      run(wanted)
    },
    [run],
  )

  const leave = useCallback((wanted: Pending) => {
    remember(wanted)
    setPending(wanted)
    setStatus('redirecting')
  }, [])

  const again = useCallback(() => {
    if (pending === null) return
    setStatus('waiting')
    run(pending)
  }, [pending, run])

  const dismiss = useCallback(() => {
    stop()
    forget()
    setStatus('idle')
  }, [stop])

  return { status, pending, begin, leave, again, dismiss }
}
