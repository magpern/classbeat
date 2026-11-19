import { useT } from '../../i18n/I18n'
import { SketchBars } from '../newclass/SketchBars'
import { EYEBROW } from './FieldCard'

// An example shape (level, seconds): warm-up, three building hills, a sprint finish. Not the user's class.
const EXAMPLE: [number, number][] = [
  [1, 300], [2, 240], [3, 360], [2, 120], [3, 360], [4, 240], [2, 120], [4, 300], [5, 240], [2, 120], [5, 180],
]
const STEPS = [1, 2, 3] as const

/**
 * The right column of the start screens: the sketch on the stage and "What happens next" in three steps. Both are
 * static and say so; the stage keeps its own colours and no heat (ADR 0013 Afterburn A1).
 */
export function StartAside() {
  const t = useT()
  return (
    <aside className="flex min-w-0 flex-col gap-4">
      <section className="flex flex-col gap-3 rounded-3xl bg-stage p-5 text-stage-text">
        <h2 className="m-0 text-xs font-semibold uppercase tracking-[0.14em] text-stage-muted">{t('start.sketch.title')}</h2>
        <SketchBars profile={EXAMPLE} label={t('start.sketch.aria')} className="h-36 rounded-none" />
        <p className="m-0 text-[0.8125rem] text-stage-muted">{t('start.sketch.note')}</p>
      </section>
      <section className="flex flex-col gap-4 rounded-3xl border border-line bg-surface p-5 shadow-card">
        <h2 className={EYEBROW}>{t('start.next.title')}</h2>
        <ol className="m-0 flex list-none flex-col gap-4 p-0">
          {STEPS.map((n) => (
            <li key={n} className="flex items-start gap-3.5">
              <span aria-hidden="true" className="w-7 font-display text-[1.375rem] leading-none text-brand">
                {n}
              </span>
              <span className="flex min-w-0 flex-col gap-0.5">
                <strong className="text-[0.9375rem] text-text">{t(`start.next.${n}.title`)}</strong>
                <span className="text-[0.875rem] text-muted">{t(`start.next.${n}.body`)}</span>
              </span>
            </li>
          ))}
        </ol>
      </section>
    </aside>
  )
}
