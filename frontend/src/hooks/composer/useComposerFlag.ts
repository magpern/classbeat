import { useEffect, useState } from 'react'
import { getConnectionsView } from '../../api/connections'

/**
 * The effective `composer_flow` flag (ADR 0038 section 2): `null` until it is known, so the
 * workspace never starts a step (the fill) before it knows whether the composer is paused. The page
 * follows the class, not the flag; the flag only decides the paused notice. A failed read counts as
 * `onError` (default on): nothing is claimed, and the server answers `feature_off` if it is off. A caller that
 * would offer something the server refuses with the flag off passes `false`, so a failed read hides it. It is read only when `needed`, for a class
 * the server says is a composer class, so every other class never calls the connections endpoint.
 */
export function useComposerFlag(needed: boolean, onError = true): boolean | null {
  const [flag, setFlag] = useState<boolean | null>(null)
  useEffect(() => {
    if (!needed) return
    let alive = true
    getConnectionsView().then(
      (view) => {
        if (alive) setFlag(view.composerFlow)
      },
      () => {
        if (alive) setFlag(onError)
      },
    )
    return () => {
      alive = false
    }
  }, [needed, onError])
  return flag
}
