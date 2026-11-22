import { ApiError, get, post } from './client'
import { isObject, list, object, optionalText, text, type JsonObject } from './decode'

// The instructor's own edits of the current draft (ADR 0042 sections 3 to 6; backend
// `classes/edit_routes.py`, `user_edit_model.py`). A block is a flat index in the base's workout,
// a piece an index in play order; no catalogue reference or Spotify id ever travels.
// `base_revision_id` and `expected_current_id` are optional on the API and ALWAYS sent here, so a
// second tab's edit is a 409 `stale_revision` and never a silent overwrite (D3).

const base = (classId: string) => `/api/classes/${encodeURIComponent(classId)}`

export type Edge = 'start' | 'end'
export type PinMode = 'bar' | 'exact'
export type LockScope = 'all' | 'timing' | 'attributes'
export type AttributeField = 'zone' | 'rpm' | 'standing' | 'cue' | 'game'
export type EventKind = 'drop' | 'downbeat' | 'section'

/** One operation of `POST /edits`, in the wire's own shape. */
export type EditOp =
  | { op: 'drag'; block: number; delta_sec: number }
  | { op: 'move'; block: number; direction: -1 | 1 }
  | { op: 'split'; block: number; offset_sec: number }
  | { op: 'merge'; block: number }
  | { op: 'set'; block: number; field: AttributeField; value: unknown }
  | { op: 'duration'; total_sec: number }
  | { op: 'lock'; block: number; scope: LockScope }
  | { op: 'unlock'; block: number }
  | { op: 'anchor'; block: number; edge: Edge; event: { piece: number; source_ms: number; kind: EventKind } }
  | { op: 'remove_anchor'; block: number; edge: Edge }
  | { op: 'use_ai_value'; block: number; field: AttributeField | 'boundary' }
  | { op: 'clear_all' }
  | { op: 'pin_set'; piece: number; edge: Edge; requested_sec: number; mode: PinMode }
  | { op: 'pin_confirm'; piece: number; edge: Edge }
  | { op: 'pin_clear'; piece: number; edge: Edge }

/** An overlay record as the page sees it (`RecordOut`): a pin or an anchor names its piece by index. */
export interface EditRecord {
  type: 'override' | 'lock' | 'anchor' | 'pin'
  segmentKind: string | null
  /** The block's class-time span in ms at the time the record was made. */
  span: [number, number] | null
  field: string | null
  scope: string | null
  edge: Edge | null
  piece: number | null
  value: unknown
  aiValue: unknown
  confirmed: boolean | null
}

const TYPES: readonly string[] = ['override', 'lock', 'anchor', 'pin']

function optionalInt(item: JsonObject, key: string): number | null {
  const value = item[key]
  return typeof value === 'number' && Number.isInteger(value) ? value : null
}

function decodeSpan(value: unknown): [number, number] | null {
  if (!Array.isArray(value) || value.length !== 2) return null
  const [from, to] = value as unknown[]
  return typeof from === 'number' && typeof to === 'number' ? [from, to] : null
}

function decodeRecord(value: unknown): EditRecord | null {
  const item = object(value, 'record')
  const type = text(item, 'type')
  if (!TYPES.includes(type)) return null // a record kind this page does not know is left out, never shown as a code
  const edge = optionalText(item, 'edge')
  return {
    type: type as EditRecord['type'],
    segmentKind: optionalText(item, 'segment_kind'),
    span: decodeSpan(item['span']),
    field: optionalText(item, 'field'),
    scope: optionalText(item, 'scope'),
    edge: edge === 'start' || edge === 'end' ? edge : null,
    piece: optionalInt(item, 'piece'),
    value: item['value'] ?? null,
    aiValue: item['ai_value'] ?? null,
    confirmed: typeof item['confirmed'] === 'boolean' ? item['confirmed'] : null,
  }
}

function decodeRecords(source: JsonObject, key: string): EditRecord[] {
  return list(source, key).flatMap((entry) => {
    const record = decodeRecord(entry)
    return record === null ? [] : [record]
  })
}

/** An undo or redo: the pick it takes. */
export interface EditStep {
  revisionId: string
  contentHash: string
}

function decodeStep(value: unknown): EditStep | null {
  if (!isObject(value)) return null
  return { revisionId: text(value, 'revision_id'), contentHash: text(value, 'content_hash') }
}

export interface Overlay {
  revisionId: string
  records: EditRecord[]
  /** Records that name no block of the current workout: kept, without effect. */
  unapplied: EditRecord[]
  /** Anchors whose music left the mix: no effect. */
  orphaned: EditRecord[]
  undo: EditStep | null
  redo: EditStep | null
}

export function decodeOverlay(value: unknown): Overlay {
  const item = object(value, 'overlay')
  return {
    revisionId: text(item, 'revision_id'),
    records: decodeRecords(item, 'records'),
    unapplied: decodeRecords(item, 'unapplied'),
    orphaned: decodeRecords(item, 'orphaned'),
    undo: decodeStep(item['undo']),
    redo: decodeStep(item['redo']),
  }
}

export type OverlayResult =
  | { kind: 'ready'; overlay: Overlay }
  /**
   * The draft cannot be edited (a 409): `edit_unavailable` (not a studio draft: a Spotify draft until
   * #1424), `no_proposal`, `music_changed`, `mix_not_applied` (after a studio revise, until the user
   * undoes it or applies the choices, #1423) or another reason the server names.
   */
  | { kind: 'unavailable'; reason: string }
  | { kind: 'none' }

export async function getOverlay(classId: string, signal?: AbortSignal): Promise<OverlayResult> {
  try {
    return { kind: 'ready', overlay: decodeOverlay(await get(`${base(classId)}/overlay`, signal)) }
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) return { kind: 'unavailable', reason: error.reason ?? 'edit_unavailable' }
    if (error instanceof ApiError && error.status === 404) return { kind: 'none' }
    throw error
  }
}

/** What an edit answers: the new current version. The new content is read again from the revision. */
export interface EditResult {
  revisionId: string
  number: number
  contentHash: string
  hints: string[]
}

export async function postEdit(classId: string, baseRevisionId: string, edit: EditOp): Promise<EditResult> {
  const body = object(await post(`${base(classId)}/edits`, { base_revision_id: baseRevisionId, edit }), 'edit')
  return {
    revisionId: text(body, 'revision_id'),
    number: typeof body['number'] === 'number' ? body['number'] : 0,
    contentHash: text(body, 'content_hash'),
    hints: Array.isArray(body['hints']) ? body['hints'].filter((h): h is string => typeof h === 'string') : [],
  }
}

/** An undo or redo: the pick of `step`, valid only while `expectedCurrentId` is still the current version. */
export async function pickStep(classId: string, step: EditStep, expectedCurrentId: string): Promise<void> {
  await post(`${base(classId)}/current`, {
    revision_id: step.revisionId,
    content_hash: step.contentHash,
    expected_current_id: expectedCurrentId,
  })
}

/** The refusal of an edit or pick: a 409 (or a 422 `invalid_input`) with its reason code, or null for anything else. */
export function refusalReason(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null
  return error.status === 409 || error.status === 422 ? (error.reason ?? 'conflict') : null
}

/** A 409 `stale_revision`: the class moved under the page. */
export function isStale(error: unknown): boolean {
  return refusalReason(error) === 'stale_revision'
}
