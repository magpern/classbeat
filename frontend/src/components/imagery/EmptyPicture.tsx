import type { ReactNode } from 'react'
import { useResolvedTheme } from '../../theme/useResolvedTheme'
import { resolveSlot, type SlotId } from './imageSlots'
import { SlotPicture } from './SlotPicture'

interface Props {
  slot: Extract<SlotId, 'emptyHome' | 'emptyClasses'>
  /** The empty-state text, unchanged; the picture sits beside it, or below it on a narrow screen. */
  children: ReactNode
}

/**
 * An empty state with a calm, decorative picture (issue 854). While the slot has no picture in the
 * current theme, only the children are drawn, exactly as before.
 */
export function EmptyPicture({ slot, children }: Props) {
  const theme = useResolvedTheme()
  if (!resolveSlot(slot, theme)) return <>{children}</>
  return (
    <div className="empty-pic">
      <div className="empty-pic__text">{children}</div>
      <SlotPicture slot={slot} sizes="(min-width: 40rem) 18rem, min(100vw, 18rem)" className="slot--empty" />
    </div>
  )
}
