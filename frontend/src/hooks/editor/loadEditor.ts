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
  /** An older Spotify draft: it has no stored map to check an edit against (#1424). */
  | 'edit_unavailable'
  /** Anything else the editor cannot edit (a soundless or running class, a shape it does not know). */
  | 'not_editable'
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

const KNOWN: readonly string[] = ['music_changed', 'mix_not_applied', 'no_proposal']

/** Only a cycling class with music can be an older Spotify draft: the sentence for it is true only then. */
function olderSpotify(summary: ClassSummary): boolean {
  return !summary.soundless && !summary.studio && summary.sport === 'cycling'
}

/** The reads are not one snapshot: another tab can move the class between the overlay and the timeline. */
const MAX_READS = 3

/** Reads again while the timeline is of another draft than the overlay; a read that never agrees fails (the page offers a retry). */
export async function loadEditor(classId: string, signal: AbortSignal): Promise<EditorLoad> {
  for (let read = 0; read < MAX_READS; read += 1) {
    const found = await readOnce(classId, signal)
    if (found !== 'moved') return found
  }
  throw new Error('the class kept moving while it was read')
}

async function readOnce(classId: string, signal: AbortSignal): Promise<EditorLoad | 'moved'> {
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
  if (overlay.kind === 'unavailable') {
    if (KNOWN.includes(overlay.reason)) return readonly(overlay.reason as ReadOnlyReason)
    return readonly(overlay.reason === 'edit_unavailable' && olderSpotify(summary) ? 'edit_unavailable' : 'not_editable')
  }
  const [revision, timeline] = await Promise.all([
    getRevision(classId, overlay.overlay.revisionId, signal),
    getTimeline(classId, signal),
  ])
  if (timeline.kind === 'music_changed') return readonly('music_changed')
  // A class with no studio timeline whose overlay is ready is a Spotify draft the server can edit
  // (an older one answers the overlay `edit_unavailable` above): same editor, without the studio parts.
  if (timeline.kind === 'none') return readonly('not_editable')
  if (timeline.kind === 'ready' && timeline.timeline.revisionId !== overlay.overlay.revisionId) return 'moved'
  const blocks = decodeBlocks(revision.raw)
  if (blocks === null) return readonly('unsupported')
  const spotify = timeline.kind !== 'ready'
  return {
    kind: 'ready',
    data: { summary, revisionId: overlay.overlay.revisionId, blocks, overlay: overlay.overlay, timeline: timeline.kind === 'ready' ? timeline.timeline : NO_TIMELINE, spotify },
  }
}
