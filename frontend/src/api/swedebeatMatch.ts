import { ApiError, get, post } from './client'
import { count, flag, list, object, optionalCount, optionalText, strings, text } from './decode'
import { decodeJob, type Job } from './jobs'
import type { CatalogueKey } from '../i18n/en'
import { t } from '../i18n/t'

// The Swedebeat match of a class and the manual "Next, in the Swedebeat app" step (ADR 0016
// sections 5 to 7). The browser only talks to classbeat's own API: it never calls Swedebeat, and
// a page never shows a catalogue id, a link or a token (the API sends none).

export type MatchStatus = 'found' | 'unsure' | 'missing'

const STATUSES: readonly string[] = ['found', 'unsure', 'missing']

export interface MatchSong {
  position: number
  status: MatchStatus
  wantedTitle: string
  wantedArtist: string
  /** Swedebeat's hit; `null` for a missing song. */
  swedebeat: { title: string; artist: string; durationSec: number | null } | null
}

export interface SwedebeatMatch {
  id: string
  number: number
  /** The song list this match is of. */
  parentId: string | null
  /** What Approve would send back with the revision id. */
  contentHash: string
  songs: MatchSong[]
  found: number
  unsure: number
  missing: number
  /** The mix's length by Swedebeat's masters. */
  swedebeatSec: number
}

function decodeMatchSong(value: unknown): MatchSong {
  const item = object(value, 'song')
  const status = text(item, 'status')
  const wanted = object(item.wanted, 'wanted')
  const hit = item.swedebeat === null || item.swedebeat === undefined ? null : object(item.swedebeat, 'swedebeat')
  return {
    position: count(item, 'position'),
    status: STATUSES.includes(status) ? (status as MatchStatus) : 'missing',
    wantedTitle: text(wanted, 'title'),
    wantedArtist: text(wanted, 'artist'),
    swedebeat:
      hit === null
        ? null
        : { title: text(hit, 'title'), artist: text(hit, 'artist'), durationSec: optionalCount(hit, 'duration') },
  }
}

export function decodeMatch(value: unknown): SwedebeatMatch {
  const item = object(value, 'match')
  if (text(item, 'kind') !== 'swedebeat_match') throw new ApiError(500, null, null, undefined)
  return {
    id: text(item, 'id'),
    number: count(item, 'number'),
    parentId: optionalText(item, 'parent_id'),
    contentHash: text(item, 'content_hash'),
    songs: list(item, 'songs').map(decodeMatchSong),
    found: count(item, 'found'),
    unsure: count(item, 'unsure'),
    missing: count(item, 'missing'),
    swedebeatSec: count(item, 'swedebeat_sec'),
  }
}

const base = (classId: string) => `/api/classes/${encodeURIComponent(classId)}`

export async function getSwedebeatMatch(
  classId: string,
  revisionId: string,
  signal?: AbortSignal,
): Promise<SwedebeatMatch> {
  return decodeMatch(await get(`${base(classId)}/revisions/${encodeURIComponent(revisionId)}`, signal))
}

/** Records the selection the mix is built from. Nothing is written to Swedebeat. */
export async function approveSwedebeatMatch(classId: string, revisionId: string): Promise<void> {
  await post(`${base(classId)}/swedebeat-match`, { revision_id: revisionId })
}

/** `swedebeat_match`: match an approved song list against the catalogue (no model, no credit). */
export async function createMatchJob(
  input: { class_id: string; song_list_revision_id: string; swedebeat_connection_id: string },
  idempotencyKey: string,
): Promise<Job> {
  const body = { kind: 'swedebeat_match', input }
  return decodeJob(await post('/api/jobs', body, { 'Idempotency-Key': idempotencyKey }))
}

/** `revise_songs` that names a match: the server picks the missing and unsure slots itself. */
export async function createCatalogueReplaceJob(
  input: {
    class_id: string
    base_revision_id: string
    match_revision_id: string
    mowl_connection_id: string
    spotify_connection_id: string
  },
  idempotencyKey: string,
): Promise<Job> {
  const body = { kind: 'revise_songs', input: { ...input, mode: 'pick' } }
  return decodeJob(await post('/api/jobs', body, { 'Idempotency-Key': idempotencyKey }))
}

// The hand-off (ADR 0016 section 7).

export type HandoffState = 'open' | 'checked' | 'verified'
export type Verdict = 'ok' | 'too_short' | 'too_long' | 'not_found'

export interface Handoff {
  state: HandoffState
  verdict: Verdict | null
  mixId: string | null
  /** Mix ids that are new since the step began; the picker shows them first. */
  candidates: string[]
  missingPositions: number[]
  analysedSec: number | null
  expectedLowSec: number | null
  expectedHighSec: number | null
  expiresAt: string
  checkedAt: string | null
}

const VERDICTS: readonly string[] = ['ok', 'too_short', 'too_long', 'not_found']
const HANDOFF_STATES: readonly string[] = ['open', 'checked', 'verified']

export function decodeHandoff(value: unknown): Handoff {
  const item = object(value, 'handoff')
  const state = text(item, 'state')
  const verdict = optionalText(item, 'verdict')
  return {
    state: HANDOFF_STATES.includes(state) ? (state as HandoffState) : 'open',
    verdict: verdict !== null && VERDICTS.includes(verdict) ? (verdict as Verdict) : null,
    mixId: optionalText(item, 'mix_id'),
    candidates: strings(item, 'candidates'),
    missingPositions: list(item, 'missing_positions').filter((n): n is number => typeof n === 'number'),
    analysedSec: optionalCount(item, 'analysed_sec'),
    expectedLowSec: optionalCount(item, 'expected_low_sec'),
    expectedHighSec: optionalCount(item, 'expected_high_sec'),
    expiresAt: text(item, 'expires_at'),
    checkedAt: optionalText(item, 'checked_at'),
  }
}

/** The live step, or `null` when there is none yet (404). An expired one throws 410. */
export async function getHandoff(classId: string, signal?: AbortSignal): Promise<Handoff | null> {
  try {
    return decodeHandoff(await get(`${base(classId)}/swedebeat-handoff`, signal))
  } catch (error) {
    if (error instanceof ApiError && error.status === 404 && error.reason !== 'feature_off') return null
    throw error
  }
}

/** Starts the step: the server snapshots the user's mixes. The same live row again is a no-op. */
export async function startHandoff(classId: string): Promise<Handoff> {
  return decodeHandoff(await post(`${base(classId)}/swedebeat-handoff`))
}

export interface Check {
  jobId: string | null
  handoff: Handoff | null
}

/** "I made the mix" / "Check again". Verified already: no job. A mix id only from the picker. */
export async function checkHandoff(classId: string, mixId: string | null, key: string): Promise<Check> {
  const body = mixId === null ? {} : { mix_id: mixId }
  const answer = object(await post(`${base(classId)}/swedebeat-handoff/check`, body, { 'Idempotency-Key': key }), 'check')
  return {
    jobId: optionalText(answer, 'job_id'),
    handoff: answer.handoff === null || answer.handoff === undefined ? null : decodeHandoff(answer.handoff),
  }
}

/** "This is the right mix": the verdict never blocks. */
export async function overrideHandoff(classId: string, mixId: string): Promise<Handoff> {
  return decodeHandoff(await post(`${base(classId)}/swedebeat-handoff/override`, { mix_id: mixId }))
}

export interface Designed {
  jobId: string | null
  revisionId: string | null
  designed: boolean
}

/** Designs the class on the verified mix. */
export async function designOnMix(classId: string, key: string): Promise<Designed> {
  const answer = object(await post(`${base(classId)}/swedebeat-handoff/design`, {}, { 'Idempotency-Key': key }), 'design')
  return {
    jobId: optionalText(answer, 'job_id'),
    revisionId: optionalText(answer, 'revision_id'),
    designed: flag(answer, 'designed'),
  }
}

/** "Not now": writes nothing. */
export async function notNow(classId: string): Promise<void> {
  await post(`${base(classId)}/swedebeat-handoff/not-now`)
}

// Plain sentences for the refusals of these routes (catalogue keys swmatch.reason.*, ADR 0028 section 2).
// Never a provider message or a reason code.

const REASONS: Record<string, CatalogueKey> = {
  feature_off: 'swmatch.reason.feature_off',
  not_found: 'swmatch.reason.not_found',
  stale_match: 'swmatch.reason.stale_match',
  handoff_expired: 'swmatch.reason.handoff_expired',
  class_busy: 'swmatch.reason.class_busy',
  not_connected: 'swmatch.reason.not_connected',
  rate_limited: 'swmatch.reason.rate_limited',
  upstream_unavailable: 'swmatch.reason.upstream_unavailable',
  key_unavailable: 'swmatch.reason.upstream_unavailable',
  outbound_blocked: 'swmatch.reason.upstream_unavailable',
  invalid_input: 'swmatch.reason.invalid_input',
  not_checked: 'swmatch.reason.not_checked',
  busy: 'swmatch.reason.busy',
  quota: 'swmatch.reason.quota',
  analysis_limit: 'swmatch.reason.analysis_limit',
  too_large: 'swmatch.reason.invalid_input',
  idempotency_mismatch: 'swmatch.reason.invalid_input',
  decrypt_failed: 'swmatch.reason.decrypt_failed',
  pool_busy: 'swmatch.reason.pool_busy',
  song_picking_off: 'swmatch.reason.song_picking_off',
  consent_missing: 'swmatch.reason.consent_missing',
  revision_limit: 'swmatch.reason.revision_limit',
  chat_full: 'swmatch.reason.chat_full',
  stale_base: 'swmatch.reason.stale_base',
  swedebeat_rounds: 'swmatch.reason.swedebeat_rounds',
  write_unresolved: 'swmatch.reason.write_unresolved',
}

/** A reconnect is needed: the sign-in with MOWL or Swedebeat was refused. */
export function needsReconnect(error: unknown): boolean {
  return error instanceof ApiError && error.reason === 'needs_reconnect'
}

export function swedebeatError(error: unknown): string {
  if (error instanceof ApiError && error.reason !== null && Object.hasOwn(REASONS, error.reason)) {
    return t(REASONS[error.reason]!)
  }
  if (error instanceof ApiError && error.status === 429) return t(REASONS.rate_limited!)
  return t('swmatch.reason.fallback')
}
