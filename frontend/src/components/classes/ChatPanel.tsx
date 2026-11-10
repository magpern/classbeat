import { useRef, useState, type FormEvent } from 'react'
import { ApiError } from '../../api/client'
import { classReasonText, describeChange, isStale } from '../../api/classMessages'
import type { ClassMessage, ClassSummary, RevisionSummary } from '../../api/classes'
import { getConnections } from '../../api/connections'
import { createReviseJob, isTerminal } from '../../api/jobs'
import { useT } from '../../i18n/I18n'
import { newIdempotencyKey } from '../../lib/submittedInput'
import { Notice, useAction } from '../forms'
import { ReviseRun, Unanswered, type SongsWay } from './ChatRuns'

export const MAX_REQUEST_CHARS = 2000

/** Characters the way the backend counts them (code points, not UTF-16 units). */
function length(value: string): number {
  return Array.from(value).length
}

function History({
  messages,
  numbers,
  openJobId,
  onView,
  onChangeSongs,
  songs,
}: {
  messages: ClassMessage[]
  numbers: ReadonlyMap<string, number>
  openJobId: string | null
  onView: (revisionId: string) => void
  onChangeSongs: () => void
  songs: SongsWay
}) {
  const t = useT()
  const answered = new Set(
    messages.filter((m) => m.role === 'assistant' && m.jobId !== null).map((m) => m.jobId),
  )
  const unanswered = messages.filter(
    (m) => m.role === 'user' && m.jobId !== null && !answered.has(m.jobId) && m.jobId !== openJobId,
  )
  const latestUnanswered = unanswered.at(-1)?.seq
  if (messages.length === 0) {
    return <p className="muted">{t('class.chat.empty')}</p>
  }
  return (
    <ol className="chat__history" aria-label={t('class.chat.requests')}>
      {messages.map((m) => {
        if (m.role === 'user') {
          return (
            <li key={m.seq} className="chat__message chat__message--user">
              <p className="chat__who">{t('class.chat.youAsked')}</p>
              <p className="chat__text">{m.body}</p>
              {m.jobId !== null && !answered.has(m.jobId) && m.jobId !== openJobId ? (
                <Unanswered
                  message={m}
                  latest={m.seq === latestUnanswered}
                  songs={songs}
                  onChangeSongs={onChangeSongs}
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
              {number === undefined
                ? t('class.chat.changed')
                : t('class.chat.changedVersion', { number })}
            </p>
            {lines.length > 0 ? (
              <ul className="chat__change">
                {lines.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            ) : null}
            {m.change?.musicChanged === true && songs !== 'soundless' ? (
              <p className="chat__text">{t('class.chat.musicChanged')}</p>
            ) : null}
            {m.body ? (
              <p className="chat__text">
                <span className="muted">{t('class.chat.designerNote')} </span>
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
                {t('class.chat.viewVersion', { number })}
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
  canChange = true,
  requestJobId = null,
  onReload,
  onView,
  onChangeSongs,
}: {
  summary: ClassSummary
  revisions: RevisionSummary[]
  messages: ClassMessage[]
  /** False when the class history says Change it is not available (the server would refuse it). */
  canChange?: boolean
  /** The reading job of a class started from the start screen: its message is shown by the workspace, not here. */
  requestJobId?: string | null
  onReload: () => void
  onView: (revisionId: string) => void
  /** Opens the Change songs panel on the Class side. */
  onChangeSongs: () => void
}) {
  const t = useT()
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
    if (text === '') invalid = t('class.chat.writeSomething')
    else if (length(text) > MAX_REQUEST_CHARS) {
      invalid = t('class.chat.tooLong', { max: MAX_REQUEST_CHARS, count: length(text) })
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
      ? t('class.chat.noVersion')
      : !canChange
        ? t('class.chat.unavailable')
        : running !== null && !revising
          ? t('class.chat.busy')
          : null

  return (
    <section className="panel chat" aria-labelledby="chat-title">
      <h2 id="chat-title" className="panel__title">
        {t('class.chat.title')}
      </h2>
      <History
        messages={messages.filter((m) => requestJobId === null || m.jobId !== requestJobId)}
        numbers={numbers}
        openJobId={running?.id ?? null}
        onView={onView}
        onChangeSongs={onChangeSongs}
        songs={
          summary.soundless
            ? 'soundless'
            : summary.sport === 'running'
            ? 'running'
            : summary.songs.state === 'approved' && summary.songs.revisionId !== null
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
      <form onSubmit={submit} noValidate aria-label={t('class.chat.form')}>
        {problem ? <Notice kind="error">{problem}</Notice> : null}
        {action.error ? <Notice kind="error">{action.error}</Notice> : null}
        <div className="field">
          <label htmlFor="change-request">{t('class.chat.label')}</label>
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
            {t('class.chat.hint', { max: MAX_REQUEST_CHARS })}
          </p>
        </div>
        <button
          type="submit"
          className="button"
          disabled={action.busy || blocked !== null || revising}
        >
          {t('class.chat.title')}
        </button>
      </form>
    </section>
  )
}
