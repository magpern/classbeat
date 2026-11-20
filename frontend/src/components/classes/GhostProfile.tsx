import { cn } from '../../lib/cn'

// Bar heights written out in full so Tailwind finds them (no inline style: the CSP refuses it).
const BARS = [
  'h-[34%]', 'h-[34%]', 'h-[51%]', 'h-[68%]', 'h-[51%]', 'h-[68%]', 'h-[85%]',
  'h-[51%]', 'h-[51%]', 'h-[51%]', 'h-[68%]', 'h-[85%]', 'h-[85%]', 'h-[34%]',
] as const

/**
 * A draft with no version yet: a ghost of a profile. It shimmers slowly (`.ghost-shimmer`,
 * motion-app.css, only under prefers-reduced-motion: no-preference) and is hidden from assistive
 * technology; the card's text says what it is.
 */
export function GhostProfile({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn('ghost-shimmer flex h-28 items-end gap-0.5 rounded-md bg-raised px-4 pt-4', className)}
    >
      {BARS.map((height, i) => (
        <span key={i} className={cn('flex-1 rounded-t-sm bg-muted/20', height)} />
      ))}
    </div>
  )
}
