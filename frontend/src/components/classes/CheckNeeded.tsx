import { TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'
import { Card } from '../ui/card'
import { Chip } from '../ui/chip'

/**
 * A "Check needed" card (ADR 0013 section 5): the frame of the panels that say classbeat could not
 * tell whether a write reached MOWL. Only the frame is new; each panel keeps its own wording and
 * actions.
 */
export function CheckNeeded({
  children,
  className,
  ...props
}: {
  children: ReactNode
  className?: string
  'data-testid'?: string
  'aria-labelledby'?: string
  'data-kind'?: string
}) {
  return (
    <Card className={cn('flex flex-col gap-3 border-warn', className)} {...props}>
      <Chip tone="warn" className="self-start">
        <TriangleAlert aria-hidden="true" size={14} />
        Check needed
      </Chip>
      {children}
    </Card>
  )
}
