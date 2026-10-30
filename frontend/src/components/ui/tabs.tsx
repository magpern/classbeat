import * as TabsPrimitive from '@radix-ui/react-tabs'
import type { ComponentProps } from 'react'
import { cn } from '../../lib/cn'

export const Tabs = TabsPrimitive.Root

export function TabsList({ className, ...props }: ComponentProps<typeof TabsPrimitive.List>) {
  return <TabsPrimitive.List className={cn('flex gap-1 overflow-x-auto border-b border-line', className)} {...props} />
}

export function TabsTrigger({ className, ...props }: ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        'min-h-11 cursor-pointer border-0 border-b-2 border-transparent bg-transparent px-4 text-[0.9375rem] font-medium whitespace-nowrap text-muted',
        'hover:text-text data-[state=active]:border-brand data-[state=active]:text-text',
        className,
      )}
      {...props}
    />
  )
}

export function TabsContent({ className, ...props }: ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content className={cn('pt-4', className)} {...props} />
}
