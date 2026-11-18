import { useEffect, useState } from 'react'
import { getConnectionsView } from '../../api/connections'

/**
 * The effective `composer_flow` flag (ADR 0038 section 2): `null` until it is known, so the class
 * page picks one body and never mounts the old one first (both would start a hand-off). A failed
 * read is the flag off: the page the class had before the flag.
 */
export function useComposerFlag(): boolean | null {
  const [flag, setFlag] = useState<boolean | null>(null)
  useEffect(() => {
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
  }, [])
  return flag
}
