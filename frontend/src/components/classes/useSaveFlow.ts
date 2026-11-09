import { useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { getClass, getRevision, type ClassSummary } from '../../api/classes'
import { getConnections } from '../../api/connections'
import type { Job } from '../../api/jobs'
import {
  outcomeText,
  refusalChangesPage,
  saveReasonText,
  type WriteKind,
  type WriteOutcome,
} from '../../api/saveMessages'
import { createSaveJob, createUpdateJob, SAVE_KIND, UPDATE_KIND } from '../../api/saving'
import { useT } from '../../i18n/I18n'
import { contentHash } from '../../lib/canonical'
import { newIdempotencyKey } from '../../lib/submittedInput'
import { useAction } from '../forms'

export type Mode = 'save' | 'update' | 'overwrite'

export interface Finished {
  kind: WriteKind
  job: Job
  /**
   * For a completed Update: whether the class's newest saved row was read back and matched, read
   * from the class after the job ended (`checking` until that answer is in; a failed read counts
   * as not confirmed, so the page never claims a match it did not see).
   */
  verified: boolean | 'checking'
}

/**
 * The state of Save to MOWL and Update saved (ADR 0007 sections 5 to 9), kept apart from how it is
 * drawn: the class header shows the buttons and the state line, the notices below it show the
 * confirmation, the progress and the outcome. The click is the approval of the current version,
 * sent as ids and hash only.
 */
export function useSaveFlow(summary: ClassSummary, onReload: () => void) {
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
  // overwrite, and still goes through the confirmation.
  const [overwriteNext, setOverwriteNext] = useState(false)
  const [again, setAgain] = useState<WriteKind | null>(null)
  const [version, setVersion] = useState(0)
  const t = useT()
  const action = useAction()
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null)

  const { saved, openJob } = summary
  const current = summary.currentRevisionId
  const number = summary.currentRevisionNumber
  const hasSaved = saved.count > 0
  const detached = saved.detachedCount > 0
  const upToDate = saved.latestUpToDate === true
  const writing = openJob !== null && (openJob.kind === SAVE_KIND || openJob.kind === UPDATE_KIND)
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

  const onRunLost = () => {
    setStarted(null)
    setLost(true)
    onReload()
  }

  const onRunFinished = (ended: { kind: WriteKind; job: Job }) => {
    const needsRead = ended.kind === 'update' && ended.job.state === 'completed'
    const done: Finished = { ...ended, verified: needsRead ? 'checking' : true }
    setFinished(done)
    if (needsRead) {
      // Read the class once more for the saved row's verified flag; a failed read is
      // "not confirmed".
      void getClass(summary.id)
        .then(
          (fresh) => fresh.saved.latestVerified === true,
          () => false,
        )
        .then((verified) => {
          setFinished((now) => (now === done ? { ...done, verified } : now))
        })
    }
    setStarted(null)
    setVersion((n) => n + 1)
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

  return {
    summary,
    mode,
    setMode,
    finished,
    decision,
    lost,
    again,
    version,
    action,
    current,
    number,
    hasSaved,
    detached,
    upToDate,
    writing,
    busy,
    run,
    savedId,
    stateLine,
    canWrite: current !== null && !busy && !action.busy,
    showUpdate: hasSaved && !upToDate,
    updateOverwrites: overwriteNext,
    start,
    decided,
    onRunLost,
    onRunFinished,
  }
}

export type SaveFlow = ReturnType<typeof useSaveFlow>
