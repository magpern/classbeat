import { useEffect, useRef } from 'react'
import type { WorkspaceTab } from '../components/classes/Workspace'

function go(id: string): void {
  const target = document.getElementById(id)
  target?.scrollIntoView?.({ block: 'start' })
  if (target?.hasAttribute('tabindex') === true) target.focus()
  else target?.querySelector<HTMLElement>('button, input, textarea, a')?.focus()
}

/**
 * Scrolls to a panel of the class and moves focus into it (a panel with tabindex -1 takes focus
 * itself, any other its first control). On a phone the panel may be on the Chat tab: the Class tab
 * opens first, and the move runs in an effect once that tab has rendered.
 */
export function useGoToPanel(tab: WorkspaceTab, setTab: (tab: WorkspaceTab) => void): (id: string) => void {
  const pending = useRef<string | null>(null)
  useEffect(() => {
    if (tab !== 'class' || pending.current === null) return
    go(pending.current)
    pending.current = null
  }, [tab])
  return (id: string) => {
    if (tab === 'class') go(id)
    else {
      pending.current = id
      setTab('class')
    }
  }
}
