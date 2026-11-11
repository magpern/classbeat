import { useEffect, useRef } from 'react'
import { stageLabel, retryLabel } from '../../api/jobMessages'
import { cancelJob, isTerminal, type Job } from '../../api/jobs'
import { needsAccount, saveFailureText, savedText, updatedText, type WriteKind } from '../../api/saveMessages'
import { useT } from '../../i18n/I18n'
import { useJobProgress } from '../../hooks/useJobProgress'
import { Link } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { BeatBars } from '../ui/beat-bars'
import { Button } from '../ui/button'
import { Cheer } from '../ui/cheer'
import type { Finished } from './useSaveFlow'

/** The live progress of the class's running save or update, from the job's events. */
export function WriteRun({
  jobId,
  kind,
  onFinished,
  onLost,
}: {
  jobId: string
  kind: WriteKind
  onFinished: (done: { kind: WriteKind; job: Job }) => void
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
      onFinished({ kind, job })
    }
  }, [job, over, kind, onFinished])

  const lost = useRef(false)
  useEffect(() => {
    if (problem === 'not_found' && !lost.current) {
      lost.current = true
      onLost()
    }
  }, [problem, onLost])

  // The page shows the sentence (`lost`) and drops this progress, so it does not stay after a reload.
  if (problem === 'not_found') return null
  const stopping = job?.cancelRequested === true || progress.cancelRequested
  return (
    <div className="chat__run" role="status" aria-live="polite" data-testid="save-progress">
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
          {kind === 'save' ? t('class.save.runSave') : t('class.save.runUpdate')}{' '}
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
        {t('class.save.stop')}
      </Button>
    </div>
  )
}

export function Outcome({
  done,
  soundless,
  onUpdateAnyway,
}: {
  done: Finished
  soundless: boolean
  onUpdateAnyway: () => void
}) {
  const t = useT()
  const { job, kind } = done
  if (job.state === 'completed') {
    if (kind === 'save') {
      return (
        <Cheer>
          <Notice kind="success">{savedText(soundless)}</Notice>
        </Cheer>
      )
    }
    if (done.verified === 'checking') return <Notice>{t('class.save.checking')}</Notice>
    return <Notice kind={done.verified ? 'success' : 'info'}>{updatedText(done.verified)}</Notice>
  }
  if (job.state === 'failed' && job.errorCategory === 'uncertain_write') {
    return (
      <Notice kind="error">{t('class.save.uncertain')}</Notice>
    )
  }
  if (job.state === 'failed') {
    return (
      <div className="flex flex-col gap-3">
        <Notice kind="error">
          {saveFailureText(job.errorCategory)}{' '}
          {needsAccount(job.errorCategory) ? <Link to="/account">{t('class.goToAccount')}</Link> : null}
        </Notice>
        {kind === 'update' && job.errorCategory === 'program_changed' ? (
          <p>
            <Button variant="secondary" onClick={onUpdateAnyway}>
              {t('class.save.updateAnyway')}
            </Button>
          </p>
        ) : null}
      </div>
    )
  }
  return <Notice>{t('class.save.stopped')}</Notice>
}

export function ConfirmButtons({
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
  const t = useT()
  return (
    <>
      <Button  disabled={busy} onClick={onConfirm}>
        {label}
      </Button>
      <Button variant="secondary" disabled={busy} onClick={onCancel}>
        {t('class.cancel')}
      </Button>
    </>
  )
}
