import type { WorkspaceTab } from '../components/classes/Workspace'

/**
 * Scrolls to a panel of the class and moves focus into it (a panel with tabindex -1 takes focus
 * itself, any other its first control). On a phone the panel may be on the Chat tab: the Class tab
 * opens first.
 */
export function useGoToPanel(tab: WorkspaceTab, setTab: (tab: WorkspaceTab) => void): (id: string) => void {
  return (id: string) => {
    const go = () => {
      const target = document.getElementById(id)
      target?.scrollIntoView?.({ block: 'start' })
      if (target?.hasAttribute('tabindex') === true) target.focus()
      else target?.querySelector<HTMLElement>('button, input, textarea, a')?.focus()
    }
    if (tab === 'class') go()
    else {
      setTab('class')
      setTimeout(go, 0)
    }
  }
}
