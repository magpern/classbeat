import { del, get, post } from './client'
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
  type JsonObject,
} from './decode'
import { decodeDraftContent, decodeUnknownBpm, type Brief, type DraftContent } from './jobs'

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
    },
    openJob,
    profile: decodeProfile(item.profile),
    tssApproximate: optionalFlag(item, 'tss_approximate') ?? false,
    brief: decodeBrief(item.brief),
    songs: decodeSongs(item.songs),
    playlist: decodePlaylist(item.playlist),
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

export interface RevisionSummary {
  id: string
  number: number
  parentId: string | null
  parentNumber: number | null
  /** `design_draft` or `revise_draft`. */
  origin: string
  createdAt: string
  isCurrent: boolean
  segmentCount: number
  intervalCount: number
  totalSec: number
}

function decodeRevisionSummary(item: JsonObject): RevisionSummary {
  const summary = object(item.summary, 'summary')
  return {
    id: text(item, 'id'),
    number: count(item, 'number'),
    parentId: optionalText(item, 'parent_id'),
    parentNumber: optionalCount(item, 'parent_number'),
    origin: text(item, 'origin'),
    createdAt: text(item, 'created_at'),
    isCurrent: flag(item, 'is_current'),
    segmentCount: count(summary, 'segments'),
    intervalCount: count(summary, 'intervals'),
    totalSec: count(summary, 'total_sec'),
  }
}

export async function listRevisions(
  classId: string,
  signal?: AbortSignal,
): Promise<RevisionSummary[]> {
  const body = object(await get(`${base(classId)}/revisions`, signal), 'revisions')
  return list(body, 'revisions').map((r) => decodeRevisionSummary(object(r, 'revision')))
}

export interface Revision extends RevisionSummary, DraftContent {
  unknownBpm: { index: number; title: string }[]
  /** ADR 0012 section 7: the backend's flag, set when the version has a zone 7 interval. */
  tssApproximate: boolean
  /** The content exactly as the server sent it: what the version hash is taken over. */
  raw: unknown
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
    raw: body.content,
  }
}

/** The backend's change summary of one answer (classbeat.design.diff); every part is optional. */
export interface ChangeSummary {
  segmentsAdded: number | null
  segmentsRemoved: number | null
  intervalsAdded: number | null
  intervalsRemoved: number | null
  intervalsChanged: number | null
  durationBefore: number | null
  durationAfter: number | null
  tssBefore: number | null
  tssAfter: number | null
  textChanged: boolean
  musicChanged: boolean
}

function number(source: unknown, key: string): number | null {
  if (!isObject(source)) return null
  const value = source[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function decodeChange(value: unknown): ChangeSummary | null {
  if (!isObject(value)) return null
  return {
    segmentsAdded: number(value.segments, 'added'),
    segmentsRemoved: number(value.segments, 'removed'),
    intervalsAdded: number(value.intervals, 'added'),
    intervalsRemoved: number(value.intervals, 'removed'),
    intervalsChanged: number(value.intervals, 'changed'),
    durationBefore: number(value.duration_sec, 'before'),
    durationAfter: number(value.duration_sec, 'after'),
    tssBefore: number(value.tss, 'before'),
    tssAfter: number(value.tss, 'after'),
    textChanged: value.text_changed === true,
    musicChanged: value.music_changed === true,
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
