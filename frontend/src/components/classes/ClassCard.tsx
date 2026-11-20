import type { ReactNode } from 'react'
import type { ClassSummary } from '../../api/classes'
import { useT } from '../../i18n/I18n'
import { classStatus } from '../../lib/classCards'
import { hasVersion } from '../../lib/classesView'
import { Link } from '../../router/Router'
import { Chip } from '../ui/chip'
import { ClassFacts } from './ClassFacts'
import { GhostProfile } from './GhostProfile'
import { MiniProfile } from './MiniProfile'

/**
 * One class of the grid. With a version: its mini profile on the stage. A draft (no version yet):
 * a ghost profile and "Continue". The title is the card's link, stretched over the card; the
 * actions sit above it.
 */
export function ClassCard({ cls, actions }: { cls: ClassSummary; actions?: ReactNode }) {
  const t = useT()
  const status = classStatus(cls)
  const versioned = hasVersion(cls)
  return (
    <li className="glow-card relative flex min-w-0 flex-col overflow-hidden rounded-lg border border-line bg-surface shadow-card">
      {versioned ? (
        <div className="bg-stage p-4 pb-0">
          <MiniProfile profile={cls.profile} motion="hover" className="h-24 rounded-b-none" />
        </div>
      ) : (
        <GhostProfile className="rounded-b-none" />
      )}
      <div className="flex flex-1 flex-col gap-3 p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Chip tone={status.tone} dot className="text-left">
            {status.label}
          </Chip>
          {versioned ? null : (
            <span aria-hidden="true" className="text-[0.8125rem] font-semibold text-brand">
              {t('class.list.continue')} →
            </span>
          )}
        </div>
        <h3 className="m-0 text-base leading-snug font-semibold wrap-anywhere">
          <Link
            to={`/classes/${cls.id}`}
            className="text-text no-underline after:absolute after:inset-0 after:content-[''] hover:underline focus-visible:underline"
          >
            {cls.title}
          </Link>
        </h3>
        {versioned ? <ClassFacts cls={cls} /> : null}
        {actions ? <div className="relative z-10 mt-auto flex flex-wrap gap-2 pt-1">{actions}</div> : null}
      </div>
    </li>
  )
}
