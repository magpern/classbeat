import { useEffect, useState } from 'react'
import { useT } from '../i18n/I18n'
import { t as translate } from '../i18n/t'
import { isSongKind } from '../api/songs'
import { SongEnded } from '../components/songs/SongEnded'
import { SongsForApproval } from '../components/songs/SongsForApproval'
import {
  designSteps,
  mixStageLabel,
  retryLabel,
  stageLabel,
  waitingForSlot,
  waitingToStart,
  youCanLeave,
} from '../api/jobMessages'
import {
  cancelJob,
  getDraftSummary,
  type DraftSummary,
  type Job,
} from '../api/jobs'
import { ApiError } from '../api/client'
import { Card, Notice, useAction } from '../components/forms'
import { DesigningSteps, stepNumber } from '../components/jobs/DesigningSteps'
import { GhostProfile } from '../components/jobs/GhostProfile'
import { Button } from '../components/ui/button'
import { Card as Panel } from '../components/ui/card'
import { DraftSummaryView } from '../components/classes/DraftSummaryView'
import { RequireAuth } from '../components/RequireAuth'
import { useJobProgress, type Progress } from '../hooks/useJobProgress'
import { forgetInput, recallInput, recallPick } from '../lib/submittedInput'
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

function cancelError(error: unknown): string {
  // 409: the design ended on its own meanwhile; the page switches to the result by itself.
  if (error instanceof ApiError && error.status === 409) return translate('job.cancelError.finished')
  return translate('common.didNotWork')
}

function Designing({
  job,
  progress,
  onJob,
}: {
  job: Job
  progress: Progress
  onJob: (job: Job) => void
}) {
  const t = useT()
  const action = useAction()
  const stopping = job.cancelRequested || progress.cancelRequested
  const stop = () => {
    void action.run(async () => {
      onJob(await cancelJob(job.id))
    }, cancelError)
  }
  const waiting = job.state === 'queued' && progress.stage === null
  const picking = isSongKind(job.kind) && job.approvedAt === null
  // The class name comes from the form this browser remembered; without it the title says "your class".
  const name = recallInput(job.id)?.title ?? recallPick(job.id)?.title ?? t('job.yourClass')
  const source = recallInput(job.id)?.music.source
  const steps = designSteps(job.kind, job.approvedAt !== null, source === 'none')
  const current = progress.stage ?? job.stage
  // Only this browser remembers the form; elsewhere the stage keeps its general words.
  const mixStage = source === 'mowl_swedebeat_mix' && current === 'music' && job.kind === 'design_draft'
  const step = stepNumber(steps, current)
  return (
    <Panel className="mx-auto flex w-full max-w-2xl flex-col gap-5" role="region" aria-labelledby="designing-title">
      <div className="flex flex-col gap-1">
        <h1 id="designing-title" className="m-0 font-display text-3xl font-extrabold">
          {picking ? t('job.findingSongs', { name }) : t('job.designing', { name })}
        </h1>
        {step !== null ? (
          <p className="m-0 text-muted tabular-nums">
            {t('job.stepOf', { step, total: steps.length })}
          </p>
        ) : null}
      </div>
      <GhostProfile />
      <DesigningSteps steps={steps} current={current} />
      <div role="status" aria-live="polite" data-testid="progress" className="flex flex-col gap-1">
        {stopping ? (
          <p className="m-0">{t('job.stopping')}</p>
        ) : progress.retry !== null ? (
          <p className="m-0">{retryLabel(progress.retry)}</p>
        ) : progress.waitingForSlot && job.state === 'queued' ? (
          <p className="m-0">{waitingForSlot()}</p>
        ) : waiting ? (
          <p className="m-0">{waitingToStart()}</p>
        ) : mixStage ? (
          <p className="m-0">{mixStageLabel()}</p>
        ) : (
          <p className="m-0">{stageLabel(current)}</p>
        )}
        {progress.operation === 'pending' ? <p className="m-0">{t('job.addingPlaylist')}</p> : null}
        {progress.toolCalls > 0 ? (
          <p className="m-0 text-muted">{t('job.toolCalls', { count: progress.toolCalls })}</p>
        ) : null}
      </div>
      <p className="m-0 text-muted">{youCanLeave()}</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <p className="m-0">
        <Button variant="secondary" disabled={action.busy || stopping} onClick={stop}>
          {t('job.stop')}
        </Button>
      </p>
    </Panel>
  )
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
