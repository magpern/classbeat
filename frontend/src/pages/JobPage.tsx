import { useCallback, useEffect, useState } from 'react'
import { planRefusalText } from '../api/planMessages'
import { sentenceText } from '../api/sentences'
import { useT } from '../i18n/I18n'
import { t as translate } from '../i18n/t'
import { isSongKind } from '../api/songs'
import { SongEnded } from '../components/songs/SongEnded'
import { SongsForApproval } from '../components/songs/SongsForApproval'
import {
  designSteps,
  failureText,
  mixStageLabel,
  retryLabel,
  stageLabel,
  waitingForSlot,
  waitingToStart,
  youCanLeave,
} from '../api/jobMessages'
import {
  cancelJob,
  createDesignJob,
  getDraftSummary,
  type DesignInput,
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
import { UncertainAttach } from '../components/classes/UncertainAttach'
import { RequireAuth } from '../components/RequireAuth'
import { useJobProgress, type Progress } from '../hooks/useJobProgress'
import { designAgainKey, forgetInput, recallInput, recallPick, rememberInput } from '../lib/submittedInput'
import { Link, useRouter } from '../router/Router'

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
  return (
    <div className="stack">
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

/** Starts the same design again as a new job of the same class, from the remembered form. */
function useDesignAgain(job: Job) {
  const { navigate } = useRouter()
  const action = useAction()
  const input = recallInput(job.id)
  const again = () => {
    if (input === null) {
      navigate('/classes/new')
      return
    }
    void action.run(async () => {
      const next: DesignInput = {
        class_id: job.classId,
        brief: input.brief,
        music: input.music,
        mowl_connection_id: input.mowl_connection_id,
      }
      let created: Job
      try {
        created = await createDesignJob(next, designAgainKey(job.id))
      } catch (error) {
        // The old connection id is gone for good (MOWL was connected again): drop the remembered
        // form so the page offers the form, which picks the current connection.
        if (error instanceof ApiError && error.reason === 'not_connected') forgetInput(job.id)
        throw error
      }
      rememberInput(created.id, next)
      forgetInput(job.id)
      navigate(`/jobs/${created.id}`)
    }, againError)
  }
  return { again, action, remembered: input !== null }
}

/**
 * The words for a failed Design again. `not_connected` also happens when MOWL was disconnected
 * and connected again since: the remembered form names the old connection, which is gone for
 * good (see `useDesignAgain`, which then drops it).
 */
function againError(error: unknown): string {
  if (error instanceof ApiError && error.reason === 'not_connected') {
    return translate('job.again.notConnected')
  }
  return againMessage(error)
}

function againMessage(error: unknown): string {
  const refusal = planRefusalText(error)
  if (refusal !== null) return refusal
  if (error instanceof ApiError && error.reason === 'class_busy') {
    return translate('job.again.classBusy')
  }
  if (error instanceof ApiError && error.status === 429) {
    return translate('job.again.tooMany')
  }
  return translate('common.didNotWork')
}

function Failed({ job }: { job: Job }) {
  const t = useT()
  if (job.errorCategory === 'uncertain_write') return <FailedUncertain job={job} />
  // A failed Swedebeat match: the server's sentence says what stopped it. The generic MOWL text for
  // the category ("wait a few minutes and try again") would promise a retry that does not happen.
  // No button either: "Try again" would start a new design, not retry the match.
  if (job.kind === 'swedebeat_match' && job.errorMessage !== null) {
    const detail = sentenceText(t, job.sentence, job.errorMessage) ?? job.errorMessage
    return <Ended job={job} title={t('job.matchStopped')} detail={detail} retry={false} />
  }
  const kept = recallInput(job.id)
  const feature =
    kept?.brief.sport === 'running'
      ? 'running'
      : kept?.music.source === 'mowl_swedebeat_mix'
        ? 'swedebeat'
        : kept?.music.source === 'none'
          ? 'soundless'
          : null
  const text = failureText(job.errorCategory, '', feature)
  return <Ended job={job} title={text.title} detail={text.detail} account={text.account === true} />
}

function FailedUncertain({ job }: { job: Job }) {
  const t = useT()
  const [resolved, setResolved] = useState(false)
  const onResolved = useCallback(() => {
    setResolved(true)
  }, [])
  const { again, action, remembered } = useDesignAgain(job)
  return (
    <div className="stack">
      <UncertainAttach jobId={job.id} onResolved={onResolved} />
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {resolved ? (
        <p>
          <button type="button" className="button" disabled={action.busy} onClick={again}>
            {remembered ? t('job.again.design') : t('job.again.startNew')}
          </button>
        </p>
      ) : null}
    </div>
  )
}

function Ended({
  job,
  title,
  detail,
  account = false,
  retry = true,
}: {
  job: Job
  title: string
  detail: string
  account?: boolean
  retry?: boolean
}) {
  const t = useT()
  const { again, action, remembered } = useDesignAgain(job)
  return (
    <Panel className="mx-auto flex w-full max-w-2xl flex-col gap-4" role="region" aria-labelledby="ended-title">
      <h1 id="ended-title" className="m-0 font-display text-3xl font-extrabold">
        {title}
      </h1>
      <p className="m-0 text-muted">{detail}</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <p className="m-0 flex flex-wrap gap-3">
        {account ? (
          <Button asChild>
            <Link to="/account">{t('job.goToAccount')}</Link>
          </Button>
        ) : null}
        {retry ? (
          <Button variant={account ? 'secondary' : 'primary'} disabled={action.busy} onClick={again}>
            {remembered
              ? job.state === 'failed'
                ? t('job.again.tryAgain')
                : t('job.again.design')
              : t('job.again.startNew')}
          </Button>
        ) : null}
      </p>
    </Panel>
  )
}
