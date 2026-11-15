import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { getRevision, type ClassSummary } from '../../api/classes'
import type { Job } from '../../api/jobs'
import { getPlayerStatus, type PlayerStatus } from '../../api/player'
import { playerReasonText, playerRefusalChangesPage } from '../../api/playerMessages'
import { PLAYER_SEND_KIND, createSendToPlayerJob } from '../../api/playerSend'
import { contentHash } from '../../lib/canonical'
import { newIdempotencyKey } from '../../lib/submittedInput'
import { useAction } from '../forms'

/** A send that has ended: its job and the version it carried (the version of the press). */
export interface SendEnded {
  job: Job
  number: number
}

/** `undefined` while the status loads, `null` when it could not be read. */
type Status = PlayerStatus | null | undefined

function usePlayerStatus(): Status {
  const [status, setStatus] = useState<Status>(undefined)
  useEffect(() => {
    const controller = new AbortController()
    getPlayerStatus(controller.signal).then(
      (found) => {
        if (!controller.signal.aborted) setStatus(found)
      },
      () => {
        if (!controller.signal.aborted) setStatus(null)
      },
    )
    return () => {
      controller.abort()
    }
  }, [])
  return status
}

/**
 * The state of "Send to player" (ADR 0034 sections 5 and 6), kept apart from how it is drawn. The
 * press opens a confirmation; only its button creates the job, and the job names the class, the
 * version and that version's hash, never a document or a number. Nothing is sent on its own.
 * `version` changes whenever the list of undecided operations must be read again.
 */
export function useSendToPlayer(summary: ClassSummary, onReload: () => void) {
  const [confirming, setConfirming] = useState(false)
  const [started, setStarted] = useState<{ id: string; number: number } | null>(null)
  const [ended, setEnded] = useState<SendEnded | null>(null)
  const [lost, setLost] = useState(false)
  const [version, setVersion] = useState(0)
  const action = useAction()
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null)
  const player = usePlayerStatus()

  const { openJob } = summary
  const current = summary.currentRevisionId
  const number = summary.currentRevisionNumber
  const sending = openJob !== null && openJob.kind === PLAYER_SEND_KIND
  const busy = openJob !== null
  // The run this page follows: the open job after a reload, else the job it just created.
  const run =
    sending && openJob ? { id: openJob.id, number: started?.number ?? number ?? 0 } : started

  const press = () => {
    if (current === null) return
    const fingerprint = JSON.stringify([summary.id, current])
    if (attempt.current?.fingerprint !== fingerprint) {
      attempt.current = { fingerprint, key: newIdempotencyKey() }
    }
    const key = attempt.current.key
    void action
      .run(
        async () => {
          // The hash of the content this page holds for the current version: the server refuses
          // (409 `hash_mismatch`) unless the revision is the one that was shown.
          const full = await getRevision(summary.id, current)
          return createSendToPlayerJob(
            { class_id: summary.id, revision_id: current, content_hash: await contentHash(full.raw) },
            key,
          )
        },
        (error) => {
          if (playerRefusalChangesPage(error)) onReload()
          if (error instanceof ApiError && error.reason === 'unresolved_write') setVersion((n) => n + 1)
          return playerReasonText(error)
        },
      )
      .then((job) => {
        if (job === undefined) return
        attempt.current = null
        setConfirming(false)
        setEnded(null)
        setLost(false)
        setStarted({ id: job.id, number: number ?? 0 })
        onReload()
      })
  }

  const onRunFinished = (done: SendEnded) => {
    setEnded(done)
    setStarted(null)
    setVersion((n) => n + 1)
    onReload()
  }

  const onRunLost = () => {
    setStarted(null)
    setLost(true)
    onReload()
  }

  // Not offered at all while the player is switched off and nothing is connected.
  const offered = player === undefined ? false : player === null || player.status !== 'not_connected' || player.linkEnabled
  return {
    summary,
    player,
    offered,
    confirming,
    setConfirming,
    ended,
    lost,
    version,
    action,
    number,
    run,
    sending,
    busy,
    // Something to show under the header: the confirmation, a run, how one ended, or a refusal.
    active: confirming || run !== null || ended !== null || lost || action.error !== null,
    canSend: current !== null && !busy && !action.busy && run === null,
    press,
    onRunFinished,
    onRunLost,
    onDecided: () => {
      setVersion((n) => n + 1)
      onReload()
    },
  }
}

export type PlayerFlow = ReturnType<typeof useSendToPlayer>
