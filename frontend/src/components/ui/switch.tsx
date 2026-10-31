import * as SwitchPrimitive from '@radix-ui/react-switch'
import type { ComponentProps } from 'react'
import { cn } from '../../lib/cn'

/** Name it with a label (htmlFor) or aria-label. The pseudo-element makes the target 44 px. */
export function Switch({ className, ...props }: ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      className={cn(
        'relative inline-flex h-7 w-12 shrink-0 cursor-pointer items-center rounded-full border-0 bg-line p-0.5 transition-colors duration-150 before:absolute before:-inset-2 before:content-[""]',
        'data-[state=checked]:bg-brand disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="block size-6 rounded-full bg-text transition-transform duration-150 data-[state=checked]:translate-x-5 data-[state=checked]:bg-on-brand" />
    </SwitchPrimitive.Root>
  )
}
