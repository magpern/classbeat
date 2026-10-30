import * as ToggleGroupPrimitive from '@radix-ui/react-toggle-group'
import type { ComponentProps } from 'react'
import { cn } from '../../lib/cn'

export function ToggleGroup({ className, ...props }: ComponentProps<typeof ToggleGroupPrimitive.Root>) {
  return (
    <ToggleGroupPrimitive.Root
      className={cn('inline-flex flex-wrap gap-1 rounded-full bg-raised p-1', className)}
      {...props}
    />
  )
}

export function ToggleGroupItem({ className, ...props }: ComponentProps<typeof ToggleGroupPrimitive.Item>) {
  return (
    <ToggleGroupPrimitive.Item
      className={cn(
        'inline-flex min-h-11 cursor-pointer items-center justify-center rounded-full border-0 bg-transparent px-4 text-[0.9375rem] font-medium text-muted transition-colors duration-150',
        'hover:text-text data-[state=on]:bg-brand data-[state=on]:text-on-brand',
        className,
      )}
      {...props}
    />
  )
}
