import type { ReactNode } from 'react'
import type { Refusal } from '../../api/songMessages'
import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { Notice } from '../forms'
import { Button } from '../ui/button'
import { Card, CardTitle } from '../ui/card'
import type { useNewPick } from './useNewPick'

export function FinalActions({
  refusal,
  classId,
  pick,
  reload,
}: {
  refusal: Refusal
  /** Null in the class workspace, which is already the class. */
  classId: string | null
  pick: ReturnType<typeof useNewPick> | null
  reload: () => void
}) {
  const t = useT()
  return (
    <div className="flex flex-wrap gap-3">
      {refusal.newSongs === true && pick !== null ? <PickAgain pick={pick} /> : null}
      <Button variant="secondary" onClick={reload}>
        {t('songs.approval.reload')}
      </Button>
      {classId !== null ? (
        <Button asChild variant="ghost">
          <Link to={`/classes/${classId}`}>{t('songs.approval.openClass')}</Link>
        </Button>
      ) : null}
    </div>
  )
}

export function RefreshButton({ onClick }: { onClick: () => void }) {
  const t = useT()
  return <Button onClick={onClick}>{t('songs.approval.refresh')}</Button>
}

/** `h1` on the job page, `h2` inside the class workspace. */
export function Heading({ as: Tag, ...props }: { as: 'h1' | 'h2'; id: string; className: string; children: ReactNode }) {
  return <Tag {...props} />
}

export function PickAgain({ pick }: { pick: ReturnType<typeof useNewPick> }) {
  const t = useT()
  return (
    <Button
      disabled={pick.action.busy}
      onClick={() => {
        pick.start(null, '')
      }}
    >
      {t('songs.ended.pickAgain')}
    </Button>
  )
}

export function Ended({
  title,
  classId,
  children,
  extra = null,
  error = null,
}: {
  title: string
  classId: string | null
  children: ReactNode
  extra?: ReactNode
  error?: string | null
}) {
  const t = useT()
  return (
    <Card>
      <CardTitle>{title}</CardTitle>
      <div className="flex flex-col gap-3">
        <p className="m-0 text-text">{children}</p>
        {error ? <Notice kind="error">{error}</Notice> : null}
        <div className="flex flex-wrap gap-3">
          {extra}
          {classId !== null ? (
            <Button asChild variant="ghost">
              <Link to={`/classes/${classId}`}>{t('songs.approval.openClass')}</Link>
            </Button>
          ) : null}
        </div>
      </div>
    </Card>
  )
}
