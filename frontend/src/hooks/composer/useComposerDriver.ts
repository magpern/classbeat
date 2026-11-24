import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { saveExactly } from '../../api/approveAndSave'
import { classReasonText } from '../../api/classMessages'
import type { ClassSummary } from '../../api/classes'
import { getConnections } from '../../api/connections'
import { fillSongs } from '../../api/fillSongs'
import { submitPending, type PendingMessage } from '../../api/pendingMessages'
import { nextStep, stepKey, type DriverStep } from '../../lib/composer/driver'
import type { MusicGate } from '../../lib/composer/musicGate'
import { SKETCH } from '../../lib/composer/workspaceKind'

/**
 * The web's one driver (ADR 0038 section 8): `nextStep` decides, this runs it, once per state.
 * Every call is idempotent on the server (the fill by the sketch's key, the submit by the message
 * id, the save by the phase-2 job id), so a closed tab, a second tab or a reload only repeats an
 * answer; the state is read from the class, never remembered here. A step that failed is not run
 * again for the same state: the fill's refusal is stored on the class for Find songs again, and a
 * failed save shows its error with the ordinary Save button.
 */
export function useComposerDriver({
  summary,
  currentOrigin,
  messages,
  music,
  reload,
  refreshMessages,
  paused = false,
}: {
  summary: ClassSummary
  currentOrigin: string | null
  /** `null` until the stored messages are read: the driver waits, so a pending one goes first. */
  messages: readonly PendingMessage[] | null
  /** What the song search waits for: the fill starts only when it is `ready` (the question, Spotify, the consent). */
  music: MusicGate
  reload: () => void
  refreshMessages: () => void
  /** The operator has turned the composer off: no step is started (the server would answer `feature_off`). */
  paused?: boolean
}): { error: string | null } {
  const [error, setError] = useState<string | null>(null)
  const last = useRef<string>('')
  const reloadRef = useRef(reload)
  const refreshRef = useRef(refreshMessages)
  useEffect(() => {
    reloadRef.current = reload
    refreshRef.current = refreshMessages
  })

  const job = summary.openJob
  const running = job !== null && (job.state === 'queued' || job.state === 'running')
  const sketchCurrent = currentOrigin === SKETCH
  const fillStarted =
    summary.composer.fill !== null && summary.composer.fill.sketchRevisionId === summary.currentRevisionId
  const step: DriverStep =
    messages === null || paused
      ? { kind: 'none' }
      : nextStep({ running, sketchCurrent, fillStarted, savePending: summary.composer.savePending, messages, music })
  const key = stepKey(summary.id, step, summary.currentRevisionId)

  useEffect(() => {
    if (key === last.current) return
    last.current = key
    if (step.kind === 'none') return
    void run(summary.id, summary.currentRevisionId, step).then(
      (outcome) => {
        if (outcome === 'busy') {
          last.current = '' // the job is still running: the next reload tries again
          return
        }
        setError(null)
        refreshRef.current()
        reloadRef.current()
      },
      (failure: unknown) => {
        setError(classReasonText(failure))
        refreshRef.current()
        reloadRef.current()
      },
    )
    // `step` is a function of `key`: the effect runs once per state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return { error }
}

async function run(classId: string, current: string | null, step: DriverStep): Promise<'done' | 'busy'> {
  if (step.kind === 'fill') {
    if (current === null) return 'done'
    try {
      await fillSongs({ classId, sketchRevisionId: current, retry: false })
    } catch (error) {
      // `class_busy` is the one refusal the server does not store: try again at the next reload.
      if (error instanceof ApiError && error.reason === 'class_busy') return 'busy'
      // Any other refusal is stored on the class and shown there with Find songs again.
      if (error instanceof ApiError && error.status === 409) return 'done'
      throw error
    }
    return 'done'
  }
  if (step.kind === 'submit') {
    try {
      await submitPending(classId, step.messageId)
    } catch (error) {
      if (error instanceof ApiError && error.reason === 'class_busy') return 'busy'
      // Any other refusal left the message `failed` with its reason: the list shows it.
      if (error instanceof ApiError && error.status === 409) return 'done'
      throw error
    }
    return 'done'
  }
  if (step.kind === 'save') {
    const mowl = (await getConnections()).find((c) => c.provider === 'mowl' && c.status === 'connected')
    if (mowl === undefined) throw new ApiError(409, 'not_connected', null, undefined)
    await saveExactly({
      classId,
      revisionId: step.target.revisionId,
      contentHash: step.target.contentHash,
      jobId: step.target.jobId,
      mowlConnectionId: mowl.id,
    })
  }
  return 'done'
}
