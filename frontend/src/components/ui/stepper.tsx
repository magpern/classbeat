import { Minus, Plus } from 'lucide-react'
import { useId } from 'react'

const BUTTON =
  'inline-flex size-11 shrink-0 cursor-pointer items-center justify-center rounded-full border border-line bg-raised text-text hover:bg-line disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand'

/**
 * The one stepper (ADR 0028 section 2): a number with − and + buttons. The number stays a real,
 * labelled input (typing works); the buttons stop at the limits. `value` is the form's text, so a
 * half-typed number is kept. `hint` is a short, already worded line beside the label ("0 to 10").
 */
export function Stepper({
  label,
  fewer,
  more,
  hint,
  value,
  min,
  max,
  onChange,
}: {
  label: string
  /** The button names, already worded: "Fewer climbs", "More climbs". */
  fewer: string
  more: string
  hint?: string
  value: string
  min: number
  max: number
  onChange: (value: string) => void
}) {
  const id = useId()
  const n = value.trim() === '' ? Number.NaN : Number(value)
  const valid = Number.isInteger(n)
  const step = (by: number) => {
    const from = valid ? n : min
    onChange(String(Math.min(max, Math.max(min, from + by))))
  }
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <label htmlFor={id} className="text-[0.9375rem] font-medium text-text">
          {label}
        </label>
        {hint ? <span className="text-[0.8125rem] whitespace-nowrap text-muted">{hint}</span> : null}
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className={BUTTON}
          aria-label={fewer}
          disabled={valid && n <= min}
          onClick={() => {
            step(-1)
          }}
        >
          <Minus aria-hidden="true" size={18} />
        </button>
        <input
          id={id}
          type="number"
          inputMode="numeric"
          min={min}
          max={max}
          value={value}
          onChange={(e) => {
            onChange(e.target.value)
          }}
          className="min-h-11 w-16 rounded-md border border-line bg-raised text-center text-base text-text tabular-nums focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
        />
        <button
          type="button"
          className={BUTTON}
          aria-label={more}
          disabled={valid && n >= max}
          onClick={() => {
            step(1)
          }}
        >
          <Plus aria-hidden="true" size={18} />
        </button>
      </div>
    </div>
  )
}
