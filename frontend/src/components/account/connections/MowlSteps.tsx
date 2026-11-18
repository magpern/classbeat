import type { CatalogueKey } from '../../../i18n/en'
import { useT } from '../../../i18n/I18n'

const STEPS = ['mowlqr.step.open', 'mowlqr.step.choose', 'mowlqr.step.point'] as const satisfies readonly CatalogueKey[]

/** The three steps beside the code (ADR 0013 Afterburn A3); the first one is highlighted in the heat line. */
export function MowlSteps() {
  const t = useT()
  return (
    <ol className="m-0 flex list-none flex-col gap-3.5 p-0">
      {STEPS.map((key, i) => (
        <li key={key} className="flex items-start gap-3.5">
          <span
            aria-hidden="true"
            className={`inline-flex size-7 shrink-0 items-center justify-center rounded-full text-sm font-bold ${i === 0 ? 'bg-heat text-on-heat' : 'bg-raised text-text'}`}
          >
            {i + 1}
          </span>
          <span className="pt-0.5 text-[0.9375rem]">{t(key)}</span>
        </li>
      ))}
    </ol>
  )
}
