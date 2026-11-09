import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

/**
 * The one sticky action bar (ADR 0028 section 2) for the primary action of a long form or list. On a
 * desktop it is a card that sticks below the top of the viewport inside the column it is placed in
 * (put it in the right column); on a phone it is a bar fixed to the bottom, and a spacer the height
 * of the bar keeps the end of the page from hiding behind it. `label` names the region.
 */
export function StickyActionBar({
  label,
  children,
  className,
}: {
  label: string
  children: ReactNode
  className?: string
}) {
  return (
    <>
      <div aria-hidden="true" className="h-bar lg:hidden" />
      <section
        aria-label={label}
        className={cn(
          'z-20 flex flex-col gap-3 border-line bg-surface p-4',
          'max-lg:fixed max-lg:inset-x-0 max-lg:bottom-0 max-lg:flex-row max-lg:items-center max-lg:justify-end max-lg:border-t max-lg:pb-[max(1rem,env(safe-area-inset-bottom))]',
          'lg:sticky lg:top-sticky lg:rounded-lg lg:border',
          className,
        )}
      >
        {children}
      </section>
    </>
  )
}
