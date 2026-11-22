import { ApiError, post } from './client'
import { count, isObject, list, object, optionalText, text } from './decode'
import { decodeJob, type Job } from './jobs'

// The DJ mix choices of a studio draft (ADR 0036 amendment B; backend `classes/mix_preview.py`,
// `swedebeat_studio/apply_kind.py`): the read-only preview, the `apply_mix_choices` job and the
// re-plan. Positions are the approved list's, never a catalogue reference. The server re-checks
// every choice; nothing here is a boundary.

const classBase = (classId: string) => `/api/classes/${encodeURIComponent(classId)}`

/** `MixChoices` as the API reads it: sorted sets of approved-list positions. */
export interface MixChoicesBody {
  dj_mix: boolean
  blend: boolean
  trim: boolean
  keep_whole: number[]
  hard_cut_in: number[]
  hard_cut_out: number[]
}

export type TimingClass = 'exact' | 'approximate' | 'unusable'
export type FullOnly = 'timing_approximate' | 'timing_unusable' | 'timing_unknown'

export interface MixPiece {
  startSec: number
  endSec: number
}

/** What classbeat's proposal plays of one approved song, and whether the song may be trimmed at all. */
export interface MixSuggestion {
  position: number
  lengthSec: number
  timingClass: TimingClass | null
  pieces: MixPiece[]
  /** Per piece: its end is on a trusted phrase point (display only). */
  endsOnPhrase: boolean[]
  fullOnly: FullOnly | null
}

export interface MixWindow extends MixPiece {
  position: number
}

export interface MixPreview {
  proposalRevisionId: string
  targetSec: number
  allowed: { lowSec: number; highSec: number }
  suggestions: MixSuggestion[]
  /** Null when the choices are refused. */
  specDigest: string | null
  windows: MixWindow[] | null
  totalSec: number | null
  /** The first reason the choices are refused, or null. */
  refusal: string | null
}

const TIMING: readonly string[] = ['exact', 'approximate', 'unusable']
const FULL: readonly string[] = ['timing_approximate', 'timing_unusable', 'timing_unknown']

function seconds(item: Record<string, unknown>, key: string): number {
  const value = item[key]
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new ApiError(500, null, null, undefined)
  return value
}

function piece(value: unknown): MixPiece {
  const item = object(value, 'piece')
  return { startSec: seconds(item, 'start_sec'), endSec: seconds(item, 'end_sec') }
}

function decodeSuggestion(value: unknown): MixSuggestion {
  const item = object(value, 'suggestion')
  const timing = optionalText(item, 'timing_class')
  const full = optionalText(item, 'full_only')
  return {
    position: count(item, 'position'),
    lengthSec: seconds(item, 'length_sec'),
    timingClass: timing !== null && TIMING.includes(timing) ? (timing as TimingClass) : null,
    pieces: list(item, 'pieces').map(piece),
    endsOnPhrase: list(item, 'ends_on_phrase').map((on) => on === true),
    // An unknown reason still means "never trimmed": shown as the unknown-timing one.
    fullOnly: full === null ? null : FULL.includes(full) ? (full as FullOnly) : 'timing_unknown',
  }
}

export function decodePreview(value: unknown): MixPreview {
  const item = object(value, 'preview')
  const allowed = object(item['allowed'], 'allowed')
  const refusal = item['refusal']
  const windows = item['windows']
  const total = item['total_sec']
  return {
    proposalRevisionId: text(item, 'proposal_revision_id'),
    targetSec: seconds(item, 'target_sec'),
    allowed: { lowSec: seconds(allowed, 'low_sec'), highSec: seconds(allowed, 'high_sec') },
    suggestions: list(item, 'suggestions').map(decodeSuggestion),
    specDigest: optionalText(item, 'spec_digest'),
    windows:
      windows === null || windows === undefined
        ? null
        : list(item, 'windows').map((w) => {
            const row = object(w, 'window')
            return { position: count(row, 'position'), ...piece(row) }
          }),
    totalSec: typeof total === 'number' && Number.isFinite(total) ? total : null,
    refusal: isObject(refusal) ? text(refusal, 'reason') : null,
  }
}

/**
 * The read-only preview: the windows, the total, the allowed range and the first refusal of these
 * choices on the draft's proposal. It stores nothing and calls no model. A 409 (`stale_base`,
 * `no_proposal`, `music_changed`, ...) is thrown as an `ApiError`.
 */
export async function previewMix(
  classId: string,
  input: { revisionId: string; contentHash: string; choices: MixChoicesBody },
): Promise<MixPreview> {
  const body = { revision_id: input.revisionId, content_hash: input.contentHash, choices: input.choices }
  return decodePreview(await post(`${classBase(classId)}/mix-choices/preview`, body))
}

/** `apply_mix_choices`: the choices as a new draft (no model, no credit). */
export async function createApplyJob(
  input: { classId: string; revisionId: string; contentHash: string; choices: MixChoicesBody },
  idempotencyKey: string,
): Promise<Job> {
  const body = {
    kind: 'apply_mix_choices',
    input: {
      class_id: input.classId,
      base_revision_id: input.revisionId,
      base_content_hash: input.contentHash,
      choices: input.choices,
    },
  }
  return decodeJob(await post('/api/jobs', body, { 'Idempotency-Key': idempotencyKey }))
}

/**
 * Re-plan the other songs: a `studio_revise`, a new proposal that is never applied. The choices are
 * not sent yet (#1372 adds them to the job input).
 */
export async function createReplanJob(
  input: { classId: string; revisionId: string; connectionId: string; request: string; choices: MixChoicesBody },
  idempotencyKey: string,
): Promise<Job> {
  const body = {
    kind: 'studio_revise',
    input: {
      class_id: input.classId,
      base_revision_id: input.revisionId,
      request: input.request,
      swedebeat_connection_id: input.connectionId,
      // TODO(#1372): send `choices` once the studio_revise input accepts them; JobInput forbids
      // extra fields, so today the field would be refused as invalid_input.
    },
  }
  return decodeJob(await post('/api/jobs', body, { 'Idempotency-Key': idempotencyKey }))
}

/** The recorded choices of an applied draft, read for pre-filling; anything unreadable is dropped. */
export function recordedChoices(value: Record<string, unknown> | null): MixChoicesBody | null {
  if (value === null) return null
  const positions = (key: string): number[] | null => {
    const raw = value[key]
    if (raw === undefined) return []
    return Array.isArray(raw) && raw.every((n) => Number.isInteger(n) && (n as number) >= 0) ? (raw as number[]) : null
  }
  const keep = positions('keep_whole')
  const cutIn = positions('hard_cut_in')
  const cutOut = positions('hard_cut_out')
  if (keep === null || cutIn === null || cutOut === null) return null
  if (typeof value['dj_mix'] !== 'boolean' || typeof value['blend'] !== 'boolean' || typeof value['trim'] !== 'boolean') {
    return null
  }
  return { dj_mix: value['dj_mix'], blend: value['blend'], trim: value['trim'], keep_whole: keep, hard_cut_in: cutIn, hard_cut_out: cutOut }
}
