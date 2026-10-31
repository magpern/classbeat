import { Check, Circle, Loader } from 'lucide-react'

type Step = { stage: string; label: string }

/** Which step the page is at, 1-based, or null when the current stage is not a step of this list. */
export function stepNumber(steps: readonly Step[], current: string | null): number | null {
  const at = steps.findIndex((s) => s.stage === current)
  return at < 0 ? null : at + 1
}

/**
 * The checklist of the Designing page: done, current and coming steps in plain words, built from
 * the job's stage events. State is told by icon and by text, never by colour alone.
 */
export function DesigningSteps({ steps, current }: { steps: readonly Step[]; current: string | null }) {
  const at = steps.findIndex((s) => s.stage === current)
  return (
    <ol className="m-0 flex list-none flex-col gap-2 p-0" aria-label="Steps">
      {steps.map((s, i) => {
        const done = i < at
        const now = i === at
        return (
          <li
            key={s.stage}
            aria-current={now ? 'step' : undefined}
            className={`flex items-center gap-3 text-[0.9375rem] ${now ? 'font-medium text-text' : 'text-muted'}`}
          >
            {done ? (
              <Check aria-hidden="true" size={18} className="shrink-0 text-ok" />
            ) : now ? (
              <Loader aria-hidden="true" size={18} className="shrink-0 text-brand" />
            ) : (
              <Circle aria-hidden="true" size={18} className="shrink-0" />
            )}
            <span>
              {s.label}
              {done ? <span className="sr-only"> (done)</span> : null}
            </span>
          </li>
        )
      })}
    </ol>
  )
}
