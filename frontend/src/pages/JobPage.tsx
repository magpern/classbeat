import { useEffect, useState } from 'react'
import { useT } from '../i18n/I18n'
import { isSongKind } from '../api/songs'
import { SongEnded } from '../components/songs/SongEnded'
import { SongsForApproval } from '../components/songs/SongsForApproval'
import {
  getDraftSummary,
  type DraftSummary,
  type Job,
} from '../api/jobs'
import { ApiError } from '../api/client'
import { Card, Notice } from '../components/forms'
import { Designing } from '../components/jobs/Designing'
import { DraftSummaryView } from '../components/classes/DraftSummaryView'
import { RequireAuth } from '../components/RequireAuth'
import { useJobProgress, type Progress } from '../hooks/useJobProgress'
import { forgetInput } from '../lib/submittedInput'
import { Link } from '../router/Router'
import { Ended, Failed } from '../components/jobs/JobEnded'

export function JobPage({ jobId }: { jobId: string }) {
  return (
    <RequireAuth>
      <JobView jobId={jobId} />
    </RequireAuth>
  )
}

function JobView({ jobId }: { jobId: string }) {
  const t = useT()
  const { job, progress, problem, setJob, follow } = useJobProgress(jobId)
  if (problem === 'not_found') {
    return (
      <Card title={t('job.notFound.title')}>
        <p>{t('job.notFound.text')}</p>
        <p>
          <Link to="/classes/new">{t('job.notFound.designLink')}</Link>
        </p>
      </Card>
    )
  }
  if (problem === 'signed_out') {
    return (
      <Card title={t('job.signedOut.title')}>
        <p>{t('job.signedOut.text')}</p>
        <p>
          <Link className="button" to={`/signin?return_to=${encodeURIComponent(`/jobs/${jobId}`)}`}>
            {t('job.signedOut.signIn')}
          </Link>
        </p>
      </Card>
    )
  }
  if (job === null) {
    return (
      <Card title={t('job.moment.title')}>
        {problem === 'failed' ? (
          <Notice kind="error">{t('job.moment.unreachable')}</Notice>
        ) : (
          <p>{t('job.moment.loading')}</p>
        )}
      </Card>
    )
  }
  // The song list is two columns (the songs, the action bar) and uses the page's full width.
  const wide = isSongKind(job.kind) && job.state === 'waiting_for_approval'
  return (
    <div className={wide ? 'mx-auto flex w-full max-w-5xl flex-col gap-5' : 'stack'}>
      {problem === 'failed' ? (
        <Notice kind="error">{t('job.dropped')}</Notice>
      ) : null}
      <JobBody
        job={job}
        progress={progress}
        onJob={setJob}
        onApproved={(next) => {
          setJob(next)
          follow()
        }}
      />
    </div>
  )
}

function JobBody({
  job,
  progress,
  onJob,
  onApproved,
}: {
  job: Job
  progress: Progress
  onJob: (job: Job) => void
  onApproved: (job: Job) => void
}) {
  const t = useT()
  const song = isSongKind(job.kind)
  if (song && job.state === 'waiting_for_approval') {
    return <SongsForApproval job={job} onApproved={onApproved} />
  }
  if (song && (job.state === 'failed' || job.state === 'cancelled' || job.state === 'expired')) {
    return <SongEnded job={job} />
  }
  switch (job.state) {
    case 'queued':
    case 'running':
      return <Designing job={job} progress={progress} onJob={onJob} />
    case 'completed':
      return <Completed job={job} />
    case 'failed':
      return <Failed job={job} />
    case 'cancelled':
      return <Ended job={job} title={t('job.cancelled.title')} detail={t('job.cancelled.detail')} />
    case 'expired':
      return <Ended job={job} title={t('job.expired.title')} detail={t('job.expired.detail')} />
    default:
      return (
        <Card title={t('job.waitingForYou.title')}>
          <p>{t('job.waitingForYou.text')}</p>
        </Card>
      )
  }
}

function Completed({ job }: { job: Job }) {
  const t = useT()
  const [draft, setDraft] = useState<DraftSummary | null>(null)
  const [state, setState] = useState<'loading' | 'none' | 'failed' | 'ready'>('loading')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    forgetInput(job.id) // the design is done: the remembered form is not needed any more
  }, [job.id])

  useEffect(() => {
    let current = true
    getDraftSummary(job.id).then(
      (found) => {
        if (!current) return
        setDraft(found)
        setState('ready')
      },
      (error: unknown) => {
        if (!current) return
        setState(error instanceof ApiError && error.status === 409 ? 'none' : 'failed')
      },
    )
    return () => {
      current = false
    }
  }, [job.id, attempt])

  return (
    <div className="stack">
      <h1>{t('job.ready.title')}</h1>
      {state === 'loading' ? <p>{t('job.ready.loadingDraft')}</p> : null}
      {state === 'none' ? <Notice>{t('job.ready.noDraft')}</Notice> : null}
      {state === 'failed' ? (
        <Notice kind="error">
          {t('job.ready.draftFailed')}{' '}
          <button
            type="button"
            className="button button--link"
            onClick={() => {
              setState('loading')
              setAttempt((n) => n + 1)
            }}
          >
            {t('common.tryAgain')}
          </button>
        </Notice>
      ) : null}
      {state === 'ready' && draft !== null ? <DraftSummaryView draft={draft} /> : null}
      <p>
        <Link className="button" to={`/classes/${job.classId}`}>
          {t('job.ready.openClass')}
        </Link>
        <Link className="button button--secondary" to="/classes/new">
          {t('job.ready.designAnother')}
        </Link>
      </p>
    </div>
  )
}
