import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

const CONTROL =
  'min-h-11 w-full rounded-md border border-line bg-raised px-3 text-base text-text placeholder:text-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand'

function Labelled({ id, label, hint, children }: { id: string; label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-[0.9375rem] font-medium text-text">
        {label}
      </label>
      {children}
      {hint ? (
        <p id={`${id}-hint`} className="m-0 text-[0.8125rem] text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  )
}

type Props<T> = Omit<T, 'id'> & { label: string; hint?: string }

export const TextInput = forwardRef<HTMLInputElement, Props<InputHTMLAttributes<HTMLInputElement>>>(
  function TextInput({ label, hint, className, ...input }, ref) {
    const id = useId()
    return (
      <Labelled id={id} label={label} hint={hint}>
        <input
          ref={ref}
          id={id}
          aria-describedby={hint ? `${id}-hint` : undefined}
          className={cn(CONTROL, className)}
          {...input}
        />
      </Labelled>
    )
  },
)

export function TextArea({ label, hint, className, ...area }: Props<TextareaHTMLAttributes<HTMLTextAreaElement>>) {
  const id = useId()
  return (
    <Labelled id={id} label={label} hint={hint}>
      <textarea
        id={id}
        rows={4}
        aria-describedby={hint ? `${id}-hint` : undefined}
        className={cn(CONTROL, 'py-2', className)}
        {...area}
      />
    </Labelled>
  )
}

export function SelectInput({ label, hint, className, children, ...select }: Props<SelectHTMLAttributes<HTMLSelectElement>>) {
  const id = useId()
  return (
    <Labelled id={id} label={label} hint={hint}>
      <select id={id} aria-describedby={hint ? `${id}-hint` : undefined} className={cn(CONTROL, className)} {...select}>
        {children}
      </select>
    </Labelled>
  )
}
