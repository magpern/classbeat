import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import { readJobEvents, type JobEvent } from '../api/jobEvents'
import { getJob, isTerminal, type Job } from '../api/jobs'

/** What the Designing page shows, folded from the job's events (ADR 0005 section 6). */
export interface Progress {
  stage: string | null
  toolCalls: number
  attempt: number
  /** The category of a scheduled retry, until the next attempt starts. */
  retry: string | null
  /** State of the newest external operation event (the playlist attach). */
  operation: string | null
  cancelRequested: boolean
}

const INITIAL: Progress = {
  stage: null,
  toolCalls: 0,
  attempt: 0,
  retry: null,
  operation: null,
  cancelRequested: false,
}

export function foldEvent(progress: Progress, event: JobEvent): Progress {
  const { payload } = event
  switch (event.kind) {
    case 'claimed':
      return {
        ...progress,
        retry: null,
        attempt: typeof payload.attempt === 'number' ? payload.attempt : progress.attempt,
      }
    case 'stage_started':
      return {
        ...progress,
        stage: typeof payload.stage === 'string' ? payload.stage : progress.stage,
      }
    case 'tool_called':
      return { ...progress, toolCalls: progress.toolCalls + 1 }
    case 'retry_scheduled':
      return { ...progress, retry: typeof payload.category === 'string' ? payload.category : '' }
    case 'external_operation':
      return { ...progress, operation: typeof payload.state === 'string' ? payload.state : null }
    case 'cancel_requested':
      return { ...progress, cancelRequested: true }
    default:
      return progress
  }
}

const MIN_WAIT_MS = 1000
/** The least pause before a stream is opened again, so a stream that keeps ending cannot spin. */
const RECONNECT_FLOOR_MS = 250
const MAX_WAIT_MS = 10000

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms)
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer)
        resolve()
      },
      { once: true },
    )
  })
}

export interface JobProgress {
  job: Job | null
  progress: Progress
  /** `not_found` for an unknown or foreign id, `failed` when the job cannot be read. */
  problem: 'not_found' | 'signed_out' | 'failed' | null
  /** Replaces the shown job (after the page cancelled it, say), unless it is already over. */
  setJob: (job: Job) => void
  /** Follows the job again after it left a waiting state (the approval was accepted). */
  follow: () => void
}

/**
 * Follows one job: reads its state, streams its events from the last one seen, and when a stream
 * ends reads the state again before it opens another. It stops for good once the job is over, on
 * an event that ends the stream (so no reconnect after a terminal event, #116) and when the page
 * unmounts. A broken stream is retried with a growing pause; the job state is the truth.
 */
export function useJobProgress(jobId: string): JobProgress {
  const [job, setJobState] = useState<Job | null>(null)
  const [problem, setProblem] = useState<JobProgress['problem']>(null)
  const [progress, dispatch] = useReducer(foldEvent, INITIAL)
  const [round, setRound] = useState(0)
  // The newest event seen survives a restart, so no event is folded twice.
  const seen = useRef(0)

  useEffect(() => {
    const abort = new AbortController()
    const { signal } = abort
    void (async () => {
      let after = seen.current
      let wait = MIN_WAIT_MS
      while (!signal.aborted) {
        let current: Job
        try {
          current = await getJob(jobId, signal)
        } catch (error) {
          if (signal.aborted) return
          if (error instanceof ApiError && error.status === 404) {
            setProblem('not_found')
            return
          }
          if (error instanceof ApiError && error.status === 401) {
            setProblem('signed_out')
            return
          }
          setProblem('failed')
          await pause(wait, signal)
          wait = Math.min(wait * 2, MAX_WAIT_MS)
          continue
        }
        setProblem(null)
        setJobState(current)
        if (isTerminal(current.state) || current.state === 'waiting_for_approval') return
        let received = 0
        let ended: boolean
        try {
          ended = await readJobEvents(jobId, after, signal, (event) => {
            after = event.seq
            seen.current = event.seq
            received += 1
            dispatch(event)
          })
        } catch (error) {
          if (signal.aborted) return
          if (error instanceof ApiError && error.status === 404) {
            setProblem('not_found')
            return
          }
          if (error instanceof ApiError && error.status === 401) {
            setProblem('signed_out')
            return
          }
          // A 429 or a dropped connection: wait, then read the state and try again.
          const hint = error instanceof ApiError && error.retryAfter !== null ? error.retryAfter * 1000 : 0
          await pause(Math.max(wait, hint), signal)
          wait = Math.min(wait * 2, MAX_WAIT_MS)
          continue
        }
        if (ended) continue // the job is over or waits: read its state, no new stream
        if (received > 0) {
          wait = MIN_WAIT_MS
          await pause(RECONNECT_FLOOR_MS, signal)
        } else {
          await pause(wait, signal)
          wait = Math.min(wait * 2, MAX_WAIT_MS)
        }
      }
    })()
    return () => {
      abort.abort()
    }
  }, [jobId, round])

  // A late answer (the cancel call, say) never replaces a state the stream already settled.
  const setJob = useCallback((next: Job) => {
    setJobState((previous) => (previous !== null && isTerminal(previous.state) ? previous : next))
  }, [])

  const follow = useCallback(() => {
    setRound((n) => n + 1)
  }, [])

  return { job, progress, problem, setJob, follow }
}
