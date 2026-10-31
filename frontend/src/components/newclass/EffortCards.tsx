import { useId } from 'react'
import { EFFORTS, type Effort } from '../../lib/sketch'

/** Easy, Moderate and Hard as three cards, by their words only (no zone numbers: the backend defines none). */
export function EffortCards({ value, onChange }: { value: Effort; onChange: (effort: Effort) => void }) {
  const group = useId()
  return (
    <div className="flex flex-col gap-2">
      <span id={`${group}-label`} className="text-[0.9375rem] font-medium text-text">
        Effort
      </span>
      <div role="radiogroup" aria-labelledby={`${group}-label`} className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {EFFORTS.map((e) => (
          <label
            key={e.value}
            className="relative flex min-h-11 cursor-pointer flex-col gap-2 rounded-md border border-line bg-raised p-4 hover:bg-line has-checked:border-brand has-checked:bg-surface has-checked:outline-2 has-checked:outline-brand has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-brand"
          >
            <input
              type="radio"
              name={`${group}-effort`}
              value={e.value}
              checked={value === e.value}
              aria-labelledby={`${group}-${e.value}`}
              onChange={() => {
                onChange(e.value)
              }}
              className="sr-only"
            />
            <span id={`${group}-${e.value}`} className="font-display text-lg font-bold text-text">
              {e.label}
            </span>
          </label>
        ))}
      </div>
    </div>
  )
}
