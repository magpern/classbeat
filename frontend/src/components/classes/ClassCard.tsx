import type { ClassSummary } from '../../api/classes'
import { classStatus, hasZone7, lengthLabel, profileSeconds, profileTss } from '../../lib/classCards'
import { Link } from '../../router/Router'
import { Card } from '../ui/card'
import { Chip } from '../ui/chip'
import { MiniProfile } from './MiniProfile'
import { TssValue } from './TssValue'

/** One class: mini profile on the stage, title, length, TSS and a status chip. Titles are text. */
export function ClassCard({ cls }: { cls: ClassSummary }) {
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
            TSS <TssValue value={profileTss(cls.profile)} approximate={hasZone7(cls.profile)} />
          </span>
        </p>
      ) : null}
      <p className="m-0">
        <Chip tone={status.tone}>{status.label}</Chip>
      </p>
    </Card>
  )
}
