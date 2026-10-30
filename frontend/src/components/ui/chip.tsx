import type { ButtonHTMLAttributes, HTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

const tones = {
  neutral: 'bg-raised text-text',
  ok: 'bg-ok-soft text-ok',
  warn: 'bg-warn-soft text-warn',
  bad: 'bg-bad-soft text-bad',
} as const

/** A status label (not interactive). */
export function Chip({
  tone = 'neutral',
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: keyof typeof tones }) {
  return (
    <span
      className={cn('inline-flex items-center gap-1 rounded-full px-3 py-1 text-[0.8125rem] font-medium', tones[tone], className)}
      {...props}
    />
  )
}

/** A choice chip: a real button that reports its state with aria-pressed. */
export function ChipButton({
  pressed,
  className,
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-pressed'> & { pressed: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      className={cn(
        'inline-flex min-h-11 cursor-pointer items-center gap-1 rounded-full border px-4 text-[0.9375rem] font-medium transition-colors duration-150',
        pressed ? 'border-brand bg-brand text-on-brand' : 'border-line bg-raised text-text hover:bg-line',
        className,
      )}
      {...props}
    />
  )
}
