import type { ReactNode } from 'react'
import type { ClassSummary } from '../../api/classes'
import { Trans, useT } from '../../i18n/I18n'
import { classStatus, lengthLabel, profileSeconds, profileTss } from '../../lib/classCards'
import { Link } from '../../router/Router'
import { Card } from '../ui/card'
import { Chip } from '../ui/chip'
import { MiniProfile } from './MiniProfile'
import { TssValue } from './TssValue'

/** One class: mini profile on the stage, title, length, TSS and a status chip. Titles are text. */
export function ClassCard({ cls, actions }: { cls: ClassSummary; actions?: ReactNode }) {
  useT() // renders again when the language changes: the status and length are looked up on each render
  const status = classStatus(cls)
  const seconds = profileSeconds(cls.profile)
  return (
    <Card className="flex flex-col gap-3 p-4">
      <MiniProfile profile={cls.profile} />
      <h3 className="m-0 font-display text-lg font-bold">
        <Link
          to={`/classes/${cls.id}`}
          className="text-text no-underline hover:underline focus-visible:underline"
        >
          {cls.title}
        </Link>
      </h3>
      {seconds > 0 ? (
        <p className="m-0 flex gap-4 text-[0.9375rem] text-muted tabular-nums">
          <span>{lengthLabel(seconds)}</span>
          <span>
            <Trans
              id="class.card.tss"
              slots={{
                value: <TssValue value={profileTss(cls.profile)} approximate={cls.tssApproximate} />,
              }}
            />
          </span>
        </p>
      ) : null}
      <p className="m-0 flex flex-wrap items-center justify-between gap-2">
        <Chip tone={status.tone}>{status.label}</Chip>
        {actions}
      </p>
    </Card>
  )
}
