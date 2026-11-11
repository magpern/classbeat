import { useCallback, useEffect, useState } from 'react'
import { getBilling, type Billing } from '../api/billing'

/** `closed`: the caller may not buy (no price, no buy action). `failed`: the answer did not arrive. */
export type BillingState =
  | { status: 'loading' }
  | { status: 'closed' }
  | { status: 'failed' }
  | { status: 'ready'; billing: Billing }

const settled = (billing: Billing | null): BillingState =>
  billing === null ? { status: 'closed' } : { status: 'ready', billing }

export function useBilling() {
  const [state, setState] = useState<BillingState>({ status: 'loading' })
  useEffect(() => {
    const abort = new AbortController()
    getBilling(abort.signal).then(
      (found) => {
        if (!abort.signal.aborted) setState(settled(found))
      },
      () => {
        if (!abort.signal.aborted) setState({ status: 'failed' })
      },
    )
    return () => {
      abort.abort()
    }
  }, [])

  /** Reads the quotes again without showing a loading state; the fresh view, or null when it failed. */
  const reload = useCallback(async (): Promise<Billing | null> => {
    try {
      const found = await getBilling()
      setState(settled(found))
      return found
    } catch {
      return null
    }
  }, [])

  /** Takes the view a billing action answered with. */
  const replace = useCallback((billing: Billing) => {
    setState({ status: 'ready', billing })
  }, [])

  return { state, reload, replace }
}
