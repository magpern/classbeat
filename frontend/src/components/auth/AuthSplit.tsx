import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'
import { useResolvedTheme } from '../../theme/useResolvedTheme'
import { resolveSlot } from '../imagery/imageSlots'
import { SlotPicture } from '../imagery/SlotPicture'

/**
 * The Sign in and Sign up screens: the card, and beside it on a desktop a picture of the studio.
 * The picture is decorative and never shown on a phone or a tablet; with no picture for the
 * theme the card stands alone, as on the other focus pages.
 */
export function AuthSplit({ children }: { children: ReactNode }) {
  const theme = useResolvedTheme()
  const panel = resolveSlot('authPanel', theme) !== null
  return (
    <div className={cn('auth-split', panel && 'auth-split--panel')}>
      {children}
      {panel ? (
        <aside className="auth-panel" aria-hidden="true">
          <SlotPicture slot="authPanel" sizes="(min-width: 64rem) 34rem, 1px" className="slot--fill-panel" />
        </aside>
      ) : null}
    </div>
  )
}
