import type { ClassSummary } from '../../api/classes'
import { useT } from '../../i18n/I18n'
import { lengthLabel, profileSeconds, profileTss } from '../../lib/classCards'
import { TssValue } from './TssValue'

/** Time and TSS of a class with a version, as eyebrow labels over numbers. */
export function ClassFacts({ cls }: { cls: ClassSummary }) {
  const t = useT()
  const seconds = profileSeconds(cls.profile)
  if (seconds <= 0) return null
  return (
    <dl className="m-0 grid grid-cols-2 gap-3 tabular-nums">
      <div className="flex flex-col gap-0.5">
        <dt className="text-xs font-semibold tracking-[0.14em] text-muted uppercase">{t('class.list.time')}</dt>
        <dd className="m-0 text-xl font-bold">{lengthLabel(seconds)}</dd>
      </div>
      <div className="flex flex-col gap-0.5">
        <dt className="text-xs font-semibold tracking-[0.14em] text-muted uppercase">{t('class.list.tss')}</dt>
        <dd className="m-0 text-xl font-bold">
          <TssValue value={profileTss(cls.profile)} approximate={cls.tssApproximate} />
        </dd>
      </div>
    </dl>
  )
}
