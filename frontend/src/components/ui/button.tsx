import { Slot } from '@radix-ui/react-slot'
import type { ButtonHTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

const variants = {
  primary: 'bg-heat text-on-heat shadow-glow hover:brightness-110',
  // The heat fill without the glow, for places the glow would spill out of its box (the signed-out header).
  flat: 'bg-heat text-on-heat hover:brightness-110',
  secondary: 'border border-solid border-line bg-raised text-text hover:bg-line',
  ghost: 'bg-transparent text-text hover:bg-raised',
  danger: 'bg-bad text-on-brand hover:opacity-90',
} as const

export type ButtonVariant = keyof typeof variants

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  /** Render the child element (for example a link) with the look of a button. */
  asChild?: boolean
}

export function Button({ variant = 'primary', asChild = false, className, type, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : 'button'
  return (
    <Comp
      {...(asChild ? {} : { type: type ?? 'button' })}
      className={cn(
        'inline-flex min-h-11 min-w-11 cursor-pointer items-center justify-center gap-2 rounded-full border-0 px-5 text-[0.9375rem] font-medium no-underline transition-[color,background-color,opacity,scale,box-shadow] duration-150 active:scale-[0.97]',
        'disabled:cursor-not-allowed disabled:opacity-50',
        variants[variant],
        className,
      )}
      {...props}
    />
  )
}
