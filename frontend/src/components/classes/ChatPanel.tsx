import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ApiError } from '../../api/client'
import { classReasonText, describeChange, isStale } from '../../api/classMessages'
import type { ClassMessage, ClassSummary, RevisionSummary } from '../../api/classes'
import { getConnections } from '../../api/connections'
import {
  retryLabel,
  reviseFailureText,
  stageLabel,
  USE_CHANGE_SONGS,
  USE_FINISH_SONGS,
  USE_NEW_CLASS_FOR_SONGS,
} from '../../api/jobMessages'
import { cancelJob, createReviseJob, getJob, isTerminal, type Job } from '../../api/jobs'
import { useJobProgress } from '../../hooks/useJobProgress'
import { newIdempotencyKey } from '../../lib/submittedInput'
import { Link } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { CHANGE_SONGS_ID } from '../songs/ChangeSongs'
import { Button } from '../ui/button'
import { UncertainAttach } from './UncertainAttach'

/** Where the class's songs stand, for the answer to a request that only asks for other songs. */
type SongsWay = 'change' | 'own' | 'pending'

export const MAX_REQUEST_CHARS = 2000

/** Characters the way the backend counts them (code points, not UTF-16 units). */
function length(value: string): number {
  return Array.from(value).length
}

/** The live progress of the class's running change, from the job's events. */
function ReviseRun({ jobId, onFinished }: { jobId: string; onFinished: () => void }) {
  const { job, progress, problem, setJob } = useJobProgress(jobId)
  const action = useAction()
  const over = job !== null && isTerminal(job.state)
  const finished = useRef(false)
  useEffect(() => {
    if (over && !finished.current) {
      finished.current = true
      onFinished()
    }
  }, [over, onFinished])

  if (problem === 'not_found') return <Notice>This change is no longer there.</Notice>
  const stopping = job?.cancelRequested === true || progress.cancelRequested
  const stop = () => {
    void action.run(async () => {
      setJob(await cancelJob(jobId))
    })
  }
  return (
    <div className="chat__run" role="status" aria-live="polite" data-testid="revise-progress">
      {problem === 'failed' ? <p>The connection to the server dropped. Trying again.</p> : null}
      {stopping ? (
        <p>Stopping. This takes a moment.</p>
      ) : progress.retry !== null ? (
        <p>{retryLabel(progress.retry)}</p>
      ) : job === null || (job.state === 'queued' && progress.stage === null) ? (
        <p>Waiting for a free slot.</p>
      ) : (
        <p>{stageLabel(progress.stage ?? job.stage)}…</p>
      )}
      {progress.operation === 'pending' ? <p>Adding your playlist to your MOWL music.</p> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <button
        type="button"
        className="button button--secondary"
        disabled={action.busy || stopping || over}
        onClick={stop}
      >
        Stop changing
      </button>
    </div>
  )
}

/** What became of a request that got no answer: the job's failure, in plain words. */
function Unanswered({
  message,
  latest,
  songs,
}: {
  message: ClassMessage
  latest: boolean
  /** Where the class's songs stand: Change songs is there, a playlist of the user's own, or a pick not done. */
  songs: SongsWay
}) {
  const canChangeSongs = songs === 'change'
  const [job, setJob] = useState<Job | null>(null)
  const [failed, setFailed] = useState(false)
  const [resolved, setResolved] = useState(false)
  const jobId = message.jobId
  useEffect(() => {
    if (!latest || jobId === null) return
    let current = true
    getJob(jobId).then(
      (found) => {
        if (current) setJob(found)
      },
      () => {
        if (current) setFailed(true)
      },
    )
    return () => {
      current = false
    }
  }, [latest, jobId])

  if (!latest || jobId === null || failed) {
    return <p className="chat__outcome muted">This request did not finish. Nothing changed.</p>
  }
  if (job === null) return <p className="chat__outcome muted">Checking what happened</p>
  if (job.state === 'failed' && job.errorCategory === 'uncertain_write') {
    return (
      <div className="chat__outcome">
        <UncertainAttach
          jobId={job.id}
          onResolved={() => {
            setResolved(true)
          }}
        />
        {resolved ? <p>You can ask for the change again now.</p> : null}
      </div>
    )
  }
  if (job.state === 'failed' && job.errorCategory === 'use_change_songs') {
    return (
      <div className="chat__outcome" role="status" data-testid="use-change-songs">
        <p>{songs === 'change' ? USE_CHANGE_SONGS : songs === 'own' ? USE_NEW_CLASS_FOR_SONGS : USE_FINISH_SONGS}</p>
        {canChangeSongs ? (
          <Button
            variant="secondary"
            onClick={() => {
              const panel = document.getElementById(CHANGE_SONGS_ID)
              panel?.scrollIntoView({ block: 'start' })
              panel?.focus()
            }}
          >
            Change songs
          </Button>
        ) : null}
      </div>
    )
  }
  if (job.state === 'failed') {
    const sentence = reviseFailureText(job.errorCategory)
    return (
      <p className="chat__outcome" role="alert">
        {sentence}{' '}
        {job.errorCategory === 'not_connected' ||
        job.errorCategory === 'needs_reconnect' ||
        job.errorCategory === 'key_unavailable' ||
        job.errorCategory === 'decrypt_failed' ? (
          <Link to="/account">Go to your account</Link>
        ) : null}
      </p>
    )
  }
  if (job.state === 'cancelled' || job.state === 'expired') {
    return <p className="chat__outcome muted">This change was stopped. Nothing changed.</p>
  }
  return <p className="chat__outcome muted">This request did not finish. Nothing changed.</p>
}

function History({
  messages,
  numbers,
  openJobId,
  onView,
  songs,
}: {
  messages: ClassMessage[]
  numbers: ReadonlyMap<string, number>
  openJobId: string | null
  onView: (revisionId: string) => void
  songs: SongsWay
}) {
  const answered = new Set(
    messages.filter((m) => m.role === 'assistant' && m.jobId !== null).map((m) => m.jobId),
  )
  const unanswered = messages.filter(
    (m) => m.role === 'user' && m.jobId !== null && !answered.has(m.jobId) && m.jobId !== openJobId,
  )
  const latestUnanswered = unanswered.at(-1)?.seq
  if (messages.length === 0) {
    return <p className="muted">Nothing asked yet. Say what to change, and classbeat changes this class.</p>
  }
  return (
    <ol className="chat__history" aria-label="Your requests">
      {messages.map((m) => {
        if (m.role === 'user') {
          return (
            <li key={m.seq} className="chat__message chat__message--user">
              <p className="chat__who">You asked</p>
              <p className="chat__text">{m.body}</p>
              {m.jobId !== null && !answered.has(m.jobId) && m.jobId !== openJobId ? (
                <Unanswered
                  message={m}
                  latest={m.seq === latestUnanswered}
                  songs={songs}
                />
              ) : null}
            </li>
          )
        }
        const lines = m.change === null ? [] : describeChange(m.change)
        const number = m.resultRevisionId === null ? undefined : numbers.get(m.resultRevisionId)
        return (
          <li key={m.seq} className="chat__message chat__message--answer">
            <p className="chat__who">
              classbeat changed it
              {number === undefined ? '' : `, version ${String(number)}`}
            </p>
            {lines.length > 0 ? (
              <ul className="chat__change">
                {lines.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            ) : null}
            {m.change?.musicChanged === true ? (
              <p className="chat__text">The music changed: the playlist in MOWL is not the one before.</p>
            ) : null}
            {m.body ? (
              <p className="chat__text">
                <span className="muted">Note from the designer: </span>
                {m.body}
              </p>
            ) : null}
            {m.resultRevisionId !== null && number !== undefined ? (
              <button
                type="button"
                className="button button--link"
                onClick={() => {
                  onView(m.resultRevisionId!)
                }}
              >
                View version {number}
              </button>
            ) : null}
          </li>
        )
      })}
    </ol>
  )
}

/**
 * The Change it chat (ADR 0007 section 3): the history of requests with the backend's change
 * summaries and the designer's notes as plain text, the box, and the live progress of the
 * running change. The request names the revision the page shows as its base; the server refuses
 * with 409 `stale_base` when the class moved on, and then the page shows the reason and reloads.
 */
export function ChatPanel({
  summary,
  revisions,
  messages,
  onReload,
  onView,
}: {
  summary: ClassSummary
  revisions: RevisionSummary[]
  messages: ClassMessage[]
  onReload: () => void
  onView: (revisionId: string) => void
}) {
  const [request, setRequest] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const action = useAction()
  // One key per user action: the same request on the same base keeps it, so a repeated click or
  // a retry after a lost answer returns the same job instead of a second one.
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null)
  const numbers = new Map(revisions.map((r) => [r.id, r.number]))
  const running = summary.openJob
  const revising = running?.kind === 'revise_draft' && !isTerminal(running.state)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const text = request.trim()
    let invalid: string | null = null
    if (text === '') invalid = 'Write what you want changed.'
    else if (length(text) > MAX_REQUEST_CHARS) {
      invalid = `A request is up to ${String(MAX_REQUEST_CHARS)} characters; this one has ${String(length(text))}.`
    }
    setProblem(invalid)
    const base = summary.currentRevisionId
    if (invalid !== null || base === null) return
    const fingerprint = JSON.stringify([summary.id, base, text])
    if (attempt.current?.fingerprint !== fingerprint) {
      attempt.current = { fingerprint, key: newIdempotencyKey() }
    }
    const key = attempt.current.key
    void action
      .run(
        async () => {
          const connection = (await getConnections()).find((c) => c.provider === 'mowl')
          if (connection === undefined || connection.status !== 'connected') {
            throw new ApiError(409, 'not_connected', null, undefined)
          }
          await createReviseJob(
            {
              class_id: summary.id,
              base_revision_id: base,
              request: text,
              mowl_connection_id: connection.id,
            },
            key,
          )
          return true
        },
        (error) => {
          if (isStale(error)) onReload()
          return classReasonText(error)
        },
      )
      .then((sent) => {
        if (sent === true) {
          setRequest('')
          attempt.current = null
          onReload()
        }
      })
  }

  const blocked =
    summary.currentRevisionId === null
      ? 'There is no version to change yet.'
      : running !== null && !revising
        ? 'This class is busy with another task. You can ask for a change when it is done.'
        : null

  return (
    <section className="panel chat" aria-labelledby="chat-title">
      <h2 id="chat-title" className="panel__title">
        Change it
      </h2>
      <History
        messages={messages}
        numbers={numbers}
        openJobId={running?.id ?? null}
        onView={onView}
        songs={
          summary.songs.state === 'approved' && summary.songs.revisionId !== null
            ? 'change'
            : summary.songs.state === 'none'
              ? 'own'
              : 'pending'
        }
      />
      {revising && running ? (
        <ReviseRun key={running.id} jobId={running.id} onFinished={onReload} />
      ) : null}
      {blocked !== null ? <Notice>{blocked}</Notice> : null}
      <form onSubmit={submit} noValidate aria-label="Ask for a change">
        {problem ? <Notice kind="error">{problem}</Notice> : null}
        {action.error ? <Notice kind="error">{action.error}</Notice> : null}
        <div className="field">
          <label htmlFor="change-request">What should change?</label>
          <textarea
            id="change-request"
            rows={3}
            value={request}
            disabled={blocked !== null || revising}
            onChange={(e) => {
              setRequest(e.target.value)
            }}
            aria-describedby="change-request-hint"
          />
          <p id="change-request-hint" className="field__hint">
            For example: make the second climb longer, or add a sprint before the cool-down. Up to{' '}
            {MAX_REQUEST_CHARS} characters.
          </p>
        </div>
        <button
          type="submit"
          className="button"
          disabled={action.busy || blocked !== null || revising}
        >
          Change it
        </button>
      </form>
    </section>
  )
}
