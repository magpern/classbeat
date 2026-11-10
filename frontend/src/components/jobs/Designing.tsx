import { designSteps, mixStageLabel, retryLabel, stageLabel, waitingForSlot, waitingToStart, youCanLeave } from '../../api/jobMessages'
import { cancelJob, type Job } from '../../api/jobs'
import { ApiError } from '../../api/client'
import { isSongKind } from '../../api/songs'
import type { Progress } from '../../hooks/useJobProgress'
import { useT } from '../../i18n/I18n'
import { t as translate } from '../../i18n/t'
import { recallInput, recallPick } from '../../lib/submittedInput'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Card as Panel } from '../ui/card'
import { DesigningSteps, stepNumber } from './DesigningSteps'
import { GhostProfile } from './GhostProfile'

function cancelError(error: unknown): string {
  // 409: the design ended on its own meanwhile; the page switches to the result by itself.
  if (error instanceof ApiError && error.status === 409) return translate('job.cancelError.finished')
  return translate('common.didNotWork')
}

/**
 * The designing state: the checklist, the live stage and Stop. On its own page it is the page's
 * main region (`h1`); `embedded` puts it inside the class workspace, under the page's own `h1`.
 */
export function Designing({
  job,
  progress,
  onJob,
  embedded = false,
}: {
  job: Job
  progress: Progress
  onJob: (job: Job) => void
  embedded?: boolean
}) {
  const t = useT()
  const action = useAction()
  const stopping = job.cancelRequested || progress.cancelRequested
  const stop = () => {
    void action.run(async () => {
      onJob(await cancelJob(job.id))
    }, cancelError)
  }
  const waiting = job.state === 'queued' && progress.stage === null
  const picking = isSongKind(job.kind) && job.approvedAt === null
  // The class name comes from the form this browser remembered; without it the title says "your class".
  const name = recallInput(job.id)?.title ?? recallPick(job.id)?.title ?? t('job.yourClass')
  const source = recallInput(job.id)?.music.source
  const steps = designSteps(job.kind, job.approvedAt !== null, source === 'none')
  const current = progress.stage ?? job.stage
  // Only this browser remembers the form; elsewhere the stage keeps its general words.
  const mixStage = source === 'mowl_swedebeat_mix' && current === 'music' && job.kind === 'design_draft'
  const step = stepNumber(steps, current)
  return (
    <Panel
      className={`flex w-full flex-col gap-5 ${embedded ? '' : 'mx-auto max-w-2xl'}`}
      role="region"
      aria-labelledby="designing-title"
    >
      <div className="flex flex-col gap-1">
        {embedded ? (
          <h2 id="designing-title" className="m-0 font-display text-2xl font-extrabold">
            {picking ? t('job.findingSongs', { name }) : t('job.designing', { name })}
          </h2>
        ) : (
          <h1 id="designing-title" className="m-0 font-display text-3xl font-extrabold">
            {picking ? t('job.findingSongs', { name }) : t('job.designing', { name })}
          </h1>
        )}
        {step !== null ? (
          <p className="m-0 text-muted tabular-nums">
            {t('job.stepOf', { step, total: steps.length })}
          </p>
        ) : null}
      </div>
      <GhostProfile />
      <DesigningSteps steps={steps} current={current} />
      <div role="status" aria-live="polite" data-testid="progress" className="flex flex-col gap-1">
        {stopping ? (
          <p className="m-0">{t('job.stopping')}</p>
        ) : progress.retry !== null ? (
          <p className="m-0">{retryLabel(progress.retry)}</p>
        ) : progress.waitingForSlot && job.state === 'queued' ? (
          <p className="m-0">{waitingForSlot()}</p>
        ) : waiting ? (
          <p className="m-0">{waitingToStart()}</p>
        ) : mixStage ? (
          <p className="m-0">{mixStageLabel()}</p>
        ) : (
          <p className="m-0">{stageLabel(current)}</p>
        )}
        {progress.operation === 'pending' ? <p className="m-0">{t('job.addingPlaylist')}</p> : null}
        {progress.toolCalls > 0 ? (
          <p className="m-0 text-muted">{t('job.toolCalls', { count: progress.toolCalls })}</p>
        ) : null}
      </div>
      <p className="m-0 text-muted">{youCanLeave()}</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <p className="m-0">
        <Button variant="secondary" disabled={action.busy || stopping} onClick={stop}>
          {t('job.stop')}
        </Button>
      </p>
    </Panel>
  )
}
