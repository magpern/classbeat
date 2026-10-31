import { Minus, Plus } from 'lucide-react'
import { useId } from 'react'

const BUTTON =
  'inline-flex size-11 shrink-0 cursor-pointer items-center justify-center rounded-full border border-line bg-raised text-text hover:bg-line disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand'

/**
 * A number with − and + buttons. The number stays a real, labelled input (typing works as before);
 * the buttons stop at the limits. `value` is the form's text, so a half-typed number is kept.
 */
export function Stepper({
  label,
  noun,
  value,
  min,
  max,
  onChange,
}: {
  label: string
  /** For the button names: "Fewer climbs", "More climbs". */
  noun: string
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
      <label htmlFor={id} className="text-[0.9375rem] font-medium text-text">
        {label}
      </label>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className={BUTTON}
          aria-label={`Fewer ${noun}`}
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
          aria-label={`More ${noun}`}
          disabled={valid && n >= max}
          onClick={() => {
            step(1)
          }}
        >
          <Plus aria-hidden="true" size={18} />
        </button>
        <span className="text-[0.8125rem] text-muted">
          {String(min)} to {String(max)}
        </span>
      </div>
    </div>
  )
}
