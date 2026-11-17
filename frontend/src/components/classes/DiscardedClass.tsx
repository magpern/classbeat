import { useEffect, useState } from 'react'
import { getJob, type Job } from '../../api/jobs'
import { failureText } from '../../api/jobMessages'
import { useT } from '../../i18n/I18n'
import { recallRequest, startAgainPath } from '../../lib/startRequest'
import { Link } from '../../router/Router'
import { Card, Notice } from '../forms'

/**
 * A class whose first creation failed or was stopped before it made anything (ADR 0038): the server
 * removed the empty class and kept the ended job. The page says why it stopped, from that job, and
 * offers the start screen with the request this tab kept for the class.
 */
export function DiscardedClass({ classId, jobId }: { classId: string; jobId: string }) {
  const t = useT()
  const [job, setJob] = useState<Job | null>(null)
  useEffect(() => {
    const abort = new AbortController()
    // Without the job the page still says the class is gone and offers the start screen.
    getJob(jobId, abort.signal).then(setJob, () => undefined)
    return () => {
      abort.abort()
    }
  }, [jobId])
  const kept = recallRequest(classId) !== null
  const why = job === null ? null : job.state === 'failed' ? failureText(job.errorCategory, job.kind) : null
  return (
    <Card title={t('class.page.discardedTitle')}>
      {why !== null ? (
        <Notice kind="error">
          {why.title} {why.detail}
        </Notice>
      ) : null}
      {job?.state === 'cancelled' ? <Notice>{t('class.page.discardedStopped')}</Notice> : null}
      <p>{t('class.page.discarded')}</p>
      <p className="flex flex-wrap gap-3">
        {kept ? (
          <Link className="button" to={startAgainPath(classId)}>
            {t('start.handoff.tryAgain')}
          </Link>
        ) : (
          <Link className="button" to="/classes/new">
            {t('class.list.design')}
          </Link>
        )}
        <Link to="/classes">{t('class.list.title')}</Link>
      </p>
    </Card>
  )
}
