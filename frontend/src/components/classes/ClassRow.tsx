import type { ReactNode } from 'react'
import type { ClassSummary } from '../../api/classes'
import { Trans, useT } from '../../i18n/I18n'
import { classStatus, lengthLabel, profileSeconds, profileTss } from '../../lib/classCards'
import { Link } from '../../router/Router'
import { Chip } from '../ui/chip'
import { ListRow } from '../ui/list-row'
import { MiniProfile } from './MiniProfile'
import { TssValue } from './TssValue'

/** The list of classes (Home and My classes): one bordered block of rows. */
export function ClassList({ children }: { children: ReactNode }) {
  return (
    <ul className="m-0 flex list-none flex-col divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface p-0">
      {children}
    </ul>
  )
}

/**
 * One class as a list row (ADR 0028 section 2): the mini profile on the stage, the title as a link,
 * length and TSS, and a status chip. Titles are text. `actions` goes at the end of the row.
 */
export function ClassRow({ cls, actions }: { cls: ClassSummary; actions?: ReactNode }) {
  useT() // renders again when the language changes: the status and length are looked up on each render
  const status = classStatus(cls)
  const seconds = profileSeconds(cls.profile)
  return (
    <ListRow
      className="flex-wrap sm:flex-nowrap"
      leading={
        <div className="w-24 shrink-0 sm:w-36">
          <MiniProfile profile={cls.profile} />
        </div>
      }
      title={
        <Link to={`/classes/${cls.id}`} className="min-w-0 text-text no-underline wrap-anywhere hover:underline focus-visible:underline">
          {cls.title}
        </Link>
      }
      description={
        <span className="flex flex-col items-start gap-1.5">
          {seconds > 0 ? (
            <span className="flex gap-4 tabular-nums">
              <span>{lengthLabel(seconds)}</span>
              <span>
                <Trans
                  id="class.card.tss"
                  slots={{
                    value: <TssValue value={profileTss(cls.profile)} approximate={cls.tssApproximate} />,
                  }}
                />
              </span>
            </span>
          ) : null}
          <Chip tone={status.tone}>{status.label}</Chip>
        </span>
      }
      action={actions}
    />
  )
}
