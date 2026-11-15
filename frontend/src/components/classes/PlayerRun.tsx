import { useEffect, useRef } from 'react'
import { retryLabel } from '../../api/jobMessages'
import { cancelJob, isTerminal } from '../../api/jobs'
import { useJobProgress } from '../../hooks/useJobProgress'
import { useT } from '../../i18n/I18n'
import { Notice, useAction } from '../forms'
import { BeatBars } from '../ui/beat-bars'
import { Button } from '../ui/button'
import type { SendEnded } from './useSendToPlayer'

/** The live progress of the class's running send to the player, from the job's events. */
export function PlayerRun({
  jobId,
  number,
  onFinished,
  onLost,
}: {
  jobId: string
  /** The version this send carries. */
  number: number
  onFinished: (done: SendEnded) => void
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
      onFinished({ job, number })
    }
  }, [job, over, number, onFinished])

  const lost = useRef(false)
  useEffect(() => {
    if (problem === 'not_found' && !lost.current) {
      lost.current = true
      onLost()
    }
  }, [problem, onLost])

  if (problem === 'not_found') return null
  const stopping = job?.cancelRequested === true || progress.cancelRequested
  return (
    <div className="chat__run" role="status" aria-live="polite" data-testid="player-progress">
      {problem === 'failed' ? <p>{t('class.runDropped')}</p> : null}
      {stopping ? (
        <p>{t('class.runStopping')}</p>
      ) : progress.retry !== null ? (
        <p>{retryLabel(progress.retry)}</p>
      ) : job === null || (job.state === 'queued' && progress.stage === null) ? (
        <p>{t('class.runWaiting')}</p>
      ) : (
        <p>
          <BeatBars />
          {t('player.send.run')}
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
