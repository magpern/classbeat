import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { getRevision, type ClassSummary } from '../../api/classes'
import { getConnections } from '../../api/connections'
import { retryLabel, stageLabel } from '../../api/jobMessages'
import { cancelJob, isTerminal, type Job } from '../../api/jobs'
import {
  needsAccount,
  OUTCOME_TEXT,
  refusalChangesPage,
  saveFailureText,
  saveReasonText,
  type WriteKind,
  type WriteOutcome,
} from '../../api/saveMessages'
import { createSaveJob, createUpdateJob, SAVE_KIND, UPDATE_KIND } from '../../api/saving'
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
}

/** The live progress of the class's running save or update, from the job's events. */
function WriteRun({
  jobId,
  kind,
  onFinished,
}: {
  jobId: string
  kind: WriteKind
  onFinished: (done: Finished) => void
}) {
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

  if (problem === 'not_found') return <Notice>This task is no longer there.</Notice>
  const stopping = job?.cancelRequested === true || progress.cancelRequested
  return (
    <div className="chat__run" role="status" aria-live="polite" data-testid="save-progress">
      {problem === 'failed' ? <p>The connection to the server dropped. Trying again.</p> : null}
      {stopping ? (
        <p>Stopping. This takes a moment.</p>
      ) : progress.retry !== null ? (
        <p>{retryLabel(progress.retry)}</p>
      ) : job === null || (job.state === 'queued' && progress.stage === null) ? (
        <p>Waiting for a free slot.</p>
      ) : (
        <p>
          {kind === 'save' ? 'Saving to MOWL: ' : 'Updating in MOWL: '}
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
        Stop
      </Button>
    </div>
  )
}

function Outcome({
  done,
  onUpdateAnyway,
}: {
  done: Finished
  onUpdateAnyway: () => void
}) {
  const { job, kind } = done
  if (job.state === 'completed') {
    return (
      <Notice kind="success">
        {kind === 'save'
          ? 'Saved. The workout is in your MOWL account, private.'
          : 'Updated. Your MOWL workout now matches this version.'}
      </Notice>
    )
  }
  if (job.state === 'failed' && job.errorCategory === 'uncertain_write') {
    return (
      <Notice kind="error">
        classbeat could not tell whether MOWL took it. Read the box below, then decide.
      </Notice>
    )
  }
  if (job.state === 'failed') {
    return (
      <div className="flex flex-col gap-3">
        <Notice kind="error">
          {saveFailureText(job.errorCategory)}{' '}
          {needsAccount(job.errorCategory) ? <Link to="/account">Go to your account</Link> : null}
        </Notice>
        {kind === 'update' && job.errorCategory === 'program_changed' ? (
          <p>
            <Button variant="secondary" onClick={onUpdateAnyway}>
              Update anyway (replaces your changes in MOWL)
            </Button>
          </p>
        ) : null}
      </div>
    )
  }
  return <Notice>This was stopped before anything was written, or classbeat lists it below.</Notice>
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
  const [decision, setDecision] = useState<string | null>(null)
  // After Do it anyway on an update the MOWL copy may be partly written: the next Update is an
  // overwrite, and still goes through the dialog.
  const [overwriteNext, setOverwriteNext] = useState(false)
  const [again, setAgain] = useState<WriteKind | null>(null)
  const [version, setVersion] = useState(0)
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
        setDecision(null)
        setAgain(null)
        if (kind === 'update' && overwrite) setOverwriteNext(false)
        onReload()
      })
  }

  const decided = (kind: WriteKind, outcome: WriteOutcome) => {
    setDecision(OUTCOME_TEXT[kind][outcome])
    if (outcome === 'still_unsure') return
    setAgain(kind)
    setOverwriteNext(kind === 'update' && outcome === 'decided')
    onReload()
  }

  const stateLine = !hasSaved
    ? detached
      ? 'This class was saved earlier to a previous MOWL connection. That workout is no longer linked, so it cannot be updated. Save adds a new one to your current MOWL account.'
      : 'Not saved to MOWL yet.'
    : upToDate
      ? `Saved to MOWL as version ${String(saved.latestRevisionNumber ?? '?')}. It is up to date.`
      : `Saved to MOWL as version ${String(saved.latestRevisionNumber ?? '?')}. The current version (${String(number ?? '?')}) has changes that are not saved yet.`

  const canWrite = current !== null && !busy && !action.busy
  const showUpdate = hasSaved && !upToDate
  const updateOverwrites = overwriteNext

  return (
    <>
      <Card className="flex flex-col gap-3" role="region" aria-labelledby="save-title" data-testid="save-panel">
        <h2 id="save-title" className="m-0 font-display text-xl font-bold">
          Save to MOWL
        </h2>
        <p data-testid="saved-state">{stateLine}</p>
        {hasSaved && detached ? (
          <p className="muted">
            Another workout was saved earlier to a previous MOWL connection and is not linked any more.
          </p>
        ) : null}
        {current === null ? <p className="muted">There is no version to save yet.</p> : null}
        {decision ? <Notice kind="info">{decision}</Notice> : null}
        {finished ? (
          <Outcome
            done={finished}
            onUpdateAnyway={() => {
              setMode('overwrite')
            }}
          />
        ) : null}
        {action.error ? <Notice kind="error">{action.error}</Notice> : null}
        {writing && openJob ? (
          <WriteRun
            key={openJob.id}
            jobId={openJob.id}
            kind={openJob.kind === SAVE_KIND ? 'save' : 'update'}
            onFinished={(done) => {
              setFinished(done)
              setVersion((n) => n + 1)
              onReload()
            }}
          />
        ) : null}
        {busy && !writing ? <p className="muted">Wait for the running task to finish first.</p> : null}
        {mode === null ? (
          <p className="m-0 flex flex-wrap gap-3">
            <Button
              
              disabled={!canWrite}
              onClick={() => {
                setMode('save')
              }}
            >
              {again === 'save' ? 'Save again' : hasSaved ? 'Save as a new workout' : 'Save to MOWL'}
            </Button>
            {showUpdate ? (
              <Button
                variant="secondary"
                disabled={!canWrite || savedId === null}
                onClick={() => {
                  setMode(updateOverwrites ? 'overwrite' : 'update')
                }}
              >
                {again === 'update' ? 'Update again' : 'Update saved workout'}
              </Button>
            ) : null}
          </p>
        ) : (
          <div className="notice" role="group" aria-label="Confirm" tabIndex={-1} ref={confirmRef}>
            {mode === 'save' ? (
              <>
                <p>
                  classbeat will add version {number ?? '?'} of this class to your MOWL account as a
                  new workout. It is private: only you can see it.
                  {hasSaved ? ' A new workout will be added; the one you saved earlier stays as it is.' : ''}
                </p>
                <ConfirmButtons
                  label="Save to MOWL"
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
                  classbeat will first read the workout in your MOWL account. If you changed it in
                  MOWL after classbeat saved it, classbeat stops and writes nothing. Otherwise it
                  replaces the workout with version {number ?? '?'}. It stays private, in your MOWL
                  account.
                </p>
                <ConfirmButtons
                  label="Update workout"
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
                  <strong>Update anyway?</strong> This replaces the workout in your MOWL account with
                  version {number ?? '?'}, including any changes you made to it in MOWL, or whatever
                  a write that did not finish left there. It cannot be undone. The workout stays
                  private.
                </p>
                <ConfirmButtons
                  label="Yes, replace it"
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
            Updating the saved workout is not available yet. Save adds a new workout instead.
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
  return (
    <>
      <Button  disabled={busy} onClick={onConfirm}>
        {label}
      </Button>
      <Button variant="secondary" disabled={busy} onClick={onCancel}>
        Cancel
      </Button>
    </>
  )
}
