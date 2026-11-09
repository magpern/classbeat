import { useEffect, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { keepTabInside, lockPageBehind } from '../lib/modalLock'

/**
 * Where a guard prompt (recent authentication, step-up) is shown: a layer of its own at the end of
 * `<body>`, above everything else, the page and any open dialog `inert` behind it while it shows.
 * A prompt asked for from inside a dialog (Connect Spotify, say) would otherwise sit in the page
 * that dialog made `inert`: visible, but nothing in it could be typed in or clicked. Focus moves
 * into the prompt, Tab stays inside it, and focus goes back where it was when the prompt goes.
 */
export function PromptLayer({ labelledBy, children }: { labelledBy: string; children: ReactNode }) {
  const [node, setNode] = useState<HTMLDivElement | null>(null)

  useEffect(() => {
    if (node === null) return undefined
    const before = document.activeElement
    const unlock = lockPageBehind(node)
    node.querySelector<HTMLElement>('[role="dialog"]')?.focus()
    return () => {
      unlock()
      const back = before instanceof HTMLElement && before !== document.body && before.isConnected ? before : null
      back?.focus()
      if (back !== null && document.activeElement === back) return
      // The control was busy (disabled) and lost focus: a dialog still open below takes it, so Tab
      // stays inside that dialog.
      const below = [...document.querySelectorAll<HTMLElement>('[role="dialog"]')].filter((el) => !node.contains(el))
      below.at(-1)?.focus()
    }
  }, [node])

  return createPortal(
    <div ref={setNode} className="overlay z-60">
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        onKeyDown={(event) => {
          keepTabInside(event, event.currentTarget)
        }}
      >
        {children}
      </div>
    </div>,
    document.body,
  )
}
