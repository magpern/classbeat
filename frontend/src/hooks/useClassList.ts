import { useEffect, useState } from 'react'
import { listClasses, type ClassSummary } from '../api/classes'

export type Listing =
  | { status: 'loading' }
  | { status: 'failed' }
  | { status: 'ready'; classes: ClassSummary[] }

/** The class list from the existing API, with a retry. */
export function useClassList(): { listing: Listing; retry: () => void } {
  const [listing, setListing] = useState<Listing>({ status: 'loading' })
  const [round, setRound] = useState(0)
  useEffect(() => {
    const abort = new AbortController()
    listClasses(abort.signal).then(
      (classes) => {
        if (!abort.signal.aborted) setListing({ status: 'ready', classes })
      },
      () => {
        if (!abort.signal.aborted) setListing({ status: 'failed' })
      },
    )
    return () => {
      abort.abort()
    }
  }, [round])
  return {
    listing,
    retry: () => {
      setListing({ status: 'loading' })
      setRound((n) => n + 1)
    },
  }
}
