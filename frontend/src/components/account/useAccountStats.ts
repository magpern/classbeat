import { useCallback, useEffect, useState } from 'react'
import { getConnectionsView } from '../../api/connections'
import { getPlan } from '../../api/plan'
import { getSpotifyStatus } from '../../api/spotify'
import { classesLine } from './classesLine'

export interface AccountStats {
  /** The plan's name, null while unknown or when it could not be read. */
  planName: string | null
  /** "3 of 30 classes in November", null while unknown. */
  classes: string | null
  /** How many of the two required connections (MOWL, Spotify) are linked, null while unknown. */
  linked: number | null
}

const NONE: AccountStats = { planName: null, classes: null, linked: null }

/**
 * What the Account header's three tiles show. Read-only: each call is settled on its own, so one
 * that fails leaves only its tile out. The Connections tab reports later changes itself.
 */
export function useAccountStats(): AccountStats & { reload: () => void } {
  const [stats, setStats] = useState<AccountStats>(NONE)
  const [round, setRound] = useState(0)
  useEffect(() => {
    const abort = new AbortController()
    const patch = (part: Partial<AccountStats>) => {
      if (!abort.signal.aborted) setStats((was) => ({ ...was, ...part }))
    }
    getPlan(abort.signal).then(
      (plan) => {
        patch({ planName: plan.name, classes: classesLine(plan) })
      },
      () => undefined,
    )
    const mowl = getConnectionsView().then(
      (view) => view.connections.some((c) => c.provider === 'mowl' && c.status === 'connected'),
      () => null,
    )
    const spotify = getSpotifyStatus(abort.signal).then(
      (status) => status !== null && status.status === 'connected',
      () => false, // Spotify is switched off or unreadable: not linked
    )
    void Promise.all([mowl, spotify]).then(([m, s]) => {
      if (m !== null) patch({ linked: (m ? 1 : 0) + (s ? 1 : 0) })
    })
    return () => {
      abort.abort()
    }
  }, [round])
  const reload = useCallback(() => {
    setRound((n) => n + 1)
  }, [])
  return { ...stats, reload }
}
