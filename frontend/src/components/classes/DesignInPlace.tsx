import { useEffect, useRef } from 'react'
import { isSongKind } from '../../api/songs'
import { useJobProgress } from '../../hooks/useJobProgress'
import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { Notice } from '../forms'
import { Designing } from '../jobs/Designing'
import { SongsForApproval } from '../songs/SongsForApproval'

/**
 * The designing state of a class with a design job and no version yet (MA3): the job's steps and
 * Stop, drawn in the class panel instead of sending the instructor to the job page. When the job
 * completes the class is read again and the new version takes this place. A job that ends any other
 * way says so here and points at the job page, which offers a new design.
 *
 * A song job (MA4) is shown the same way, and when it waits for approval the songs are listed here
 * with the existing Approve and change requests (`SongsForApproval`, one column), so the instructor
 * does not leave the class. `onChanged` reads the class again (a new pick was started).
 */
export function DesignInPlace({
  jobId,
  onCompleted,
  onChanged,
}: {
  jobId: string
  onCompleted: () => void
  onChanged: () => void
}) {
  const t = useT()
  const { job, progress, problem, setJob, follow } = useJobProgress(jobId)
  const completed = job?.state === 'completed'
  const told = useRef(false)
  useEffect(() => {
    if (completed && !told.current) {
      told.current = true
      onCompleted()
    }
  }, [completed, onCompleted])

  if (job === null) {
    return <Notice>{problem === 'failed' ? t('job.moment.unreachable') : t('job.moment.loading')}</Notice>
  }
  if (job.state === 'queued' || job.state === 'running') {
    return <Designing job={job} progress={progress} onJob={setJob} embedded />
  }
  if (completed) return <Notice>{t('ws.opening')}</Notice>
  const songs = isSongKind(job.kind)
  if (songs && job.state === 'waiting_for_approval') {
    return (
      <SongsForApproval
        job={job}
        embedded={{ onChanged }}
        onApproved={(next) => {
          setJob(next)
          follow()
        }}
      />
    )
  }
  return (
    <Notice kind="error">
      {songs ? t('ws.songsStopped') : t('ws.designStopped')} <Link to={`/jobs/${jobId}`}>{t('ws.seeJob')}</Link>
    </Notice>
  )
}
