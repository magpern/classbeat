import type { ReactNode } from 'react'
import type { ClassSummary } from '../../api/classes'
import { useT } from '../../i18n/I18n'
import { classStatus } from '../../lib/classCards'
import { Link } from '../../router/Router'
import { Button } from '../ui/button'
import { Chip } from '../ui/chip'
import { ClassFacts } from './ClassFacts'
import { MiniProfile } from './MiniProfile'
import { ZoneLegend } from './ZoneLegend'

/**
 * The newest class with a version, large (ADR 0013 Afterburn A3): the profile on the stage with a
 * zone legend, the status, the title, Time and TSS, Open class and the row's other actions. The
 * request is not quoted: the class list does not carry the brief.
 */
export function ClassFeature({ cls, actions }: { cls: ClassSummary; actions?: ReactNode }) {
  const t = useT()
  const status = classStatus(cls)
  return (
    <article
      aria-labelledby={`feature-${cls.id}`}
      className="glow-card relative flex flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-card lg:flex-row"
    >
      <div aria-hidden="true" className="absolute inset-x-0 top-0 z-10 h-1 bg-heat" />
      <div className="flex min-w-0 flex-col gap-3 bg-stage p-5 pt-6 sm:p-7 sm:pt-8 lg:flex-[3]">
        <span className="text-xs font-semibold tracking-[0.14em] text-stage-muted uppercase">
          {t('class.list.latest')}
        </span>
        <MiniProfile profile={cls.profile} motion="hover" className="h-40 sm:h-48" />
        <ZoneLegend />
      </div>
      <div className="flex min-w-0 flex-col gap-4 p-5 sm:p-7 lg:flex-[2]">
        <Chip tone={status.tone} dot className="self-start text-left">
          {status.label}
        </Chip>
        <h2 id={`feature-${cls.id}`} className="m-0 font-display text-2xl font-extrabold sm:text-3xl">
          <Link to={`/classes/${cls.id}`} className="text-text no-underline hover:underline focus-visible:underline">
            {cls.title}
          </Link>
        </h2>
        <ClassFacts cls={cls} />
        <div className="mt-auto flex flex-wrap gap-2 pt-2">
          <Button asChild variant="ghost" className="bg-text text-bg hover:bg-text hover:opacity-90">
            <Link to={`/classes/${cls.id}`}>
              {t('class.list.open')}
            </Link>
          </Button>
          {actions}
        </div>
      </div>
    </article>
  )
}
