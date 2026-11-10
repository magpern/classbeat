import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import type { ClassSummary } from '../../api/classes'
import { designFromInterpretation, getInterpretation } from '../../api/interpret'
import { useT } from '../../i18n/I18n'
import { recallRequest, startAgainPath } from '../../lib/startRequest'
import { Link, useRouter } from '../../router/Router'
import { Notice } from '../forms'

type State = 'waiting' | 'starting' | 'refused' | 'failed' | 'error' | 'quiet'

const POLL_MS = 2000

/**
 * The minimal hand-off of a class started from the free-text start screen (MA2; the workspace
 * around it is MA3). While the `interpret_request` job runs it waits; once the reading is stored it
 * asks the server to design from it (`design-from-interpretation`, idempotent: a reopened tab gets
 * the same job) and follows that job on its page. A refusal or a failed reading says so and offers
 * "Try again", which opens the start screen with the same request.
 */
export function HandoffNotice({ summary, reload }: { summary: ClassSummary; reload: () => void }) {
  // Only a class with no version whose open job, if any, is the reading: a design in progress has its own notice.
  if (summary.currentRevisionId !== null || (summary.openJob !== null && summary.openJob.kind !== 'interpret_request')) return null
  return <Handoff classId={summary.id} interpreting={summary.openJob !== null} reload={reload} />
}

function Handoff({ classId, interpreting, reload }: { classId: string; interpreting: boolean; reload: () => void }) {
  const t = useT()
  const { navigate } = useRouter()
  const [state, setState] = useState<State>('waiting')
  // Read inside the polling loop: the page reloads the class while it waits, and the loop must not restart.
  const stillReading = useRef(interpreting)
  useEffect(() => {
    stillReading.current = interpreting
  }, [interpreting])

  useEffect(() => {
    const abort = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const step = async () => {
      try {
        const found = await getInterpretation(classId, abort.signal)
        if (abort.signal.aborted) return
        if (found.designJobId !== null) {
          navigate(`/jobs/${found.designJobId}`)
        } else if (found.refusal !== null) {
          setState('refused')
        } else {
          setState('starting')
          const jobId = await designFromInterpretation(classId)
          if (!abort.signal.aborted) navigate(`/jobs/${jobId}`)
        }
      } catch (error) {
        if (abort.signal.aborted) return
        if (error instanceof ApiError && error.reason === 'not_ready') {
          if (stillReading.current) {
            setState('waiting')
            timer = setTimeout(() => {
              reload()
              void step()
            }, POLL_MS)
          } else {
            // No reading was stored and no job is running: it failed. A class not made on the start screen says nothing.
            setState(recallRequest(classId) === null ? 'quiet' : 'failed')
          }
        } else if (error instanceof ApiError && error.status === 409) {
          setState('refused')
        } else {
          setState('error')
        }
      }
    }
    void step()
    return () => {
      abort.abort()
      clearTimeout(timer)
    }
  }, [classId, navigate, reload])

  if (state === 'quiet') return null
  const sad = state === 'refused' || state === 'failed' || state === 'error'
  return (
    <Notice kind={sad ? 'error' : undefined}>
      {state === 'waiting' ? t('start.handoff.waiting') : null}
      {state === 'starting' ? t('start.handoff.starting') : null}
      {state === 'refused' ? t('start.handoff.refused') : null}
      {state === 'failed' ? t('start.handoff.failed') : null}
      {state === 'error' ? t('start.handoff.error') : null}
      {sad ? (
        <>
          {' '}
          <Link to={startAgainPath(classId)}>{t('start.handoff.tryAgain')}</Link>
        </>
      ) : null}
    </Notice>
  )
}
