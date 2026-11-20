import * as TabsPrimitive from '@radix-ui/react-tabs'
import type { ComponentProps } from 'react'
import { cn } from '../../lib/cn'

export const Tabs = TabsPrimitive.Root

export function TabsList({ className, ...props }: ComponentProps<typeof TabsPrimitive.List>) {
  return <TabsPrimitive.List className={cn('inline-flex max-w-full gap-1 overflow-x-auto rounded-full bg-raised p-1', className)} {...props} />
}

export function TabsTrigger({ className, ...props }: ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        'min-h-11 cursor-pointer rounded-full border-0 bg-transparent px-4 text-[0.9375rem] font-medium whitespace-nowrap text-muted',
        'hover:text-text data-[state=active]:bg-surface data-[state=active]:text-text data-[state=active]:shadow-[inset_0_-2px_0_var(--cb-tab-mark)]',
        'focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand',
        className,
      )}
      {...props}
    />
  )
}

export function TabsContent({ className, ...props }: ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content className={cn('pt-4', className)} {...props} />
}
