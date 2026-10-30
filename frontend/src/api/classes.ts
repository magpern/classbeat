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
import { decodeDraftContent, decodeUnknownBpm, type DraftContent } from './jobs'

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
