import { useEffect, useRef } from 'react'
import type { ClassSummary } from '../../api/classes'
import { isTerminal } from '../../api/jobs'
import { useJobProgress } from '../../hooks/useJobProgress'
import { useT } from '../../i18n/I18n'
import type { CatalogueKey } from '../../i18n/en'
import { SKETCH } from '../../lib/composer/workspaceKind'
import { BeatBars } from '../ui/beat-bars'

type OpenJob = NonNullable<ClassSummary['openJob']>

/** The plain words for the job that runs (ADR 0038 section 10.2: "one status line while a job runs"). */
export function statusKey(job: OpenJob, songsApproved: boolean, currentOrigin: string | null): CatalogueKey {
  switch (job.kind) {
    case 'interpret_request':
      return 'cw.status.reading'
    case 'design_draft':
      return 'cw.status.sketching'
    case 'revise_draft':
      return currentOrigin === SKETCH || currentOrigin === null ? 'cw.status.sketching' : 'cw.status.revising'
    case 'design_with_songs':
    case 'revise_songs':
      return songsApproved ? 'cw.status.phase2' : 'cw.status.songs'
    case 'save_workout':
      return 'cw.status.saving'
    case 'update_workout':
      return 'cw.status.updating'
    default:
      return 'cw.status.other'
  }
}

/**
 * One status line for the job that runs. It follows the job's events and reads the class again
 * when the job ends, which is what lets the driver take its next step.
 */
export function JobStatusLine({
  job,
  songsApproved,
  currentOrigin,
  onFinished,
}: {
  job: OpenJob
  songsApproved: boolean
  currentOrigin: string | null
  onFinished: () => void
}) {
  const t = useT()
  const { job: followed } = useJobProgress(job.id)
  const over = followed !== null && isTerminal(followed.state)
  const told = useRef(false)
  useEffect(() => {
    if (over && !told.current) {
      told.current = true
      onFinished()
    }
  }, [over, onFinished])
  const songs = job.kind === 'design_with_songs' && !songsApproved
  return (
    <div role="status" aria-live="polite" data-testid="job-status" className="flex flex-col gap-1 text-[0.9375rem] text-muted">
      <p className="m-0 flex items-center gap-2">
        <BeatBars />
        {t(statusKey(job, songsApproved, currentOrigin))}
      </p>
      {songs ? <p className="m-0 text-sm">{t('cw.status.songsHint')}</p> : null}
    </div>
  )
}
