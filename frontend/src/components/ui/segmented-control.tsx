import { useRef, type KeyboardEvent, type ReactNode } from 'react'
import { cn } from '../../lib/cn'

export interface SegmentedOption<T extends string> {
  value: T
  label: ReactNode
  disabled?: boolean
}

type Name = { 'aria-label': string; 'aria-labelledby'?: never } | { 'aria-labelledby': string; 'aria-label'?: never }

/**
 * The one control for choosing one of a few options (ADR 0028 section 2): sport, length, music
 * source, effort, genre filters, theme. A radio group: one stop in the tab order (the selected
 * option), the arrow keys move and select, Home and End jump. Colours come from tokens only, so it
 * follows the theme.
 */
export function SegmentedControl<T extends string>({
  options,
  value,
  onValueChange,
  className,
  ...name
}: {
  options: readonly SegmentedOption<T>[]
  value: T
  onValueChange: (value: T) => void
  className?: string
} & Name) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const enabled = options.flatMap((o, i) => (o.disabled ? [] : [i]))
  const selected = options.findIndex((o) => o.value === value)
  // The tab stop is the selected option, or the first enabled one when nothing (valid) is selected.
  const stop = selected >= 0 && !options[selected]?.disabled ? selected : enabled[0]

  const move = (to: number | undefined) => {
    const option = to === undefined ? undefined : options[to]
    if (!option) return
    refs.current[to as number]?.focus()
    onValueChange(option.value)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const at = enabled.indexOf(index)
    if (at < 0 || enabled.length === 0) return
    const last = enabled.length - 1
    let next: number | undefined
    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        next = enabled[at === last ? 0 : at + 1]
        break
      case 'ArrowLeft':
      case 'ArrowUp':
        next = enabled[at === 0 ? last : at - 1]
        break
      case 'Home':
        next = enabled[0]
        break
      case 'End':
        next = enabled[last]
        break
      default:
        return
    }
    event.preventDefault()
    move(next)
  }

  return (
    <div
      role="radiogroup"
      className={cn('inline-flex flex-wrap gap-1 rounded-3xl bg-raised p-1', className)}
      {...name}
    >
      {options.map((option, index) => (
        <button
          key={option.value}
          ref={(el) => {
            refs.current[index] = el
          }}
          type="button"
          role="radio"
          aria-checked={index === selected}
          disabled={option.disabled}
          tabIndex={index === stop ? 0 : -1}
          onClick={() => {
            onValueChange(option.value)
          }}
          onKeyDown={(event) => {
            onKeyDown(event, index)
          }}
          className={cn(
            'inline-flex min-h-11 cursor-pointer items-center justify-center rounded-full border-0 bg-transparent px-4 text-[0.9375rem] font-medium text-muted transition-colors duration-150',
            'hover:text-text aria-checked:bg-brand aria-checked:text-on-brand',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
