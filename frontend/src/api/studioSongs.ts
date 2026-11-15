import { ApiError, get, post } from './client'
import { count, flag, isObject, list, object, optionalCount, optionalText, text } from './decode'
import { decodeJob, type Job } from './jobs'
import { swedebeatError } from './swedebeatMatch'
import type { CatalogueKey } from '../i18n/en'
import { t } from '../i18n/t'

// The studio's song list and the user's actions on it (ADR 0036 section 3.3; backend
// `classes/studio_songs_routes.py`). The browser only talks to classbeat's own API. The API sends
// no Spotify id and no catalogue reference, so nothing here can show one.

export const STUDIO_KIND = 'studio_songs'

export type SwapReason = 'missing' | 'unsure' | 'asked'
export type EvidenceState = 'ok' | 'suspected_unavailable'
export type StudioAction = 'undo' | 'another' | 'swap-anyway'

export interface Named {
  title: string
  artist: string
}

export interface StudioSwap {
  original: Named
  replacement: Named
  reason: SwapReason
  undone: boolean
}

export interface StudioSong {
  position: number
  title: string
  artist: string
  role: string
  cover: string | null
  /** A gap has no SwedeBeat facts and can never go into a mix. */
  gap: boolean
  swedebeat: { title: string; artist: string; durationSec: number; bpm: number } | null
  swap: StudioSwap | null
  canUndo: boolean
  canAskAnother: boolean
}

export interface StudioEvidence {
  state: EvidenceState
  sent: number
  missing: number
  anyway: boolean
}

export interface StudioChanges {
  count: number
  limit: number
}

export interface StudioList {
  revisionId: string
  contentHash: string
  targetSec: number
  totalSec: number
  evidence: StudioEvidence
  approvable: boolean
  note: string
  songs: StudioSong[]
  /**
   * The class's song changes against its limit, when the API sends them. TODO(backend): the view
   * does not carry this yet, so until it does the page learns it only from a `revision_limit`
   * refusal (`refusalChanges`).
   */
  changes: StudioChanges | null
}

const REASONS: readonly string[] = ['missing', 'unsure', 'asked']

function named(value: unknown, what: string): Named {
  const item = object(value, what)
  return { title: text(item, 'title'), artist: text(item, 'artist') }
}

function decodeSwap(value: unknown): StudioSwap {
  const item = object(value, 'swap')
  const reason = text(item, 'reason')
  return {
    original: named(item.original, 'original'),
    replacement: named(item.replacement, 'replacement'),
    reason: REASONS.includes(reason) ? (reason as SwapReason) : 'missing',
    undone: flag(item, 'undone'),
  }
}

function decodeSong(value: unknown): StudioSong {
  const item = object(value, 'song')
  const facts = item.swedebeat === null || item.swedebeat === undefined ? null : object(item.swedebeat, 'swedebeat')
  return {
    position: count(item, 'position'),
    title: text(item, 'title'),
    artist: text(item, 'artist'),
    role: text(item, 'role'),
    cover: optionalText(item, 'cover'),
    gap: flag(item, 'gap'),
    swedebeat:
      facts === null
        ? null
        : {
            title: text(facts, 'title'),
            artist: text(facts, 'artist'),
            durationSec: count(facts, 'duration_sec'),
            bpm: Number(facts.bpm),
          },
    swap: item.swap === null || item.swap === undefined ? null : decodeSwap(item.swap),
    canUndo: flag(item, 'can_undo'),
    canAskAnother: flag(item, 'can_ask_another'),
  }
}

function decodeChanges(value: unknown): StudioChanges | null {
  if (!isObject(value)) return null
  const used = optionalCount(value, 'count')
  const limit = optionalCount(value, 'limit')
  return used === null || limit === null ? null : { count: used, limit }
}

export function decodeStudioList(value: unknown): StudioList {
  const item = object(value, 'studio list')
  const evidence = object(item.evidence, 'evidence')
  const state = text(evidence, 'state')
  return {
    revisionId: text(item, 'revision_id'),
    contentHash: text(item, 'content_hash'),
    targetSec: count(item, 'target_sec'),
    totalSec: count(item, 'total_sec'),
    evidence: {
      state: state === 'suspected_unavailable' ? 'suspected_unavailable' : 'ok',
      sent: count(evidence, 'sent'),
      missing: count(evidence, 'missing'),
      anyway: flag(evidence, 'anyway'),
    },
    approvable: flag(item, 'approvable'),
    note: optionalText(item, 'note') ?? '',
    songs: list(item, 'songs').map(decodeSong),
    changes: decodeChanges(item.changes),
  }
}

const base = (classId: string, revisionId: string) =>
  `/api/classes/${encodeURIComponent(classId)}/studio-songs/${encodeURIComponent(revisionId)}`

export async function getStudioList(classId: string, revisionId: string, signal?: AbortSignal): Promise<StudioList> {
  return decodeStudioList(await get(base(classId, revisionId), signal))
}

/**
 * Undo a swap, ask for another song, or Swap anyway: each answers with the new `studio_songs` job
 * (201, or 200 for a repeated key), and each needs its own `Idempotency-Key`. The waiting list is
 * cancelled by the server in the same step; a refusal leaves it as it was.
 */
export async function actOnStudioList(
  classId: string,
  revisionId: string,
  action: StudioAction,
  position: number | null,
  idempotencyKey: string,
): Promise<Job> {
  const body = position === null ? {} : { position }
  return decodeJob(await post(`${base(classId, revisionId)}/${action}`, body, { 'Idempotency-Key': idempotencyKey }))
}

/** `{limit, count}` of a `revision_limit` refusal (409), the class's song changes against its limit. */
export function refusalChanges(error: unknown): StudioChanges | null {
  if (!(error instanceof ApiError) || error.reason !== 'revision_limit') return null
  const detail = isObject(error.body) ? error.body.detail : null
  return decodeChanges(detail)
}

// Plain sentences for the refusals of these routes (catalogue keys studio.reason.*, ADR 0028 section 2).
// Never a reason code. A refusal changes nothing: the waiting list is left as it was.

const OWN: Record<string, CatalogueKey> = {
  quota: 'studio.reason.quota',
  list_has_gap: 'studio.reason.list_has_gap',
  invalid_input: 'studio.reason.invalid_input',
  not_found: 'studio.reason.not_found',
  feature_off: 'studio.reason.not_found',
  idempotency_mismatch: 'studio.reason.idempotency',
  idempotency_key_missing: 'studio.reason.idempotency',
  idempotency_key_invalid: 'studio.reason.idempotency',
}

export function studioError(error: unknown): string {
  if (error instanceof ApiError && error.reason === 'revision_limit') {
    const changes = refusalChanges(error)
    return changes === null
      ? t('studio.reason.revision_limit')
      : t('studio.reason.revision_limit_counts', { count: changes.count, limit: changes.limit })
  }
  if (error instanceof ApiError && error.reason !== null && Object.hasOwn(OWN, error.reason)) {
    return t(OWN[error.reason]!)
  }
  return swedebeatError(error)
}
