import { useEffect } from 'react'
import { failureText } from '../../api/jobMessages'
import type { Job } from '../../api/jobs'
import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { Card, CardTitle } from '../ui/card'
import { rememberOutputLimit } from './studioAction'

/**
 * A `produce_outputs` job that failed. Its words come from the category; the way on is the class
 * page, never "Design again" (that would start a new design). A failure for the limit is
 * remembered, so the class page stops offering "approve again" for the day.
 */
export function OutputsEnded({ job }: { job: Job }) {
  const t = useT()
  const text = failureText(job.errorCategory, job.kind)
  useEffect(() => {
    if (job.errorCategory === 'output_limit') rememberOutputLimit(job.classId)
  }, [job.errorCategory, job.classId])
  return (
    <Card data-testid="outputs-ended">
      <CardTitle>{text.title}</CardTitle>
      <p className="m-0 text-text">{text.detail}</p>
      <p className="m-0">
        <Link className="button" to={`/classes/${encodeURIComponent(job.classId)}`}>
          {t('studio.ended.backToClass')}
        </Link>
      </p>
    </Card>
  )
}
