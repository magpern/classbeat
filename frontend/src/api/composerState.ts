import { isObject, optionalCount, optionalText, type JsonObject } from './decode'

// The composer flow's part of a class answer (ADR 0038 sections 5, 7 and 8). Every key is optional
// so a class without the flag reads as the empty state:
//
//   composer:     boolean   the class was read with the composer flow on (#1188; `classes/composer_view.py`);
//   fill_songs:   { sketch_revision_id, attempt, failure }  the fill of the current sketch (#1188):
//                 `failure` is the stored refusal, the error category of the failed job, `cancelled`
//                 or `expired`; null while it runs or waits;
//   save_pending: { revision_id, content_hash, job_id }     the draft phase 2 stored and the phase-2
//                 job that asked for the save (ADR section 7, #1189); null once a newer revision exists;
//   phase2:       { state: "failed" | "differed" }           `failed`: the newest approved list can be
//                 resumed and its sketch is still current ("The playlist was not created"); `differed`:
//                 the current draft was re-designed on the final songs and is not saved by itself; it
//                 stays while that draft is current, so the page hides it once the class is saved (#1189).

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
  /** See the top of the file; null when neither applies. */
  phase2: 'failed' | 'differed' | null
  /** The class answer's `composer`: the interpret job was admitted with the flag on (#1188). */
  composer: boolean
}

export const NO_COMPOSER_STATE: ComposerState = { fill: null, savePending: null, phase2: null, composer: false }

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
    phase2: phase2 === 'failed' || phase2 === 'differed' ? phase2 : null,
    composer: item.composer === true,
  }
}
