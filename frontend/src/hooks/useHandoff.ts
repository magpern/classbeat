import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import type { ClassSummary } from '../api/classes'
import { designFromInterpretation, getInterpretation } from '../api/interpret'
import type { InterpretationItem } from '../api/interpretationItems'
import { recallRequest } from '../lib/startRequest'

export type HandoffPhase = 'idle' | 'reading' | 'starting' | 'refused' | 'failed' | 'error'

export interface Handoff {
  phase: HandoffPhase
  /** How the request was read, once the reading is stored (also after a refusal); null before. */
  items: InterpretationItem[] | null
  /** The `interpret_request` job that read the request: the class's first message belongs to it. */
  interpretJobId: string | null
  /** The stored refusal's reason code (`not_connected`, say); null when none or not coded. */
  refusal: string | null
  /** Asks again, after an `error`. */
  retry: () => void
}

const POLL_MS = 2000

/**
 * The web-side hand-off of a class started from the free-text start screen (ADR 0029 section 5;
 * MA2 opened it, MA3 draws it in the workspace). It runs only for a class with no version that is
 * not being designed: while the `interpret_request` job runs it waits and reloads the class; once
 * the reading is stored it asks the server to design from it (`design-from-interpretation`,
 * idempotent, so a reopened tab gets the same job) and reloads, so the class then shows its
 * designing state in place. A stored refusal is answered again until the instructor acts.
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
  const [interpretJobId, setInterpretJobId] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [round, setRound] = useState(0)
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
        setItems(found.items)
        setInterpretJobId(found.interpretJobId)
        if (found.designJobId !== null) {
          // Started already (another tab, or before a reload): the class shows its job.
          setPhase('idle')
          reloadRef.current()
        } else if (found.refusal !== null) {
          setRefusal(found.refusal)
          setPhase('refused')
        } else {
          setPhase('starting')
          await designFromInterpretation(classId)
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
  return { phase: active ? phase : 'idle', items, interpretJobId, refusal, retry }
}
