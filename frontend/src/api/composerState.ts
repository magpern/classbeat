import { isObject, optionalCount, optionalText, type JsonObject } from './decode'

// The composer flow's part of a class answer (ADR 0038 sections 5, 7 and 8). The backend builds of
// #1188 and #1189 fix the final shapes; these are the minimal ones the web needs, and every key is
// optional so a class without the flag, or a server without them, reads as the empty state:
//
//   fill_songs:   { sketch_revision_id, attempt, failure }  the fill started for a sketch (ADR section 5):
//                 `failure` is the stored refusal or failure code, null while it runs or waits;
//   save_pending: { revision_id, content_hash, job_id }     the draft phase 2 stored and the phase-2
//                 job that asked for the save (ADR section 7);
//   phase2:       { state }                                  the last phase-2 job's state, so a failed
//                 one reads "The playlist was not created" (ADR section 7).

export interface FillState {
  /** The sketch revision the fill was started for. */
  sketchRevisionId: string
  /** The next retry uses `attempt + 1` (the idempotency key's third part). */
  attempt: number
  /** The stored refusal or failure code (`quota`, `invalid_output`, ...); null when none. */
  failure: string | null
}

export interface SavePending {
  revisionId: string
  contentHash: string
  /** The phase-2 job: the save names it as `save_pending_job_id`, and its key derives from it. */
  jobId: string
}

export interface ComposerState {
  fill: FillState | null
  savePending: SavePending | null
  /** `failed` when the newest phase-2 job of the class failed; null otherwise. */
  phase2: 'failed' | null
}

export const NO_COMPOSER_STATE: ComposerState = { fill: null, savePending: null, phase2: null }

function fillOf(value: unknown): FillState | null {
  if (!isObject(value)) return null
  const sketch = optionalText(value, 'sketch_revision_id')
  if (sketch === null) return null
  return { sketchRevisionId: sketch, attempt: optionalCount(value, 'attempt') ?? 0, failure: optionalText(value, 'failure') }
}

function savePendingOf(value: unknown): SavePending | null {
  if (!isObject(value)) return null
  const revisionId = optionalText(value, 'revision_id')
  const contentHash = optionalText(value, 'content_hash')
  const jobId = optionalText(value, 'job_id')
  return revisionId === null || contentHash === null || jobId === null ? null : { revisionId, contentHash, jobId }
}

export function decodeComposerState(item: JsonObject): ComposerState {
  const phase2 = isObject(item.phase2) ? item.phase2.state : null
  return {
    fill: fillOf(item.fill_songs),
    savePending: savePendingOf(item.save_pending),
    phase2: phase2 === 'failed' ? 'failed' : null,
  }
}
