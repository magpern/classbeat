import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { getClass, getRevision, type ClassSummary } from '../../api/classes'
import { getConnections } from '../../api/connections'
import { retryLabel, stageLabel } from '../../api/jobMessages'
import { cancelJob, isTerminal, type Job } from '../../api/jobs'
import {
  needsAccount,
  outcomeText,
  refusalChangesPage,
  saveFailureText,
  savedText,
  saveReasonText,
  updatedText,
  type WriteKind,
  type WriteOutcome,
} from '../../api/saveMessages'
import { createSaveJob, createUpdateJob, SAVE_KIND, UPDATE_KIND } from '../../api/saving'
import { useT } from '../../i18n/I18n'
import { useJobProgress } from '../../hooks/useJobProgress'
import { contentHash } from '../../lib/canonical'
import { newIdempotencyKey } from '../../lib/submittedInput'
import { Link } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Card } from '../ui/card'
import { UncertainWrite } from './UncertainWrite'

type Mode = 'save' | 'update' | 'overwrite'

interface Finished {
  kind: WriteKind
  job: Job
  /**
   * For a completed Update: whether the class's newest saved row was read back and matched, read
   * from the class after the job ended (`checking` until that answer is in; a failed read counts
   * as not confirmed, so the page never claims a match it did not see).
   */
  verified: boolean | 'checking'
}

/** The live progress of the class's running save or update, from the job's events. */
function WriteRun({
  jobId,
  kind,
  onFinished,
  onLost,
}: {
  jobId: string
  kind: WriteKind
  onFinished: (done: { kind: WriteKind; job: Job }) => void
  /** The task cannot be read any more (404): the page drops it and reads the class again. */
  onLost: () => void
}) {
  const t = useT()
  const { job, progress, problem, setJob } = useJobProgress(jobId)
  const action = useAction()
  const over = job !== null && isTerminal(job.state)
  const finished = useRef(false)
  useEffect(() => {
    if (job !== null && over && !finished.current) {
      finished.current = true
      onFinished({ kind, job })
    }
  }, [job, over, kind, onFinished])

  const lost = useRef(false)
  useEffect(() => {
    if (problem === 'not_found' && !lost.current) {
      lost.current = true
      onLost()
    }
  }, [problem, onLost])

  // The page shows the sentence (`lost`) and drops this progress, so it does not stay after a reload.
  if (problem === 'not_found') return null
  const stopping = job?.cancelRequested === true || progress.cancelRequested
  return (
    <div className="chat__run" role="status" aria-live="polite" data-testid="save-progress">
      {problem === 'failed' ? <p>{t('class.runDropped')}</p> : null}
      {stopping ? (
        <p>{t('class.runStopping')}</p>
      ) : progress.retry !== null ? (
        <p>{retryLabel(progress.retry)}</p>
      ) : job === null || (job.state === 'queued' && progress.stage === null) ? (
        <p>{t('class.runWaiting')}</p>
      ) : (
        <p>
          {kind === 'save' ? t('class.save.runSave') : t('class.save.runUpdate')}{' '}
          {stageLabel(progress.stage ?? job.stage)}…
        </p>
      )}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <Button
        variant="secondary"
        disabled={action.busy || stopping || over}
        onClick={() => {
          void action.run(async () => {
            setJob(await cancelJob(jobId))
          })
        }}
      >
        {t('class.save.stop')}
      </Button>
    </div>
  )
}

function Outcome({
  done,
  soundless,
  onUpdateAnyway,
}: {
  done: Finished
  soundless: boolean
  onUpdateAnyway: () => void
}) {
  const t = useT()
  const { job, kind } = done
  if (job.state === 'completed') {
    if (kind === 'save') return <Notice kind="success">{savedText(soundless)}</Notice>
    if (done.verified === 'checking') return <Notice>{t('class.save.checking')}</Notice>
    return <Notice kind={done.verified ? 'success' : 'info'}>{updatedText(done.verified)}</Notice>
  }
  if (job.state === 'failed' && job.errorCategory === 'uncertain_write') {
    return (
      <Notice kind="error">{t('class.save.uncertain')}</Notice>
    )
  }
  if (job.state === 'failed') {
    return (
      <div className="flex flex-col gap-3">
        <Notice kind="error">
          {saveFailureText(job.errorCategory)}{' '}
          {needsAccount(job.errorCategory) ? <Link to="/account">{t('class.goToAccount')}</Link> : null}
        </Notice>
        {kind === 'update' && job.errorCategory === 'program_changed' ? (
          <p>
            <Button variant="secondary" onClick={onUpdateAnyway}>
              {t('class.save.updateAnyway')}
            </Button>
          </p>
        ) : null}
      </div>
    )
  }
  return <Notice>{t('class.save.stopped')}</Notice>
}

/**
 * Save to MOWL and Update saved (ADR 0007 sections 5 to 9). The state line says what MOWL holds
 * for this class; the buttons ask first (what is written, private, in your MOWL account); the
 * click is the approval of the current version, sent as ids and hash only. Undecided writes are
 * shown by `UncertainWrite`, and Save and Update explain the `write_unresolved` refusal.
 */
export function SavePanel({
  summary,
  onReload,
}: {
  summary: ClassSummary
  onReload: () => void
}) {
  const [mode, setMode] = useState<Mode | null>(null)
  const [finished, setFinished] = useState<Finished | null>(null)
  // The job this page just created. A save or update can end before the reload after it sees it
  // open (an Update that stops at `program_changed` is one read), and then `summary.openJob` never
  // holds it: following the created job keeps its outcome from being lost.
  const [started, setStarted] = useState<{ id: string; kind: WriteKind } | null>(null)
  const [decision, setDecision] = useState<string | null>(null)
  // The task this page followed could not be read any more (it is gone, or not ours any more).
  const [lost, setLost] = useState(false)
  // After Do it anyway on an update the MOWL copy may be partly written: the next Update is an
  // overwrite, and still goes through the dialog.
  const [overwriteNext, setOverwriteNext] = useState(false)
  const [again, setAgain] = useState<WriteKind | null>(null)
  const [version, setVersion] = useState(0)
  const t = useT()
  const action = useAction()
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null)
  const confirmRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (mode !== null) confirmRef.current?.focus()
  }, [mode])

  const { saved, openJob } = summary
  const current = summary.currentRevisionId
  const number = summary.currentRevisionNumber
  const hasSaved = saved.count > 0
  const detached = saved.detachedCount > 0
  const upToDate = saved.latestUpToDate === true
  const writing =
    openJob !== null && (openJob.kind === SAVE_KIND || openJob.kind === UPDATE_KIND)
  const busy = openJob !== null
  const run =
    writing && openJob
      ? { id: openJob.id, kind: openJob.kind === SAVE_KIND ? ('save' as const) : ('update' as const) }
      : started
  const savedId = saved.latestSavedWorkoutId

  const start = (kind: WriteKind, overwrite: boolean) => {
    if (current === null) return
    const fingerprint = JSON.stringify([summary.id, current, kind, overwrite, savedId])
    if (attempt.current?.fingerprint !== fingerprint) {
      attempt.current = { fingerprint, key: newIdempotencyKey() }
    }
    const key = attempt.current.key
    void action
      .run(
        async () => {
          if (kind === 'update' && savedId === null) throw new ApiError(409, 'invalid_input', null, undefined)
          const connection = (await getConnections()).find((c) => c.provider === 'mowl')
          if (connection === undefined || connection.status !== 'connected') {
            throw new ApiError(409, 'not_connected', null, undefined)
          }
          // The hash of the content this page holds for the current version: the server refuses
          // (409 `hash_mismatch`) unless the revision is the one that was shown (section 5).
          const full = await getRevision(summary.id, current)
          const base = {
            class_id: summary.id,
            revision_id: current,
            content_hash: await contentHash(full.raw),
            mowl_connection_id: connection.id,
          }
          if (kind === 'save') return createSaveJob(base, key)
          return createUpdateJob({ ...base, saved_workout_id: savedId!, overwrite }, key)
        },
        (error) => {
          if (refusalChangesPage(error)) onReload()
          // The undecided list changes the same way: read it again.
          if (error instanceof ApiError && error.reason === 'write_unresolved') {
            setVersion((n) => n + 1)
          }
          return saveReasonText(error)
        },
      )
      .then((job) => {
        if (job === undefined) return
        attempt.current = null
        setMode(null)
        setFinished(null)
        setStarted({ id: job.id, kind })
        setLost(false)
        setDecision(null)
        setAgain(null)
        if (kind === 'update' && overwrite) setOverwriteNext(false)
        onReload()
      })
  }

  const decided = (kind: WriteKind, outcome: WriteOutcome) => {
    setDecision(outcomeText(kind, outcome))
    if (outcome === 'still_unsure') return
    setAgain(kind)
    setOverwriteNext(kind === 'update' && outcome === 'decided')
    onReload()
  }

  const stateLine = !hasSaved
    ? detached
      ? t('class.save.detachedLine')
      : t('class.save.notSaved')
    : upToDate
      ? t('class.save.upToDate', { saved: String(saved.latestRevisionNumber ?? '?') })
      : t('class.save.behind', {
          saved: String(saved.latestRevisionNumber ?? '?'),
          current: String(number ?? '?'),
        })

  const canWrite = current !== null && !busy && !action.busy
  const showUpdate = hasSaved && !upToDate
  const updateOverwrites = overwriteNext

  return (
    <>
      <Card className="flex flex-col gap-3" role="region" aria-labelledby="save-title" data-testid="save-panel">
        <h2 id="save-title" className="m-0 font-display text-xl font-bold">
          {t('class.save.title')}
        </h2>
        <p data-testid="saved-state">{stateLine}</p>
        {hasSaved && detached ? (
          <p className="muted">{t('class.save.detachedOther')}</p>
        ) : null}
        {current === null ? <p className="muted">{t('class.save.noVersion')}</p> : null}
        {decision ? <Notice kind="info">{decision}</Notice> : null}
        {finished ? (
          <Outcome
            done={finished}
            soundless={summary.soundless}
            onUpdateAnyway={() => {
              setMode('overwrite')
            }}
          />
        ) : null}
        {action.error ? <Notice kind="error">{action.error}</Notice> : null}
        {lost ? (
          <Notice>{t('class.save.lost')}</Notice>
        ) : null}
        {run ? (
          <WriteRun
            key={run.id}
            jobId={run.id}
            kind={run.kind}
            onLost={() => {
              setStarted(null)
              setLost(true)
              onReload()
            }}
            onFinished={(ended) => {
              const needsRead = ended.kind === 'update' && ended.job.state === 'completed'
              const done: Finished = { ...ended, verified: needsRead ? 'checking' : true }
              setFinished(done)
              if (needsRead) {
                // Read the class once more for the saved row's verified flag; a failed read is
                // "not confirmed".
                void getClass(summary.id).then(
                  (fresh) => fresh.saved.latestVerified === true,
                  () => false,
                ).then((verified) => {
                  setFinished((now) => (now === done ? { ...done, verified } : now))
                })
              }
              setStarted(null)
              setVersion((n) => n + 1)
              onReload()
            }}
          />
        ) : null}
        {busy && !writing ? <p className="muted">{t('class.waitRunning')}</p> : null}
        {mode === null ? (
          <p className="m-0 flex flex-wrap gap-3">
            <Button
              
              disabled={!canWrite}
              onClick={() => {
                setMode('save')
              }}
            >
              {again === 'save'
                ? t('class.save.again')
                : hasSaved
                  ? t('class.save.asNew')
                  : t('class.save.title')}
            </Button>
            {showUpdate ? (
              <Button
                variant="secondary"
                disabled={!canWrite || savedId === null}
                onClick={() => {
                  setMode(updateOverwrites ? 'overwrite' : 'update')
                }}
              >
                {again === 'update' ? t('class.save.updateAgain') : t('class.save.update')}
              </Button>
            ) : null}
          </p>
        ) : (
          <div className="notice" role="group" aria-label={t('class.confirm')} tabIndex={-1} ref={confirmRef}>
            {mode === 'save' ? (
              <>
                <p>
                  {t(summary.soundless ? 'class.save.confirmSaveSoundless' : 'class.save.confirmSave', {
                    number: String(number ?? '?'),
                  })}
                  {hasSaved ? ` ${t('class.save.confirmSaveMore')}` : ''}
                </p>
                <ConfirmButtons
                  label={t('class.save.title')}
                  busy={action.busy}
                  onConfirm={() => {
                    start('save', false)
                  }}
                  onCancel={() => {
                    setMode(null)
                  }}
                />
              </>
            ) : null}
            {mode === 'update' ? (
              <>
                <p>
                  {t(
                    summary.soundless
                      ? 'class.save.confirmUpdateSoundless'
                      : 'class.save.confirmUpdate',
                    { number: String(number ?? '?') },
                  )}
                </p>
                <ConfirmButtons
                  label={t('class.save.updateButton')}
                  busy={action.busy}
                  onConfirm={() => {
                    start('update', false)
                  }}
                  onCancel={() => {
                    setMode(null)
                  }}
                />
              </>
            ) : null}
            {mode === 'overwrite' ? (
              <>
                <p>
                  <strong>{t('class.save.overwriteTitle')}</strong>{' '}
                  {t('class.save.overwriteText', { number: String(number ?? '?') })}
                </p>
                <ConfirmButtons
                  label={t('class.save.replace')}
                  busy={action.busy}
                  onConfirm={() => {
                    start('update', true)
                  }}
                  onCancel={() => {
                    setMode(null)
                  }}
                />
              </>
            ) : null}
          </div>
        )}
        {hasSaved && !upToDate && savedId === null ? (
          <p className="muted" data-testid="update-unavailable">
            {t('class.save.updateUnavailable')}
          </p>
        ) : null}
      </Card>
      <UncertainWrite
        classId={summary.id}
        version={version}
        onDecided={decided}
        onChanged={onReload}
      />
    </>
  )
}

function ConfirmButtons({
  label,
  busy,
  onConfirm,
  onCancel,
}: {
  label: string
  busy: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const t = useT()
  return (
    <>
      <Button  disabled={busy} onClick={onConfirm}>
        {label}
      </Button>
      <Button variant="secondary" disabled={busy} onClick={onCancel}>
        {t('class.cancel')}
      </Button>
    </>
  )
}
