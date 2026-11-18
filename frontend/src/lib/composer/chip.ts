import type { ClassSummary } from '../../api/classes'
import { SKETCH } from './workspaceKind'

// The state of a composer class, derived from the current revision and the songs' state (ADR 0038
// section 10.2). Pure. A class is in one of these states:
//
//   rough               a sketch, songs not in yet (a fill runs, or none has started)
//   ready               a song list waits for approval: Approve and save
//   expired             the list ended (expired, cancelled, replaced or failed) for the same sketch: Find songs again
//   playlistNotCreated  phase 2 failed after the approval: Create the playlist again
//   saving              the save of `save_pending` is queued or running, or `save_pending` is set
//   saved               a draft, saved and up to date
//   draft               a draft that is not saved (or changed since): the ordinary Save and Update

export type WorkspaceState = 'rough' | 'ready' | 'expired' | 'playlistNotCreated' | 'saving' | 'saved' | 'draft'

export interface WorkspaceChip {
  state: WorkspaceState
  /** The sketch is current and a fill has ended for it: offer Find songs again. */
  findAgain: boolean
  /** Phase 2 failed for this sketch: offer Create the playlist again. */
  createAgain: boolean
}

export type ChipInput = Pick<
  ClassSummary,
  'currentRevisionId' | 'openJob' | 'saved' | 'songs' | 'composer'
> & {
  /** The current revision's `origin`; null without a version. */
  currentOrigin: string | null
}

function isRunning(openJob: ClassSummary['openJob']): boolean {
  return openJob !== null && (openJob.state === 'queued' || openJob.state === 'running')
}

export function deriveChip(input: ChipInput): WorkspaceChip {
  const { composer, songs, openJob, saved } = input
  const none = { findAgain: false, createAgain: false }
  const saving = composer.savePending !== null || (isRunning(openJob) && openJob?.kind === 'save_workout')
  if (saving) return { state: 'saving', ...none }
  if (input.currentOrigin !== SKETCH) {
    if (input.currentRevisionId === null) return { state: 'rough', ...none }
    return { state: saved.count > 0 && saved.latestUpToDate === true ? 'saved' : 'draft', ...none }
  }
  if (songs.state === 'waiting' && openJob?.state !== 'queued' && openJob?.state !== 'running') {
    return { state: 'ready', ...none }
  }
  const running = isRunning(openJob)
  // The fill belongs to this sketch only: a revise made a new sketch, which has none started.
  const forThis = composer.fill !== null && composer.fill.sketchRevisionId === input.currentRevisionId
  if (!running && forThis && songs.state === 'approved' && composer.phase2 === 'failed') {
    return { state: 'playlistNotCreated', findAgain: false, createAgain: true }
  }
  const ended = !running && forThis && songs.state !== 'approved' && songs.state !== 'waiting'
  const expired = ended && songs.state === 'expired'
  return { state: expired ? 'expired' : 'rough', findAgain: ended, createAgain: false }
}
