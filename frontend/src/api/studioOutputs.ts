import { ApiError, get, post } from './client'
import { count, flag, isObject, list, object, optionalCount, optionalFlag, optionalText, text } from './decode'
import { decodeJob, decodeStartedJob, type Job } from './jobs'

// A studio class's timeline, its design start, its chat revise and its output choice (ADR 0036
// sections 4 to 6; backend `classes/studio_timeline.py`, `studio_outputs_routes.py`,
// `studio_design_routes.py`). The API sends no catalogue reference and no Spotify id.

const classBase = (classId: string) => `/api/classes/${encodeURIComponent(classId)}`

// The timeline ---------------------------------------------------------------------------------

export interface TimelineTrack {
  position: number
  title: string
  artist: string
  bpm: number
  startSec: number
  endSec: number
  playedSec: number
  effectiveSec: number
  mixStartSec: number
  mixEndSec: number
  crossfadeInSec: number
  crossfadeOutSec: number
  /** The position of the first piece of the same song when this piece plays it again (#1342). */
  repeatOf: number | null
  /** The same song as the piece before it, playing on (a loop join, A4: exact timing, on phrase points). */
  loop: boolean
}

/** A soft check of the mix (never a refusal): its code and the piece positions it names. */
export interface TimelineHint {
  code: 'whole_track_repeated' | 'one_song_dominates'
  pieces: number[]
}

export interface TimelineFade {
  after: number
  sec: number
  mixFromSec: number
  mixToSec: number
}

export interface TimelineInterval {
  segmentName: string
  segmentKind: string
  startSec: number
  endSec: number
  zone: number | null
  standing: boolean
  /** The position of the track under the start of the interval. */
  track: number
}

export interface Timeline {
  revisionId: string
  contentHash: string
  durationSec: number
  workoutSec: number
  tracks: TimelineTrack[]
  /** A song plays in more than one piece: the class is mix-only (ADR 0036 amendment A8). */
  hasRepeats: boolean
  hints: TimelineHint[]
  crossfades: TimelineFade[]
  intervals: TimelineInterval[]
  /** SHA-256 of the stored mix spec: Approve is offered only when the mix screen's preview has this digest (B10). */
  specDigest: string
  /** The model's design the choices apply to; null: none (`no_proposal`), so only a re-plan helps. */
  proposalRevisionId: string | null
  /** The spec is the whole-songs form. */
  isPlain: boolean
  /** The recorded choices of an applied draft, to pre-fill the screen as toggles; never applied by themselves. */
  choices: Record<string, unknown> | null
}

function num(item: Record<string, unknown>, key: string): number {
  const value = item[key]
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new ApiError(500, null, null, undefined)
  return value
}

function decodeTrack(value: unknown): TimelineTrack {
  const item = object(value, 'track')
  return {
    position: count(item, 'position'),
    title: text(item, 'title'),
    artist: text(item, 'artist'),
    bpm: num(item, 'bpm'),
    startSec: num(item, 'start_sec'),
    endSec: num(item, 'end_sec'),
    playedSec: num(item, 'played_sec'),
    effectiveSec: num(item, 'effective_sec'),
    mixStartSec: num(item, 'mix_start_sec'),
    mixEndSec: num(item, 'mix_end_sec'),
    crossfadeInSec: num(item, 'crossfade_in_sec'),
    crossfadeOutSec: num(item, 'crossfade_out_sec'),
    repeatOf: optionalCount(item, 'repeat_of'),
    loop: optionalFlag(item, 'loop') === true,
  }
}

const HINT_CODES: readonly string[] = ['whole_track_repeated', 'one_song_dominates']

/** The known hints only: an unknown code is left out rather than shown as a code. */
function decodeHints(item: Record<string, unknown>): TimelineHint[] {
  if (item['hints'] === undefined) return []
  return list(item, 'hints').flatMap((value) => {
    const hint = object(value, 'hint')
    const code = text(hint, 'code')
    if (!HINT_CODES.includes(code)) return []
    const pieces = list(hint, 'pieces').map((n) => {
      if (typeof n !== 'number' || !Number.isInteger(n) || n < 0) throw new ApiError(500, null, null, undefined)
      return n
    })
    return [{ code: code as TimelineHint['code'], pieces }]
  })
}

function decodeFade(value: unknown): TimelineFade {
  const item = object(value, 'crossfade')
  return { after: count(item, 'after'), sec: num(item, 'sec'), mixFromSec: num(item, 'mix_from_sec'), mixToSec: num(item, 'mix_to_sec') }
}

function decodeInterval(value: unknown): TimelineInterval {
  const item = object(value, 'interval')
  return {
    segmentName: optionalText(item, 'segment_name') ?? '',
    segmentKind: optionalText(item, 'segment_kind') ?? '',
    startSec: num(item, 'start_sec'),
    endSec: num(item, 'end_sec'),
    zone: optionalCount(item, 'zone'),
    standing: flag(item, 'standing'),
    track: count(item, 'track'),
  }
}

export function decodeTimeline(value: unknown): Timeline {
  const item = object(value, 'timeline')
  const tracks = list(item, 'tracks').map(decodeTrack)
  return {
    revisionId: text(item, 'revision_id'),
    contentHash: text(item, 'content_hash'),
    durationSec: num(item, 'duration_sec'),
    workoutSec: num(item, 'workout_sec'),
    tracks,
    // The server's flag, or any piece marked as a repeat: either one makes the class mix-only.
    hasRepeats: optionalFlag(item, 'has_repeats') === true || tracks.some((t) => t.repeatOf !== null),
    hints: decodeHints(item),
    crossfades: list(item, 'crossfades').map(decodeFade),
    intervals: list(item, 'intervals').map(decodeInterval),
    specDigest: text(item, 'spec_digest'),
    proposalRevisionId: optionalText(item, 'proposal_revision_id'),
    isPlain: flag(item, 'is_plain'),
    choices: isObject(item['choices']) ? item['choices'] : null,
  }
}

export type TimelineResult =
  | { kind: 'ready'; timeline: Timeline }
  /** A studio class whose cached analysis is gone: Change songs analyses again. */
  | { kind: 'music_changed' }
  /**
   * `no_studio_draft`: not a studio class, or a studio class whose design has not made a draft yet
   * (the answer is the same; the class page tells them apart by the class's approved song list).
   */
  | { kind: 'no_draft' }
  /** No such class or the studio is off. */
  | { kind: 'none' }

export async function getTimeline(classId: string, signal?: AbortSignal): Promise<TimelineResult> {
  try {
    return { kind: 'ready', timeline: decodeTimeline(await get(`${classBase(classId)}/timeline`, signal)) }
  } catch (error) {
    if (error instanceof ApiError && error.status === 409 && error.reason === 'music_changed') return { kind: 'music_changed' }
    if (error instanceof ApiError && error.status === 409 && error.reason === 'no_studio_draft') return { kind: 'no_draft' }
    if (error instanceof ApiError && (error.status === 404 || error.status === 409)) return { kind: 'none' }
    throw error
  }
}

// The design start and the chat revise ---------------------------------------------------------

/** "Approve" is done: design the class on the approved list (no credit; the pick committed it). */
export async function startStudioDesign(classId: string, revisionId: string, idempotencyKey: string): Promise<Job> {
  const path = `${classBase(classId)}/studio-songs/${encodeURIComponent(revisionId)}/design`
  return decodeStartedJob(await post(path, {}, { 'Idempotency-Key': idempotencyKey }))
}

export interface StudioReviseInput {
  class_id: string
  base_revision_id: string
  request: string
  swedebeat_connection_id: string
}

/** `studio_revise`: a chat change of a studio draft (no MOWL or Spotify connection). */
export async function createStudioReviseJob(input: StudioReviseInput, idempotencyKey: string): Promise<Job> {
  return decodeJob(await post('/api/jobs', { kind: 'studio_revise', input }, { 'Idempotency-Key': idempotencyKey }))
}

// The output choice (ADR 0036 section 6) ---------------------------------------------------------

export type Output = 'spotify_playlist' | 'swedebeat_playlist' | 'swedebeat_mix' | 'mowl'
export const OUTPUTS: readonly Output[] = ['spotify_playlist', 'swedebeat_playlist', 'swedebeat_mix', 'mowl']

/** The matrix's defaults: both playlists on, the mix and MOWL optional (the mix is on with MOWL). */
export const DEFAULT_OUTPUTS: readonly Output[] = ['spotify_playlist', 'swedebeat_playlist']

/** Turns one output on or off; MOWL needs the mix, so MOWL on turns the mix on and the mix off turns MOWL off. */
export function toggleOutput(chosen: readonly Output[], output: Output, on: boolean): Output[] {
  const next = new Set(chosen)
  if (on) {
    next.add(output)
    if (output === 'mowl') next.add('swedebeat_mix')
  } else {
    next.delete(output)
    if (output === 'swedebeat_mix') next.delete('mowl')
  }
  return OUTPUTS.filter((o) => next.has(o))
}

/**
 * The user's approval of a draft for these outputs. The browser sends only the draft's id and
 * hash and the choice; the server binds the SwedeBeat connection and re-checks everything.
 */
export async function chooseOutputs(
  classId: string,
  input: { revisionId: string; contentHash: string; outputs: readonly Output[] },
  idempotencyKey: string,
): Promise<Job> {
  const body = { revision_id: input.revisionId, content_hash: input.contentHash, outputs: input.outputs }
  return decodeStartedJob(await post(`${classBase(classId)}/outputs`, body, { 'Idempotency-Key': idempotencyKey }))
}
