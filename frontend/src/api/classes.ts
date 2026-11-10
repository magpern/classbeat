import { del, get, post, request } from './client'
import {
  count,
  flag,
  isObject,
  list,
  object,
  optionalCount,
  optionalFlag,
  optionalText,
  text,
} from './decode'
import { decodeBeatOne, type BeatOne } from './beatOneTypes'
import { decodeDraftContent, decodeUnknownBpm } from './draftTypes'
import { decodeInterpretationItems } from './interpretationItems'
import type { Brief } from './jobs'
import {
  decodeChange,
  decodeRevisionSummary,
  decodeRuleHits,
  type ChangeSummary,
  type Revision,
  type RevisionSummary,
} from './revisionTypes'

export * from './beatOneTypes'
export * from './revisionTypes'

// The class API (ADR 0007 section 12, backend/src/classbeat/classes/routes.py). Server text is
// data: decoded into typed fields and rendered as text by React, never as HTML.

export interface OpenJob {
  id: string
  /** `design_draft`, `revise_draft`, ... */
  kind: string
  state: string
}

export interface SavedSummary {
  count: number
  latestRevisionNumber: number | null
  latestSavedAt: string | null
  latestUpToDate: boolean | null
  /**
   * NOT SENT by the backend yet (PR for #150 lists the gap): the id Update names
   * (`saved_workout_id`, ADR 0007 section 5) and how many saves were detached by a MOWL
   * reconnect (section 9). Absent means null and 0; the page then offers no Update.
   */
  latestSavedWorkoutId: string | null
  detachedCount: number
  /**
   * SL6 (#532): whether the newest saved row was read back from MOWL and matched (`state_hash` set).
   * False: MOWL took the write but classbeat could not confirm it (ADR 0007 item 8). Null: no saved
   * row, or an answer without the field; neither is ever worded as a match.
   */
  latestVerified: boolean | null
}

/** The class's newest song list (ADR 0008 section 9). */
export interface ClassSongs {
  /** `none`, `waiting`, `approved`, `expired`, `superseded` or `cancelled`. */
  state: string
  jobId: string | null
  revisionId: string | null
  contentHash: string | null
  expiresAt: string | null
}

export interface ClassPlaylist {
  name: string
  /** The owner's own link, only when the server built it from a verified playlist. */
  link: string | null
}

const SPOTIFY_PLAYLIST_LINK = /^https:\/\/open\.spotify\.com\/playlist\/[A-Za-z0-9]{22}$/

const SPOTIFY_TRACK_LINK = /^https:\/\/open\.spotify\.com\/track\/[A-Za-z0-9]{22}$/

/** Whether a link from the API is a Spotify track link; the frontend never builds one. */
export function isTrackLink(link: string): boolean {
  return SPOTIFY_TRACK_LINK.test(link)
}

/** Whether a link from the API is a Spotify playlist link; the frontend never builds one. */
export function isPlaylistLink(link: string): boolean {
  return SPOTIFY_PLAYLIST_LINK.test(link)
}

function decodePlaylist(value: unknown): ClassPlaylist | null {
  if (value === null || value === undefined) return null
  const item = object(value, 'playlist')
  const link = optionalText(item, 'link')
  return {
    name: text(item, 'name'),
    link: link !== null && SPOTIFY_PLAYLIST_LINK.test(link) ? link : null,
  }
}

function decodeSongs(value: unknown): ClassSongs {
  if (value === null || value === undefined) {
    return { state: 'none', jobId: null, revisionId: null, contentHash: null, expiresAt: null }
  }
  const item = object(value, 'songs')
  return {
    state: text(item, 'state'),
    jobId: optionalText(item, 'job_id'),
    revisionId: optionalText(item, 'revision_id'),
    contentHash: optionalText(item, 'content_hash'),
    expiresAt: optionalText(item, 'expires_at'),
  }
}

export interface ClassSummary {
  id: string
  title: string
  /** ADR 0018 section 1: the class's sport; an answer without it is cycling. */
  sport: 'cycling' | 'running'
  /** ADR 0007 amendment item 10 (#530): a class with no music; an answer without it is not. */
  soundless: boolean
  createdAt: string
  updatedAt: string
  currentRevisionId: string | null
  currentRevisionNumber: number | null
  revisionCount: number
  saved: SavedSummary
  openJob: OpenJob | null
  /** The current version's intervals as [zone, seconds], in order (empty without one). */
  profile: [number, number][]
  /** ADR 0012 section 7: the backend's flag, set when the current version has a zone 7 interval. */
  tssApproximate: boolean
  /** The brief the class was designed for; null when there is none (or on the list). */
  brief: Brief | null
  /** Only the class page's answer has these; the list's rows read as none. */
  songs: ClassSongs
  playlist: ClassPlaylist | null
  /** ADR 0016: false while the `swedebeat_enabled` flag is off (the key is absent from the answer). */
  swedebeatOn: boolean
  /** The class's newest Swedebeat match and whether it is the approved selection. */
  swedebeatMatch: { revisionId: string; approved: boolean } | null
  /** Beat-one corrections (ADR 0007 amendment): unavailable on the list and without beat data. */
  beatOne: BeatOne
}

const INTENSITIES: readonly unknown[] = ['easy', 'moderate', 'hard']

/** The class's brief; anything malformed reads as none, and callers then use their fallback. */
function decodeBrief(value: unknown): Brief | null {
  if (!isObject(value)) return null
  const whole = (key: string): number | null => {
    const n = value[key]
    return typeof n === 'number' && Number.isInteger(n) ? n : null
  }
  const length = whole('length_min')
  const climbs = whole('climbs')
  const sprints = whole('sprints')
  const races = whole('races')
  const ftp = value.ftp_test
  if (length === null || climbs === null || sprints === null || races === null) return null
  if (!INTENSITIES.includes(value.intensity)) return null
  if (ftp !== null && ftp !== 3 && ftp !== 5 && ftp !== 20) return null
  if (typeof value.style !== 'string' || typeof value.text !== 'string') return null
  return {
    length_min: length,
    intensity: value.intensity as Brief['intensity'],
    climbs,
    sprints,
    races,
    ftp_test: ftp,
    style: value.style,
    text: value.text,
  }
}

/**
 * Absent means no profile. An interval that is not a pair of whole numbers (zone 0 to 99,
 * seconds 0 or more) is skipped, so one bad interval never fails the list or hides the rest.
 */
function decodeProfile(value: unknown): [number, number][] {
  if (!Array.isArray(value)) return []
  const out: [number, number][] = []
  for (const entry of value as unknown[]) {
    if (!Array.isArray(entry) || entry.length !== 2) continue
    const [zone, sec] = entry as unknown[]
    if (!Number.isInteger(zone) || !Number.isInteger(sec)) continue
    if ((zone as number) < 0 || (zone as number) > 99 || (sec as number) < 0) continue
    out.push([zone as number, sec as number])
  }
  return out
}

function decodeMatchPointer(value: unknown): { revisionId: string; approved: boolean } | null {
  if (value === null || value === undefined) return null
  const item = object(value, 'swedebeat_match')
  return { revisionId: text(item, 'revision_id'), approved: flag(item, 'approved') }
}

function decodeClass(value: unknown): ClassSummary {
  const item = object(value, 'class')
  const saved = object(item.saved, 'saved')
  const job = item.open_job
  let openJob: OpenJob | null = null
  if (job !== null && job !== undefined) {
    const found = object(job, 'open_job')
    openJob = { id: text(found, 'id'), kind: text(found, 'kind'), state: text(found, 'state') }
  }
  return {
    id: text(item, 'id'),
    title: text(item, 'title'),
    sport: item.sport === 'running' ? 'running' : 'cycling',
    soundless: item.soundless === true,
    createdAt: text(item, 'created_at'),
    updatedAt: text(item, 'updated_at'),
    currentRevisionId: optionalText(item, 'current_revision_id'),
    currentRevisionNumber: optionalCount(item, 'current_revision_number'),
    revisionCount: count(item, 'revision_count'),
    saved: {
      count: count(saved, 'count'),
      latestRevisionNumber: optionalCount(saved, 'latest_revision_number'),
      latestSavedAt: optionalText(saved, 'latest_saved_at'),
      latestUpToDate: optionalFlag(saved, 'latest_up_to_date'),
      latestSavedWorkoutId: optionalText(saved, 'latest_saved_workout_id'),
      detachedCount: optionalCount(saved, 'detached_count') ?? 0,
      latestVerified: optionalFlag(saved, 'latest_verified'),
    },
    openJob,
    profile: decodeProfile(item.profile),
    tssApproximate: optionalFlag(item, 'tss_approximate') ?? false,
    brief: decodeBrief(item.brief),
    songs: decodeSongs(item.songs),
    playlist: decodePlaylist(item.playlist),
    swedebeatOn: 'swedebeat_match' in item,
    swedebeatMatch: decodeMatchPointer(item.swedebeat_match),
    beatOne: decodeBeatOne(item.beat_one),
  }
}

const base = (classId: string) => `/api/classes/${encodeURIComponent(classId)}`

export async function listClasses(signal?: AbortSignal): Promise<ClassSummary[]> {
  const body = object(await get('/api/classes', signal), 'classes')
  return list(body, 'classes').map(decodeClass)
}

export async function getClass(classId: string, signal?: AbortSignal): Promise<ClassSummary> {
  return decodeClass(await get(base(classId), signal))
}


export async function listRevisions(
  classId: string,
  signal?: AbortSignal,
): Promise<RevisionSummary[]> {
  const body = object(await get(`${base(classId)}/revisions`, signal), 'revisions')
  return list(body, 'revisions').map((r) => decodeRevisionSummary(object(r, 'revision')))
}


export async function getRevision(
  classId: string,
  revisionId: string,
  signal?: AbortSignal,
): Promise<Revision> {
  const body = object(
    await get(`${base(classId)}/revisions/${encodeURIComponent(revisionId)}`, signal),
    'revision',
  )
  return {
    ...decodeRevisionSummary(body),
    ...decodeDraftContent(body.content),
    unknownBpm: decodeUnknownBpm(body),
    tssApproximate: optionalFlag(body, 'tss_approximate') ?? false,
    ruleHits: decodeRuleHits(body),
    notesUseCurrentRules: optionalFlag(body, 'notes_use_current_rules') ?? false,
    raw: body.content,
    interpretation: decodeInterpretationItems(isObject(body.provenance) ? body.provenance.interpretation : null),
  }
}

export interface ClassMessage {
  seq: number
  role: 'user' | 'assistant'
  /** The user's request, or the model's note: untrusted text, shown as plain text only. */
  body: string
  jobId: string | null
  baseRevisionId: string | null
  resultRevisionId: string | null
  change: ChangeSummary | null
  createdAt: string
}

export async function listMessages(
  classId: string,
  signal?: AbortSignal,
): Promise<ClassMessage[]> {
  const body = object(await get(`${base(classId)}/messages`, signal), 'messages')
  return list(body, 'messages').map((entry) => {
    const item = object(entry, 'message')
    return {
      seq: count(item, 'seq'),
      role: text(item, 'role') === 'user' ? 'user' : 'assistant',
      body: text(item, 'body'),
      jobId: optionalText(item, 'job_id'),
      baseRevisionId: optionalText(item, 'base_revision_id'),
      resultRevisionId: optionalText(item, 'result_revision_id'),
      change: decodeChange(item.change),
      createdAt: text(item, 'created_at'),
    }
  })
}

/** Makes `revisionId` the current revision. `contentHash` is the hash of the content shown. */
export async function pickCurrent(
  classId: string,
  revisionId: string,
  contentHash: string,
): Promise<ClassSummary> {
  return decodeClass(
    await post(`${base(classId)}/current`, { revision_id: revisionId, content_hash: contentHash }),
  )
}

export interface Deleted {
  mowlWorkoutsKept: number
}

export async function deleteClass(classId: string, confirmUncertain: boolean): Promise<Deleted> {
  const query = confirmUncertain ? '?confirm_uncertain=true' : ''
  const body = object(await del(`${base(classId)}${query}`), 'deleted')
  return { mowlWorkoutsKept: count(body, 'mowl_workouts_kept') }
}

/** Dismiss a pattern note for this class (ADR 0017 section 4); idempotent. */
export async function dismissRule(classId: string, ruleId: string): Promise<void> {
  await request('PUT', `${base(classId)}/dismissed-rules/${encodeURIComponent(ruleId)}`)
}

/** Undo a dismissal ("Show again"); idempotent. */
export async function showRuleAgain(classId: string, ruleId: string): Promise<void> {
  await del(`${base(classId)}/dismissed-rules/${encodeURIComponent(ruleId)}`)
}

/** Set one song's beat-one correction (-3 to 3, not 0) for the music of `revisionId`. */
export async function setBeatOne(
  classId: string,
  song: number,
  beats: number,
  revisionId: string,
): Promise<BeatOne> {
  const body = await request('PUT', `${base(classId)}/beat-one/${String(song)}`, {
    body: { beats, revision_id: revisionId },
  })
  return decodeBeatOne(body)
}

/** Clear one song's correction; idempotent. */
export async function clearBeatOne(
  classId: string,
  song: number,
  revisionId: string,
): Promise<BeatOne> {
  const query = `?revision_id=${encodeURIComponent(revisionId)}`
  return decodeBeatOne(await del(`${base(classId)}/beat-one/${String(song)}${query}`))
}

/** Clear one correction of the class by its id (for earlier music, or a song not played). */
export async function clearBeatOneRow(classId: string, rowId: string): Promise<BeatOne> {
  return decodeBeatOne(await del(`${base(classId)}/beat-one/rows/${encodeURIComponent(rowId)}`))
}

/** Clear every correction of the class. */
export async function clearAllBeatOne(classId: string): Promise<BeatOne> {
  return decodeBeatOne(await del(`${base(classId)}/beat-one`))
}
