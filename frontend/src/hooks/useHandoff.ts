import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import type { ClassSummary } from '../api/classes'
import { designFromInterpretation, getInterpretation, rereadClass, type RereadBody } from '../api/interpret'
import type { InterpretationItem } from '../api/interpretationItems'
import { getJob } from '../api/jobs'
import { NO_EXTRAS, type ReadingExtras } from '../api/readingExtras'
import { heldNeed, type Need } from '../lib/planUpgrade'
import { recallRequest } from '../lib/startRequest'

export type HandoffPhase = 'idle' | 'reading' | 'confirm' | 'choose' | 'starting' | 'refused' | 'failed' | 'error'

/** What the confirm screen does with the reading it shows (#1538). */
export interface Confirmation {
  /** The reading the questions belong to: the re-read and the hand-off name it, so a stale tab is refused. */
  interpretJobId: string
  /** "Design this class": agrees to the reading as it is; only then is anything designed or fetched. */
  onDesign: () => void
  /** "Update the reading": answers that change it read the request again (no class credit); resolves with the refusal, null when the new reading is on its way. */
  onReread: (body: RereadBody) => Promise<unknown>
  /** The last re-read ended without a new reading: the earlier one is kept. */
  rereadFailed: boolean
}

export interface Handoff {
  phase: HandoffPhase
  /** How the request was read, once the reading is stored (also after a refusal); null before. */
  items: InterpretationItem[] | null
  /** The composer's pinned songs and questions (ADR 0038 section 3); empty for any other class. */
  extras: ReadingExtras
  /** The `interpret_request` job that read the request: the class's first message belongs to it. */
  interpretJobId: string | null
  /** The stored refusal's reason code (`not_connected`, say); null when none or not coded. */
  refusal: string | null
  /** Set in phase `choose`: what the reading asked for that the plan does not allow. */
  held: Need | null
  /** "Design within my plan": sends the design hand-off the reading held back. */
  designWithinPlan: () => void
  /** Set in phase `confirm` only: what the reading's questions do (see `Confirmation`). */
  confirmation: Confirmation | null
  /** Asks again, after an `error`. */
  retry: () => void
}

const POLL_MS = 2000

/**
 * The web-side hand-off of a class started from the free-text start screen (ADR 0029 section 5;
 * MA2 opened it, MA3 draws it in the workspace). It runs only for a class with no version that is
 * not being designed: while the `interpret_request` job runs it waits and reloads the class; once
 * the reading is stored it **waits for the instructor** (phase `confirm`, #1538: nothing is
 * designed or fetched until they agree, and a reopened tab waits again). `confirm()` then asks the
 * server to design from the reading they saw (`design-from-interpretation` with its
 * `interpret_job_id`, idempotent, so a reopened tab gets the same job) and reloads, so the class
 * then shows its designing state in place. `reread()` sends answers that change the reading and
 * waits for the new one. A stored refusal is answered again until the instructor acts.
 */
export function useHandoff(summary: ClassSummary, reload: () => void): Handoff {
  const classId = summary.id
  // Only a class with no version whose open job, if any, is the reading: a design in progress is shown as such.
  const active =
    summary.currentRevisionId === null &&
    (summary.openJob === null || summary.openJob.kind === 'interpret_request')
  const interpreting = summary.openJob !== null
  const [phase, setPhase] = useState<HandoffPhase>('idle')
  const [items, setItems] = useState<InterpretationItem[] | null>(null)
  const [extras, setExtras] = useState<ReadingExtras>(NO_EXTRAS)
  const [interpretJobId, setInterpretJobId] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [round, setRound] = useState(0)
  const [held, setHeld] = useState<Need | null>(null)
  const [rereadFailed, setRereadFailed] = useState(false)
  // Set when the instructor agreed to the reading (this tab only: a reopened tab waits again).
  const confirmed = useRef(false)
  // Set when the instructor chose "design within my plan": the hand-off is then sent as usual.
  const chose = useRef(false)
  // The job of a re-read whose reading is awaited; the reading shown stays the old one until it is stored.
  const rereadJob = useRef<string | null>(null)
  // Read inside the polling loop: the page reloads the class while it waits, and the loop must not restart.
  const stillReading = useRef(interpreting)
  const reloadRef = useRef(reload)
  useEffect(() => {
    stillReading.current = interpreting
    reloadRef.current = reload
  }, [interpreting, reload])

  useEffect(() => {
    if (!active) return
    const abort = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const step = async () => {
      try {
        const found = await getInterpretation(classId, abort.signal)
        if (abort.signal.aborted) return
        if (rereadJob.current !== null && found.interpretJobId === rereadJob.current) rereadJob.current = null
        let waiting = stillReading.current // a reopened tab while a re-read runs: the old reading is about to be replaced
        if (rereadJob.current !== null) {
          // The new reading is not stored yet: the old one is not shown (its questions are no longer asked).
          const job = await getJob(rereadJob.current, abort.signal)
          if (abort.signal.aborted) return
          waiting = job.state === 'queued' || job.state === 'running'
          if (!waiting) {
            rereadJob.current = null // ended with no new reading: the earlier one stands
            setRereadFailed(true)
          }
        }
        if (waiting && found.designJobId === null) {
          setPhase('reading')
          timer = setTimeout(() => {
            reloadRef.current()
            void step()
          }, POLL_MS)
          return
        }
        setItems(found.items)
        setExtras(found.extras)
        setInterpretJobId(found.interpretJobId)
        if (found.designJobId !== null) {
          // Started already (another tab, or before a reload): the class shows its job.
          setPhase('idle')
          reloadRef.current()
        } else if (found.refusal !== null) {
          setRefusal(found.refusal)
          setPhase('refused')
        } else if (!confirmed.current) {
          // Nothing is designed or fetched until the instructor agrees to the reading (#1538).
          setPhase('confirm')
        } else if (!chose.current && heldNeed(found.items) !== null) {
          // Over the plan: nothing is sent (the design job takes the class credit) until the instructor chooses.
          setHeld(heldNeed(found.items))
          setPhase('choose')
        } else {
          setPhase('starting')
          await designFromInterpretation(classId, found.interpretJobId)
          if (abort.signal.aborted) return
          setPhase('idle')
          reloadRef.current()
        }
      } catch (error) {
        if (abort.signal.aborted) return
        if (error instanceof ApiError && error.reason === 'not_ready') {
          if (stillReading.current) {
            setPhase('reading')
            timer = setTimeout(() => {
              reloadRef.current()
              void step()
            }, POLL_MS)
          } else {
            // No reading was stored and no job is running: it failed. A class not made on the start screen says nothing.
            setPhase(recallRequest(classId) === null ? 'idle' : 'failed')
          }
        } else if (error instanceof ApiError && error.status === 409) {
          // The refusal's own code, so a fresh `not_connected` offers Interpret again at once, as a stored one does.
          setRefusal(error.reason)
          setPhase('refused')
        } else {
          setPhase('error')
        }
      }
    }
    void step()
    return () => {
      abort.abort()
      clearTimeout(timer)
    }
  }, [active, classId, round])

  const retry = useCallback(() => {
    setPhase('reading')
    setRound((n) => n + 1)
  }, [])
  const designWithinPlan = useCallback(() => {
    chose.current = true
    setPhase('starting')
    setRound((n) => n + 1)
  }, [])
  const confirm = useCallback(() => {
    confirmed.current = true
    setRereadFailed(false)
    setPhase('starting')
    setRound((n) => n + 1)
  }, [])
  const reread = useCallback(
    async (body: RereadBody): Promise<unknown> => {
      try {
        rereadJob.current = await rereadClass(classId, body)
      } catch (error) {
        return error
      }
      setRereadFailed(false)
      setItems(null)
      setExtras(NO_EXTRAS)
      setPhase('reading')
      reloadRef.current()
      setRound((n) => n + 1)
      return null
    },
    [classId],
  )
  const confirmation =
    active && phase === 'confirm' && interpretJobId !== null
      ? { interpretJobId, onDesign: confirm, onReread: reread, rereadFailed }
      : null
  return { phase: active ? phase : 'idle', items, extras, interpretJobId, refusal, held, designWithinPlan, confirmation, retry }
}
