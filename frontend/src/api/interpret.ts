import { get, post } from './client'
import { decodeJob, type Job } from './jobs'
import { isObject, object, optionalText, text } from './decode'

// The free-text start (ADR 0029 section 5, backend/src/classbeat/design/interpret_kind.py and
// classes/interpretation_routes.py): an `interpret_request` job reads the request, then the
// browser asks for the design (`design-from-interpretation`). Server text is data: only codes and
// ids are decoded here, never shown as sentences.

/** What the instructor chose in the picker; absent means classbeat chooses from the request. */
export type MusicOverride =
  | { source: 'mowl_spotify_playlist'; spotify_playlist_id: string }
  | { source: 'mowl_swedebeat_mix'; mix_id: string }
  | { source: 'none' }
  | { source: 'pick' }

/** What `POST /api/jobs` takes for `interpret_request`. */
export interface InterpretInput {
  request: string
  music_override?: MusicOverride
  sport_override?: 'cycling' | 'running'
  mowl_connection_id: string
  spotify_connection_id?: string
}

export async function createInterpretJob(input: InterpretInput, idempotencyKey: string): Promise<Job> {
  const body = { kind: 'interpret_request', input }
  return decodeJob(await post('/api/jobs', body, { 'Idempotency-Key': idempotencyKey }))
}

export interface Interpretation {
  /** A hand-off refusal's reason code (stored, and answered again until a retry); null when none. */
  refusal: string | null
  /** The design job the hand-off started, if any. */
  designJobId: string | null
}

export async function getInterpretation(classId: string, signal?: AbortSignal): Promise<Interpretation> {
  const body = object(await get(`/api/classes/${encodeURIComponent(classId)}/interpretation`, signal), 'interpretation')
  const refusal = isObject(body.refusal) ? text(body.refusal, 'reason') : null
  const design = isObject(body.design_job) ? optionalText(body.design_job, 'job_id') : null
  return { refusal, designJobId: design }
}

/** Starts the design of an interpreted request; the same job comes back for every later call. */
export async function designFromInterpretation(classId: string, retry = false): Promise<string> {
  const body = object(
    await post(`/api/classes/${encodeURIComponent(classId)}/design-from-interpretation`, retry ? { retry: true } : {}),
    'design job',
  )
  return text(body, 'job_id')
}
