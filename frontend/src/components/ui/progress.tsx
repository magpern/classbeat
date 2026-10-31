import * as ProgressPrimitive from '@radix-ui/react-progress'
import type { ComponentProps } from 'react'
import { cn } from '../../lib/cn'

export function Progress({ className, value, ...props }: ComponentProps<typeof ProgressPrimitive.Root>) {
  const pct = Math.min(100, Math.max(0, value ?? 0))
  return (
    <ProgressPrimitive.Root
      value={value}
      className={cn('relative h-2 w-full overflow-hidden rounded-full bg-raised', className)}
      {...props}
    >
      <ProgressPrimitive.Indicator
        className="h-full rounded-full bg-brand transition-[width] duration-150"
        style={{ width: `${pct}%` }}
      />
    </ProgressPrimitive.Root>
  )
}
