import { get, post } from './client'
import { count, flag, isObject, list, object, optionalText, text, type JsonObject } from './decode'

// Jobs (ADR 0005 section 2, backend/src/classbeat/jobs/routes.py) and the design_draft kind
// (ADR 0006 section 3). Server text is data: it is decoded into typed fields and rendered as text.

export type JobState =
  | 'queued'
  | 'running'
  | 'waiting_for_approval'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'expired'

const STATES: readonly string[] = [
  'queued',
  'running',
  'waiting_for_approval',
  'completed',
  'failed',
  'cancelled',
  'expired',
]

const TERMINAL_STATES: readonly string[] = ['completed', 'failed', 'cancelled', 'expired']

export interface Job {
  id: string
  state: JobState
  stage: string | null
  classId: string
  cancelRequested: boolean
  /** An ADR 0005 section 4 category, never an error text. */
  errorCategory: string | null
}

export function isTerminal(state: string): boolean {
  return TERMINAL_STATES.includes(state)
}

function decodeJob(value: unknown): Job {
  const item = object(value, 'job')
  const state = text(item, 'state')
  return {
    id: text(item, 'id'),
    state: STATES.includes(state) ? (state as JobState) : 'failed',
    stage: optionalText(item, 'stage'),
    classId: text(item, 'class_id'),
    cancelRequested: flag(item, 'cancel_requested'),
    errorCategory: optionalText(item, 'error_category'),
  }
}

/** The brief of the new-class form (backend `Brief`, ADR 0006 section 7). */
export interface Brief {
  length_min: number
  intensity: 'easy' | 'moderate' | 'hard'
  climbs: number
  sprints: number
  races: number
  ftp_test: 3 | 5 | 20 | null
  style: string
  text: string
}

/** What `POST /api/jobs` takes for `design_draft`: a new class by title, or an existing one. */
export interface DesignInput {
  title?: string
  class_id?: string
  brief: Brief
  music: { source: 'mowl_spotify_playlist'; spotify_playlist_id: string }
  mowl_connection_id: string
}

export async function createDesignJob(input: DesignInput, idempotencyKey: string): Promise<Job> {
  const body = { kind: 'design_draft', input }
  return decodeJob(await post('/api/jobs', body, { 'Idempotency-Key': idempotencyKey }))
}

export async function getJob(id: string, signal?: AbortSignal): Promise<Job> {
  return decodeJob(await get(`/api/jobs/${encodeURIComponent(id)}`, signal))
}

export async function cancelJob(id: string): Promise<Job> {
  return decodeJob(await post(`/api/jobs/${encodeURIComponent(id)}/cancel`))
}

// The draft summary: GET /api/jobs/{id}/revision answers the stored WorkoutDraft and the tracks
// MOWL had no BPM for. Only what the answer holds is decoded; nothing is computed or added.

export interface DraftInterval {
  sec: number
  zone: number
  rpm: [number, number] | 'beat'
  standing: boolean
  cue: string
}

export interface DraftSegment {
  name: string
  kind: string
  intervals: DraftInterval[]
}

export interface DraftSummary {
  number: number
  name: string
  description: string
  segments: DraftSegment[]
  unknownBpm: { index: number; title: string }[]
}

function decodeInterval(value: unknown): DraftInterval {
  const item = object(value, 'interval')
  const rpm = item.rpm
  let decoded: DraftInterval['rpm']
  if (rpm === 'beat') decoded = 'beat'
  else if (Array.isArray(rpm) && rpm.length === 2 && rpm.every((n) => typeof n === 'number')) {
    decoded = [rpm[0] as number, rpm[1] as number]
  } else throw new Error('Unexpected response from the server (rpm).')
  return {
    sec: count(item, 'sec'),
    zone: count(item, 'zone'),
    rpm: decoded,
    standing: flag(item, 'standing'),
    cue: optionalText(item, 'cue') ?? '',
  }
}

function decodeSegment(value: unknown): DraftSegment {
  const item = object(value, 'segment')
  return {
    name: text(item, 'name'),
    kind: text(item, 'kind'),
    intervals: list(item, 'intervals').map(decodeInterval),
  }
}

export async function getDraftSummary(id: string): Promise<DraftSummary> {
  const body = object(await get(`/api/jobs/${encodeURIComponent(id)}/revision`), 'revision')
  const content = object(body.content, 'content')
  return {
    number: count(body, 'number'),
    name: text(content, 'name'),
    description: optionalText(content, 'description') ?? '',
    segments: list(content, 'segments').map(decodeSegment),
    unknownBpm: list(body, 'unknown_bpm').map((entry) => {
      const item: JsonObject = isObject(entry) ? entry : {}
      return { index: count(item, 'index'), title: text(item, 'title') }
    }),
  }
}

// The user's Spotify playlists as MOWL lists them (backend/src/classbeat/design/routes.py).

export interface Playlist {
  spotifyPlaylistId: string
  name: string
}

export async function getMowlPlaylists(signal?: AbortSignal): Promise<Playlist[]> {
  const body = object(await get('/api/music/mowl-playlists', signal), 'playlists')
  return list(body, 'playlists').map((entry) => {
    const item = object(entry, 'playlist')
    return { spotifyPlaylistId: text(item, 'spotify_playlist_id'), name: text(item, 'name') }
  })
}

// External operations (ADR 0005 section 7): the undecided attach and the user's two actions.

export interface Operation {
  id: string
  /** `pending`, `succeeded`, `failed` or `uncertain`. */
  state: string
  resolvedBy: string | null
}

function decodeOperation(value: unknown): Operation {
  const item = object(value, 'operation')
  return {
    id: text(item, 'id'),
    state: text(item, 'state'),
    resolvedBy: optionalText(item, 'resolved_by'),
  }
}

export async function listUncertainOperations(jobId: string): Promise<Operation[]> {
  const path = `/api/external-operations?job_id=${encodeURIComponent(jobId)}`
  return list(object(await get(path), 'operations'), 'operations').map(decodeOperation)
}

export async function checkAgain(id: string): Promise<Operation> {
  return decodeOperation(
    await post(`/api/external-operations/${encodeURIComponent(id)}/check-again`),
  )
}

export async function doItAnyway(id: string): Promise<Operation> {
  return decodeOperation(
    await post(`/api/external-operations/${encodeURIComponent(id)}/do-it-anyway`),
  )
}
