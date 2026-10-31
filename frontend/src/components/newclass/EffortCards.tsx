import { useId } from 'react'
import { EFFORTS, type Effort } from '../../lib/sketch'

const ZONE_NAMES = ['Recovery', 'Endurance', 'Tempo', 'Threshold', 'VO2max', 'Anaerobic', 'Neuromuscular']

/** Easy, Moderate and Hard as three cards, each with the zones the class mostly works in. */
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
              aria-describedby={`${group}-${e.value}-zones`}
              onChange={() => {
                onChange(e.value)
              }}
              className="sr-only"
            />
            <span id={`${group}-${e.value}`} className="font-display text-lg font-bold text-text">
              {e.label}
            </span>
            <span id={`${group}-${e.value}-zones`} className="text-[0.8125rem] text-muted">
              Zones {String(e.low)} to {String(e.high)}, {ZONE_NAMES[e.low - 1]} to {ZONE_NAMES[e.high - 1]}
            </span>
            <span aria-hidden="true" className="flex h-2 gap-0.5">
              {[1, 2, 3, 4, 5, 6, 7].map((z) => (
                <span
                  key={z}
                  className="flex-1 rounded-full"
                  style={{
                    background: `var(--cb-zone-${String(z)})`,
                    opacity: z >= e.low && z <= e.high ? 1 : 0.25,
                  }}
                />
              ))}
            </span>
          </label>
        ))}
      </div>
    </div>
  )
}
