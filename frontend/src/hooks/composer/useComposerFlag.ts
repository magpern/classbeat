import { useEffect, useState } from 'react'
import { getConnectionsView } from '../../api/connections'

/**
 * The effective `composer_flow` flag (ADR 0038 section 2): `null` until it is known, so the class
 * page picks one body and never mounts the old one first (both would start a hand-off). A failed
 * read is the flag off: the page the class had before the flag. It is read only when `needed`: the
 * page needs the flag only for a class the server says is a composer class, so every other class
 * renders as before and never waits for, or fails with, the connections call.
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
        if (alive) setFlag(false)
      },
    )
    return () => {
      alive = false
    }
  }, [needed])
  return flag
}
