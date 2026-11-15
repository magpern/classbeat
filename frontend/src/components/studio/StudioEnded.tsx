import { failureText } from '../../api/jobMessages'
import type { Job } from '../../api/jobs'
import { useT } from '../../i18n/I18n'
import { startAgainPath } from '../../lib/startRequest'
import { Link } from '../../router/Router'
import { Card, CardTitle } from '../ui/card'
import { recallStudioAction } from './studioAction'

/**
 * A studio job that failed. After an action on a list (undo, another, Swap anyway) the waiting
 * list was cancelled when the action started, so nothing is left to approve: say so, and offer a
 * new pick, which costs a class credit (ADR 0036 section 3.3; the C3 review).
 */
export function StudioEnded({ job }: { job: Job }) {
  const t = useT()
  const text = failureText(job.errorCategory, job.kind)
  const changed = recallStudioAction(job.id) !== null
  return (
    <Card>
      <CardTitle>{text.title}</CardTitle>
      <p className="m-0 text-text">{text.detail}</p>
      {changed ? (
        <>
          <p className="m-0 text-text" data-testid="studio-no-list">{t('studio.ended.noList')}</p>
          <p className="m-0 text-text" data-testid="studio-new-pick">{t('studio.ended.newPick')}</p>
        </>
      ) : null}
      <p className="m-0">
        <Link className="button" to={startAgainPath(job.classId)}>
          {t('studio.newPick')}
        </Link>
      </p>
    </Card>
  )
}
