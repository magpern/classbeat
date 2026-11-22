import { ApiError, del, get, post, request } from './client'
import { DecodeError, isObject, list, object, optionalCount, optionalText, text, count, type JsonObject } from './decode'

// The Spotify listening preview of a class (ADR 0042 sections 7.2 to 7.4): the owner-scoped player
// proxy (audition, pause, state, devices) and the alignment facts with the instructor's corrections.
// The browser never sends a track or device id and never holds a token: a song is its play-order
// piece, a device is its name and type. Every call answers a value or a failure with a reason code;
// a failure is never thrown at the page and a raw provider message never reaches it.

export interface PreviewFailure {
  /** The backend's reason code, or `network`, `unauthorized` or `invalid_response` made here. */
  reason: string
  status: number
  /** Seconds until a 429 may be tried again; null: not said. */
  retryAfterS: number | null
}

export type Result<T> = { ok: true; value: T } | { ok: false; failure: PreviewFailure }

export interface DeviceView {
  name: string
  type: string
}

export type PlayerStatus = 'idle' | 'external' | 'paused' | 'playing'

/** The observed player state (section 7.3). `pieces`: every play-order index of the class that plays the track. */
export interface PlayerState {
  status: PlayerStatus
  pieces: number[]
  progressMs: number | null
  timestamp: number | null
  device: DeviceView | null
}

export interface AuditionAck {
  pieceIndex: number
  device: DeviceView | null
}

export interface AlignmentSong {
  /** The 0-based play-order index; `song` is the 1-based number a correction is stored under. */
  piece: number
  song: number
  title: string
  artist: string
  lengthMs: number
  analysis: { spotdownJob: string | null; sha256: string | null; timingClass: string | null }
  /** spotdown's alignment of the analysed file to Spotify's recording; null: none. */
  alignmentOffsetMs: number | null
  /** The instructor's correction, separate from the alignment; null: none. */
  correction: {
    offsetMs: number
    setAgainst: { mapVersion: number | null; sha256: string } | null
    /** `listen_again`: set against another analysis; shown, not applied until confirmed again. */
    state: 'current' | 'listen_again'
  } | null
}

export interface Alignment {
  /** The draft the corrections apply to: a write sends it back. */
  revisionId: string
  mapVersion: number | null
  crossfadeMs: number
  songs: AlignmentSong[]
}

const FAILURE = (reason: string, status: number, retryAfterS: number | null = null): PreviewFailure => ({ reason, status, retryAfterS })

function detailOf(body: unknown): JsonObject {
  if (!isObject(body) || !isObject(body.detail)) return {}
  return body.detail
}

/** A thrown API error as a failure: the reason code the backend gave, never its text. */
export function failureOf(error: unknown): PreviewFailure {
  if (error instanceof ApiError) {
    const detail = detailOf(error.body)
    const reason = error.reason ?? (error.status === 401 ? 'unauthorized' : 'unknown')
    const retry = error.retryAfter ?? (typeof detail.retry_after === 'number' ? detail.retry_after : null)
    return FAILURE(reason, error.status, retry)
  }
  if (error instanceof DecodeError) return FAILURE('invalid_response', 502)
  // A fetch that could not reach the server throws a TypeError; anything else is not a network fault.
  return error instanceof TypeError ? FAILURE('network', 0) : FAILURE('unknown', 0)
}

async function run<T>(call: () => Promise<unknown>, decode: (body: unknown) => T): Promise<Result<T>> {
  try {
    return { ok: true, value: decode(await call()) }
  } catch (error) {
    return { ok: false, failure: failureOf(error) }
  }
}

function device(value: unknown): DeviceView | null {
  if (value === null || value === undefined) return null
  const item = object(value, 'device')
  return { name: text(item, 'name'), type: text(item, 'type') }
}

const STATUSES: readonly string[] = ['idle', 'external', 'paused', 'playing']

function decodeState(body: unknown): PlayerState {
  const item = object(body, 'state')
  const status = text(item, 'status')
  if (!STATUSES.includes(status)) throw new DecodeError('status')
  const pieces = list(item, 'pieces').map((p) => {
    if (typeof p !== 'number' || !Number.isInteger(p) || p < 0) throw new DecodeError('pieces')
    return p
  })
  return {
    status: status as PlayerStatus,
    pieces,
    progressMs: optionalCount(item, 'progress_ms'),
    timestamp: optionalCount(item, 'timestamp'),
    device: device(item.device),
  }
}

const base = (classId: string): string => `/api/classes/${encodeURIComponent(classId)}/preview/spotify`

/** Plays the draft's song `piece` from `positionMs` of Spotify's recording, on the user's device. */
export function auditionPiece(classId: string, piece: number, positionMs: number): Promise<Result<AuditionAck>> {
  return run(
    () => post(`${base(classId)}/audition`, { piece_index: piece, position_ms: positionMs }),
    (body) => {
      const item = object(body, 'audition')
      return { pieceIndex: count(item, 'piece_index'), device: device(item.device) }
    },
  )
}

/** Pauses, only while a song of this class plays; answers the state the backend observed. */
export function pausePlayer(classId: string): Promise<Result<PlayerState>> {
  return run(() => post(`${base(classId)}/pause`), decodeState)
}

export function readPlayerState(classId: string, signal?: AbortSignal): Promise<Result<PlayerState>> {
  return run(() => get(`${base(classId)}/state`, signal), decodeState)
}

function decodeSong(value: unknown): AlignmentSong {
  const item = object(value, 'song')
  const analysis = object(item.analysis, 'analysis')
  const alignment = object(item.alignment, 'alignment')
  const raw = item.correction
  let correction: AlignmentSong['correction'] = null
  if (raw !== null && raw !== undefined) {
    const c = object(raw, 'correction')
    const state = text(c, 'state')
    if (state !== 'current' && state !== 'listen_again') throw new DecodeError('state')
    const against = c.set_against_analysis
    const set = against === null || against === undefined ? null : object(against, 'set_against_analysis')
    correction = {
      offsetMs: offset(c, 'offset_ms'),
      setAgainst: set === null ? null : { mapVersion: optionalCount(set, 'map_version'), sha256: text(set, 'sha256') },
      state,
    }
  }
  return {
    piece: count(item, 'piece'),
    song: count(item, 'song'),
    title: text(item, 'title'),
    artist: text(item, 'artist'),
    lengthMs: count(item, 'length_ms'),
    analysis: { spotdownJob: optionalText(analysis, 'spotdown_job'), sha256: optionalText(analysis, 'sha256'), timingClass: optionalText(analysis, 'timing_class') },
    alignmentOffsetMs: alignment.offset_ms === null || alignment.offset_ms === undefined ? null : offset(alignment, 'offset_ms'),
    correction,
  }
}

function offset(source: JsonObject, key: string): number {
  const value = source[key]
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new DecodeError(key)
  return value
}

function decodeAlignment(body: unknown): Alignment {
  const item = object(body, 'alignment')
  return {
    revisionId: text(item, 'revision_id'),
    mapVersion: optionalCount(item, 'map_version'),
    crossfadeMs: count(item, 'crossfade_ms'),
    songs: list(item, 'songs').map(decodeSong),
  }
}

/** The draft's songs with spotdown's alignment and the class's corrections; a 404 or 409 is a failure (not a Spotify draft). */
export function readAlignment(classId: string, signal?: AbortSignal): Promise<Result<Alignment>> {
  return run(() => get(`${base(classId)}/alignment`, signal), decodeAlignment)
}

/** Sets (or confirms again) song `song`'s correction (1-based); answers the alignment as it now stands. */
export function setCorrection(classId: string, song: number, revisionId: string, offsetMs: number): Promise<Result<Alignment>> {
  return run(
    () => request('PUT', `${base(classId)}/corrections/${String(song)}`, { body: { revision_id: revisionId, offset_ms: offsetMs } }),
    decodeAlignment,
  )
}

export function clearCorrection(classId: string, song: number, revisionId: string): Promise<Result<Alignment>> {
  return run(() => del(`${base(classId)}/corrections/${String(song)}?revision_id=${encodeURIComponent(revisionId)}`), decodeAlignment)
}
