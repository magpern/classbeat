import type { ReactNode } from 'react'
import type { ClassSummary } from '../../api/classes'
import type { DraftSegment } from '../../api/draftTypes'
import type { Song } from '../../api/songs'
import type { WorkspaceChip } from '../../lib/composer/chip'
import { useT } from '../../i18n/I18n'
import { lengthLabel } from '../../lib/classCards'
import { totalSeconds } from '../../lib/workout'
import { Notice } from '../forms'
import { ProfileChart } from '../classes/ProfileChart'
import { Button } from '../ui/button'
import { JobStatusLine } from './JobStatusLine'
import { SegmentList } from './SegmentList'
import { StateChip } from './StateChip'

export interface ClassColumnProps {
  summary: ClassSummary
  title: string
  chip: WorkspaceChip & { state: Parameters<typeof StateChip>[0]['state'] }
  segments: readonly DraftSegment[] | null
  songs: readonly Song[] | null
  hasList: boolean
  attached: number
  currentOrigin: string | null
  /** The More menu, in the title line. */
  menu: ReactNode
  /** Everything under the segments: notices, the approve bar or the save buttons, the More panel. */
  below: ReactNode
  /** A job of the class is queued or running. */
  runningJob: boolean
  canSwap: boolean
  swapping: boolean
  onSwap: (song: Song) => void
  onFinished: () => void
  onFindAgain: () => void
  onCreateAgain: () => void
  /** The error of the last Find songs again or Create the playlist again. */
  actionError: string | null
  actionBusy: boolean
}

/**
 * The class, right (ADR 0038 section 10.2): the title line with its state chip, one status line
 * while a job runs, the profile, the segments with their songs, and what belongs under them.
 */
export function ClassColumn(p: ClassColumnProps) {
  const t = useT()
  const job = p.summary.openJob
  const seconds = p.segments === null ? 0 : totalSeconds(p.segments as DraftSegment[])
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-1 basis-64 flex-col gap-2">
          <h1 className="m-0 font-display text-3xl font-extrabold break-words">{p.title}</h1>
          <div className="flex flex-wrap items-center gap-2">
            {seconds > 0 ? <span className="text-muted">{lengthLabel(seconds)}</span> : null}
            <StateChip state={p.chip.state} summary={p.summary} />
          </div>
        </div>
        {p.menu}
      </div>
      {p.runningJob && job !== null ? (
        <JobStatusLine
          key={job.id}
          job={job}
          songsApproved={p.summary.songs.state === 'approved'}
          currentOrigin={p.currentOrigin}
          onFinished={p.onFinished}
        />
      ) : null}
      {p.chip.findAgain || p.chip.createAgain ? (
        <div className="flex flex-wrap items-center gap-3">
          {p.summary.composer.fill?.failure != null && p.chip.findAgain ? (
            <p className="m-0 text-[0.9375rem] text-muted">{t('cw.fill.failed')}</p>
          ) : null}
          <Button disabled={p.actionBusy} onClick={p.chip.createAgain ? p.onCreateAgain : p.onFindAgain}>
            {p.chip.createAgain ? t('cw.createAgain') : t('cw.findAgain')}
          </Button>
        </div>
      ) : null}
      {p.actionError !== null ? <Notice kind="error">{p.actionError}</Notice> : null}
      {p.segments !== null && p.segments.length > 0 ? (
        <>
          <ProfileChart segments={p.segments as DraftSegment[]} title={p.title} songs={null} />
          <SegmentList
            segments={p.segments}
            songs={p.songs}
            hasList={p.hasList}
            attached={p.attached}
            canSwap={p.canSwap}
            swapping={p.swapping}
            onSwap={p.onSwap}
          />
        </>
      ) : null}
      {p.below}
    </div>
  )
}
