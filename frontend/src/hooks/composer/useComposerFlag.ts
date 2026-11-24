import { useEffect, useState } from 'react'
import { getConnectionsView } from '../../api/connections'

/**
 * The effective `composer_flow` flag (ADR 0038 section 2): `null` until it is known, so the
 * workspace never starts a step (the fill) before it knows whether the composer is paused. The page
 * follows the class, not the flag; the flag only decides the paused notice. A failed read counts as
 * on: nothing is claimed, and the server answers `feature_off` if it is off. It is read only when `needed`, for a class
 * the server says is a composer class, so every other class never calls the connections endpoint.
 */
export function useComposerFlag(needed: boolean): boolean | null {
  const [flag, setFlag] = useState<boolean | null>(null)
  useEffect(() => {
    if (!needed) return
    let alive = true
    getConnectionsView().then(
      (view) => {
        if (alive) setFlag(view.composerFlow)
      },
      () => {
        if (alive) setFlag(true)
      },
    )
    return () => {
      alive = false
    }
  }, [needed])
  return flag
}
