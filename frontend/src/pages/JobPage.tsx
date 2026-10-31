import { useCallback, useEffect, useState } from 'react'
import { isSongKind } from '../api/songs'
import { PICK_STAGES } from '../api/songMessages'
import { SongEnded } from '../components/songs/SongEnded'
import { SongsForApproval } from '../components/songs/SongsForApproval'
import { failureText, retryLabel, stageLabel } from '../api/jobMessages'
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
import { DraftSummaryView } from '../components/classes/DraftSummaryView'
import { UncertainAttach } from '../components/classes/UncertainAttach'
import { RequireAuth } from '../components/RequireAuth'
import { useJobProgress, type Progress } from '../hooks/useJobProgress'
import { designAgainKey, forgetInput, recallInput, rememberInput } from '../lib/submittedInput'
import { Link, useRouter } from '../router/Router'

export function JobPage({ jobId }: { jobId: string }) {
  return (
    <RequireAuth>
      <JobView jobId={jobId} />
    </RequireAuth>
  )
}

function JobView({ jobId }: { jobId: string }) {
  const { job, progress, problem, setJob, follow } = useJobProgress(jobId)
  if (problem === 'not_found') {
    return (
      <Card title="Class not found">
        <p>This design does not exist, or it is not yours.</p>
        <p>
          <Link to="/classes/new">Design a class</Link>
        </p>
      </Card>
    )
  }
  if (problem === 'signed_out') {
    return (
      <Card title="You are signed out">
        <p>Sign in again to see this design. It carries on meanwhile.</p>
        <p>
          <Link className="button" to={`/signin?return_to=${encodeURIComponent(`/jobs/${jobId}`)}`}>
            Sign in
          </Link>
        </p>
      </Card>
    )
  }
  if (job === null) {
    return (
      <Card title="One moment">
        {problem === 'failed' ? (
          <Notice kind="error">The server could not be reached. Trying again.</Notice>
        ) : (
          <p>Loading</p>
        )}
      </Card>
    )
  }
  return (
    <div className="stack">
      {problem === 'failed' ? (
        <Notice kind="error">The connection to the server dropped. Trying again.</Notice>
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
      return <Ended job={job} title="You stopped this design" detail="Nothing was saved." />
    case 'expired':
      return <Ended job={job} title="This design expired" detail="Start a new one." />
    default:
      return (
        <Card title="Waiting for you">
          <p>This design is waiting for an answer from you.</p>
        </Card>
      )
  }
}

function cancelError(error: unknown): string {
  // 409: the design ended on its own meanwhile; the page switches to the result by itself.
  if (error instanceof ApiError && error.status === 409) return 'This design has just finished.'
  return 'That did not work. Try again.'
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
  const action = useAction()
  const stopping = job.cancelRequested || progress.cancelRequested
  const stop = () => {
    void action.run(async () => {
      onJob(await cancelJob(job.id))
    }, cancelError)
  }
  const waiting = job.state === 'queued' && progress.stage === null
  const picking = job.kind === 'design_with_songs' && job.approvedAt === null
  return (
    <section className="panel" aria-labelledby="designing-title">
      <h1 id="designing-title" className="panel__title">
        {picking ? 'Picking your songs…' : 'Designing your class…'}
      </h1>
      {picking ? <PickStages current={progress.stage ?? job.stage} /> : null}
      <div role="status" aria-live="polite" data-testid="progress">
        {stopping ? (
          <p>Stopping. This takes a moment.</p>
        ) : progress.retry !== null ? (
          <p>{retryLabel(progress.retry)}</p>
        ) : waiting ? (
          <p>Waiting for a free slot.</p>
        ) : (
          <p>{stageLabel(progress.stage ?? job.stage)}</p>
        )}
        {progress.operation === 'pending' ? <p>Adding your playlist to your MOWL music.</p> : null}
        {progress.toolCalls > 0 ? (
          <p className="muted">The designer has checked its draft {progress.toolCalls} time(s).</p>
        ) : null}
      </div>
      <p className="muted">You can leave this page. The design carries on and this address shows it.</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <p>
        <button
          type="button"
          className="button button--secondary"
          disabled={action.busy || stopping}
          onClick={stop}
        >
          Stop designing
        </button>
      </p>
    </section>
  )
}

/** The stages of picking songs in words, with the running one marked (ADR 0008 section 9). */
function PickStages({ current }: { current: string | null }) {
  const at = PICK_STAGES.findIndex((s) => s.stage === current)
  return (
    <ol className="m-0 flex list-none flex-col gap-1 p-0 text-muted" aria-label="Steps">
      {PICK_STAGES.map((s, i) => (
        <li
          key={s.stage}
          aria-current={i === at ? 'step' : undefined}
          className={i === at ? 'font-medium text-text' : undefined}
        >
          {s.label}
          {i < at ? ' (done)' : ''}
        </li>
      ))}
    </ol>
  )
}

function Completed({ job }: { job: Job }) {
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
      <h1>Your class is ready</h1>
      {state === 'loading' ? <p>Loading the draft</p> : null}
      {state === 'none' ? <Notice>This design finished without a draft.</Notice> : null}
      {state === 'failed' ? (
        <Notice kind="error">
          The draft could not be loaded.{' '}
          <button
            type="button"
            className="button button--link"
            onClick={() => {
              setState('loading')
              setAttempt((n) => n + 1)
            }}
          >
            Try again
          </button>
        </Notice>
      ) : null}
      {state === 'ready' && draft !== null ? <DraftSummaryView draft={draft} /> : null}
      <p>
        <Link className="button" to={`/classes/${job.classId}`}>
          Open the class
        </Link>
        <Link className="button button--secondary" to="/classes/new">
          Design another class
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
    return 'MOWL is not connected, or was connected again since this design started. Connect it on your account page if needed, then start a new design.'
  }
  return againMessage(error)
}

function againMessage(error: unknown): string {
  if (error instanceof ApiError && error.reason === 'class_busy') {
    return 'This class already has a design running, perhaps from another tab. Wait for it to finish, then try again.'
  }
  if (error instanceof ApiError && error.status === 429) {
    return 'You already have designs running. Wait for one to finish.'
  }
  return 'That did not work. Try again.'
}

function Failed({ job }: { job: Job }) {
  if (job.errorCategory === 'uncertain_write') return <FailedUncertain job={job} />
  const text = failureText(job.errorCategory)
  return <Ended job={job} title={text.title} detail={text.detail} account={text.account === true} />
}

function FailedUncertain({ job }: { job: Job }) {
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
            {remembered ? 'Design again' : 'Start a new design'}
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
}: {
  job: Job
  title: string
  detail: string
  account?: boolean
}) {
  const { again, action, remembered } = useDesignAgain(job)
  return (
    <section className="panel" aria-labelledby="ended-title">
      <h1 id="ended-title" className="panel__title">
        {title}
      </h1>
      <p>{detail}</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <p>
        {account ? (
          <Link className="button" to="/account">
            Go to your account
          </Link>
        ) : null}
        <button
          type="button"
          className={account ? 'button button--secondary' : 'button'}
          disabled={action.busy}
          onClick={again}
        >
          {remembered ? 'Design again' : 'Start a new design'}
        </button>
      </p>
    </section>
  )
}
