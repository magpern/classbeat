import { ApiError } from '../../api/client'
import { getClass, getRevision, type ClassSummary } from '../../api/classes'
import { getOverlay, type Overlay } from '../../api/classEdits'
import { getTimeline, type Timeline } from '../../api/studioOutputs'
import { decodeBlocks, type EditorBlock } from '../../lib/editor/blocks'

// Reads everything the editor shows: the class, its current draft's workout, the edit overlay and
// the studio timeline. An edit answers no new content, so this is read again after every edit, undo
// and redo. The overlay answers first: it names the draft an edit would apply to.

/** Why the page is read-only: the page says it in words and offers the way forward that exists. */
export type ReadOnlyReason =
  | 'no_draft'
  | 'open_job'
  /** Neither a studio draft nor a Spotify draft the server can edit (an older Spotify draft has no stored map). */
  | 'edit_unavailable'
  | 'music_changed'
  /** After a studio revise, until the user undoes it or applies the choices (#1423). */
  | 'mix_not_applied'
  | 'no_proposal'
  | 'unsupported'

export interface EditorData {
  summary: ClassSummary
  /** The draft an edit applies to: every edit sends it as `base_revision_id`. */
  revisionId: string
  blocks: EditorBlock[]
  overlay: Overlay
  /** Empty for a Spotify draft: it has no studio mix, so no pieces, cut pins or anchors. */
  timeline: Timeline
  /** A Spotify draft: the workout is edited here, the music is heard in the Spotify audition panel. */
  spotify: boolean
}

const NO_TIMELINE: Timeline = {
  revisionId: '',
  contentHash: '',
  durationSec: 0,
  workoutSec: 0,
  tracks: [],
  hasRepeats: false,
  hints: [],
  crossfades: [],
  intervals: [],
  specDigest: '',
  proposalRevisionId: null,
  isPlain: false,
  choices: null,
}

export type EditorLoad =
  | { kind: 'ready'; data: EditorData }
  | { kind: 'readonly'; reason: ReadOnlyReason; summary: ClassSummary }
  | { kind: 'not_found' }

const KNOWN: readonly string[] = ['edit_unavailable', 'music_changed', 'mix_not_applied', 'no_proposal']

export async function loadEditor(classId: string, signal: AbortSignal): Promise<EditorLoad> {
  let summary: ClassSummary
  try {
    summary = await getClass(classId, signal)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return { kind: 'not_found' }
    throw error
  }
  const readonly = (reason: ReadOnlyReason): EditorLoad => ({ kind: 'readonly', reason, summary })
  if (summary.currentRevisionId === null) return readonly('no_draft')
  if (summary.openJob !== null) return readonly('open_job')
  const overlay = await getOverlay(classId, signal)
  if (overlay.kind === 'none') return { kind: 'not_found' }
  if (overlay.kind === 'unavailable') return readonly(KNOWN.includes(overlay.reason) ? (overlay.reason as ReadOnlyReason) : 'edit_unavailable')
  const [revision, timeline] = await Promise.all([
    getRevision(classId, overlay.overlay.revisionId, signal),
    getTimeline(classId, signal),
  ])
  if (timeline.kind === 'music_changed') return readonly('music_changed')
  // A class with no studio timeline whose overlay is ready is a Spotify draft the server can edit
  // (an older one answers the overlay `edit_unavailable` above): same editor, without the studio parts.
  if (timeline.kind === 'none') return readonly('edit_unavailable')
  const blocks = decodeBlocks(revision.raw)
  if (blocks === null) return readonly('unsupported')
  const spotify = timeline.kind !== 'ready'
  return {
    kind: 'ready',
    data: { summary, revisionId: overlay.overlay.revisionId, blocks, overlay: overlay.overlay, timeline: timeline.kind === 'ready' ? timeline.timeline : NO_TIMELINE, spotify },
  }
}
