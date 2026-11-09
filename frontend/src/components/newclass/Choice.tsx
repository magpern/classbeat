import { useId, type ReactNode } from 'react'
import { SegmentedControl, type SegmentedOption } from '../ui/segmented-control'

/**
 * A labelled `SegmentedControl` (ADR 0028 section 2): the visible label names the radio group, and
 * `children` (a helper line, an input) sit under it. `hideLabel` is for a control that a heading
 * already names: the label then only names the group for assistive technology.
 */
export function Choice<T extends string>({
  label,
  hideLabel = false,
  options,
  value,
  onValueChange,
  children,
}: {
  label: string
  hideLabel?: boolean
  options: readonly SegmentedOption<T>[]
  value: T
  onValueChange: (value: T) => void
  children?: ReactNode
}) {
  const id = useId()
  const name = hideLabel ? { 'aria-label': label } : { 'aria-labelledby': id }
  return (
    <div className="flex min-w-0 flex-col gap-2">
      {hideLabel ? null : (
        <span id={id} className="text-[0.9375rem] font-medium text-text">
          {label}
        </span>
      )}
      <SegmentedControl options={options} value={value} onValueChange={onValueChange} className="max-w-full" {...name} />
      {children}
    </div>
  )
}
