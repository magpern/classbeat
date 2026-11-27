import { get, post } from './client'
import { decodeInterpretationItems, type InterpretationItem } from './interpretationItems'
import { decodeJob, getJob, type Job } from './jobs'
import { isObject, object, optionalText, text } from './decode'
import { decodeReadingExtras, type ReadingExtras } from './readingExtras'

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

/** An attached Spotify playlist (ADR 0038 section 3): favoured by the design, never a limit. */
export interface MusicSource {
  kind: 'spotify_playlist'
  id: string
}

/** What `POST /api/jobs` takes for `interpret_request`. */
export interface InterpretInput {
  request: string
  music_override?: MusicOverride
  /** At most 5 (ADR 0038 section 3); never together with `music_override`. */
  music_sources?: MusicSource[]
  sport_override?: 'cycling' | 'running'
  mowl_connection_id: string
  spotify_connection_id?: string
}

export async function createInterpretJob(input: InterpretInput, idempotencyKey: string): Promise<Job> {
  const body = { kind: 'interpret_request', input }
  return decodeJob(await post('/api/jobs', body, { 'Idempotency-Key': idempotencyKey }))
}

export interface Interpretation {
  /** The `interpret_request` job that read the request; the class's first message belongs to it. */
  interpretJobId: string | null
  /** How the request was read: what was asked, what was not done and what was assumed. */
  items: InterpretationItem[]
  /** A hand-off refusal's reason code (stored, and answered again until a retry); null when none. */
  refusal: string | null
  /** The design job the hand-off started, if any. */
  designJobId: string | null
  /** The composer's pinned songs and questions (ADR 0038 section 3); empty for any other class. */
  extras: ReadingExtras
}

export async function getInterpretation(classId: string, signal?: AbortSignal): Promise<Interpretation> {
  const body = object(await get(`/api/classes/${encodeURIComponent(classId)}/interpretation`, signal), 'interpretation')
  const refusal = isObject(body.refusal) ? text(body.refusal, 'reason') : null
  const design = isObject(body.design_job) ? optionalText(body.design_job, 'job_id') : null
  return {
    interpretJobId: optionalText(body, 'interpret_job_id'),
    items: decodeInterpretationItems(body.items) ?? [],
    refusal,
    designJobId: design,
    extras: decodeReadingExtras(body),
  }
}

/**
 * Starts the design of an interpreted request; the same job comes back for every later call.
 * `interpretJobId` names the reading the instructor confirmed: the server refuses `not_ready` when
 * another was stored since (#1538), so a stale tab never designs a reading it did not show.
 */
export async function designFromInterpretation(classId: string, interpretJobId: string | null = null, retry = false): Promise<string> {
  const sent = { ...(retry ? { retry: true } : {}), ...(interpretJobId !== null ? { interpret_job_id: interpretJobId } : {}) }
  const body = object(await post(`/api/classes/${encodeURIComponent(classId)}/design-from-interpretation`, sent), 'design job')
  return text(body, 'job_id')
}

/** One answer to a reading question: an option of it, or the instructor's own words (backend `Answer`). */
export type RereadAnswer = { questionIndex: number; optionIndex: number } | { questionIndex: number; text: string }

/**
 * What `POST /api/classes/{id}/reread` takes (#1538, backend `RereadIn`). `noMusic` is the closed
 * "No music" field of a reading with no music question, never derived from words; `musicSources`
 * are the Spotify playlists of the picker (the server checks them as a request's own).
 */
export interface RereadBody {
  interpretJobId: string
  answers: readonly RereadAnswer[]
  note?: string
  noMusic?: true
  musicSources?: readonly string[]
}

/** Reads the request again with the answers, before any design: a new `interpret_request` job, no class credit. */
export async function rereadClass(classId: string, input: RereadBody): Promise<string> {
  const body = {
    interpret_job_id: input.interpretJobId,
    answers: input.answers.map((a) =>
      'optionIndex' in a ? { question_index: a.questionIndex, option_index: a.optionIndex } : { question_index: a.questionIndex, text: a.text },
    ),
    ...(input.note !== undefined ? { note: input.note } : {}),
    ...(input.noMusic === true ? { no_music: true } : {}),
    ...(input.musicSources !== undefined ? { music_sources: input.musicSources.map((id) => ({ kind: 'spotify_playlist', id })) } : {}),
  }
  return text(object(await post(`/api/classes/${encodeURIComponent(classId)}/reread`, body), 'reread job'), 'job_id')
}

/**
 * After a stored `not_connected` refusal: a new reading of the same request with the current
 * connections, in a new class (no credit). The answer names the new `interpret_request` job; its
 * job says which class it made. The same job comes back for every later call.
 */
export async function interpretAgain(classId: string): Promise<{ classId: string }> {
  const started = object(await post(`/api/classes/${encodeURIComponent(classId)}/interpret-again`, {}), 'interpret job')
  const job = await getJob(text(started, 'job_id'))
  return { classId: job.classId }
}
