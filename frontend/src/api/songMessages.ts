import type { CatalogueKey } from '../i18n/en'
import { t } from '../i18n/t'
import { ApiError } from './client'
import { planRefusalText } from './planMessages'
import { spotifySentence } from './spotifyMessages'

// The words of song picking (ADR 0008 sections 5 and 9, ADR 0009 section 8). A category code, a
// provider message and a model text are never shown; every refusal and failure has a sentence.
// The words are the catalogue's (ADR 0023), looked up when a function is called.

/** Phase 1 stages in the order they run, with the words the Designing page uses. */
const PICK_STAGES: Record<string, CatalogueKey> = {
  pool_search: 'songs.stage.pool_search',
  pool_write: 'songs.stage.pool_write',
  pool_analyse: 'songs.stage.pool_analyse',
  choose: 'songs.stage.choose',
}

/** Phase 2 stages (after the approval) that no other job has. */
const SONG_STAGE_LABELS: Record<string, CatalogueKey> = {
  create: 'songs.stage.create',
  fill: 'songs.stage.fill',
  attach: 'songs.stage.attach',
}

export function pickStageLabel(stage: string | null): string | null {
  if (stage === null) return null
  if (Object.hasOwn(PICK_STAGES, stage)) return t(PICK_STAGES[stage]!)
  return Object.hasOwn(SONG_STAGE_LABELS, stage) ? t(SONG_STAGE_LABELS[stage]!) : null
}

/** What Approve songs answers with when it refuses (ADR 0008 section 5). */
export interface Refusal {
  text: string
  /** A final refusal cannot be retried: the page then offers no Approve button. */
  final: boolean
  /** The fix is on the account page (the Spotify and MOWL cards). */
  account?: boolean
  /** The user can ask for new songs instead. */
  newSongs?: boolean
}

interface RefusalKeys {
  key: CatalogueKey
  final: boolean
  account?: true
  newSongs?: true
}

const APPROVE_REFUSALS: Record<string, RefusalKeys> = {
  expired: { key: 'songs.approve.expired', final: true, newSongs: true },
  superseded: { key: 'songs.approve.superseded', final: true },
  hash_mismatch: { key: 'songs.approve.hash_mismatch', final: true },
  not_connected: { key: 'songs.approve.not_connected', final: false, account: true },
  account_changed: { key: 'songs.approve.account_changed', final: true, newSongs: true },
  not_waiting: { key: 'songs.approve.not_waiting', final: true },
  busy: { key: 'songs.approve.busy', final: false },
  quota: { key: 'songs.approve.quota', final: false },
  not_found: { key: 'songs.approve.not_found', final: true },
  invalid_input: { key: 'songs.approve.invalid_input', final: true },
}

function refusal({ key, ...rest }: RefusalKeys): Refusal {
  return { text: t(key), ...rest }
}

export function approveRefusal(error: unknown): Refusal {
  if (error instanceof ApiError && (error.reason === 'quota' || error.reason === 'busy')) {
    return { text: planRefusalText(error) ?? t('songs.approve.default'), final: false }
  }
  if (error instanceof ApiError && error.reason !== null && Object.hasOwn(APPROVE_REFUSALS, error.reason)) {
    return refusal(APPROVE_REFUSALS[error.reason]!)
  }
  return { text: t('songs.approve.default'), final: false }
}

// What starting a pick, a change or a resume answers with (ADR 0008 section 9, 409 reasons).
const START_ERRORS: Record<string, CatalogueKey> = {
  song_picking_off: 'songs.start.song_picking_off',
  consent_missing: 'songs.start.consent_missing',
  pool_busy: 'songs.start.pool_busy',
  class_busy: 'songs.start.class_busy',
  stale_base: 'songs.start.stale_base',
  revision_limit: 'songs.start.revision_limit',
  chat_full: 'songs.start.chat_full',
  not_song_picked: 'songs.start.not_song_picked',
  invalid_input: 'songs.start.invalid_input',
  not_connected: 'songs.start.not_connected',
  receipt_invalid: 'songs.start.receipt_invalid',
  account_changed: 'songs.start.account_changed',
  write_unresolved: 'songs.start.write_unresolved',
  idempotency_mismatch: 'songs.start.idempotency_mismatch',
}

export function startError(error: unknown): string {
  const refused = planRefusalText(error)
  if (refused !== null) return refused
  if (error instanceof ApiError && error.reason !== null && Object.hasOwn(START_ERRORS, error.reason)) {
    return t(START_ERRORS[error.reason]!)
  }
  return t('songs.start.default')
}

export function isClassBusy(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && error.reason === 'class_busy'
}

export interface SongFailure {
  title: string
  detail: string
  /** The user can fix it on the account page. */
  account?: boolean
}

interface SongFailureKeys {
  title: CatalogueKey
  /** A catalogue key, or a function for the Spotify sentences, which have their own table. */
  detail: CatalogueKey | (() => string)
  account?: true
}

/**
 * The failure sentences of a song job, by category (ADR 0008 section 9, ADR 0009 section 8).
 * Categories that a song job shares with other jobs but words differently are here too.
 */
const SONG_FAILURES: Record<string, SongFailureKeys> = {
  invalid_grant: { title: 'songs.failure.invalid_grant.title', detail: () => spotifySentence('invalid_grant'), account: true },
  app_misconfigured: { title: 'songs.failure.app_misconfigured.title', detail: () => spotifySentence('app_misconfigured'), account: true },
  not_allowlisted: { title: 'songs.failure.not_allowlisted.title', detail: () => spotifySentence('not_allowlisted'), account: true },
  forbidden: { title: 'songs.failure.forbidden.title', detail: () => spotifySentence('forbidden') },
  rate_limited: { title: 'songs.failure.rate_limited.title', detail: 'songs.failure.rate_limited.detail' },
  quota_exhausted: { title: 'songs.failure.quota_exhausted.title', detail: 'songs.failure.quota_exhausted.detail' },
  upstream_unavailable: { title: 'songs.failure.upstream_unavailable.title', detail: 'songs.failure.upstream_unavailable.detail' },
  not_found: { title: 'songs.failure.not_found.title', detail: 'songs.failure.not_found.detail' },
  scope_mismatch: { title: 'songs.failure.scope_mismatch.title', detail: () => spotifySentence('scope_mismatch'), account: true },
  invalid_response: { title: 'songs.failure.scope_mismatch.title', detail: () => spotifySentence('invalid_response'), account: true },
  account_changed: { title: 'songs.failure.account_changed.title', detail: 'sentence.job.account_changed' },
  access_denied: { title: 'songs.failure.access_denied.title', detail: () => spotifySentence('access_denied'), account: true },
  not_connected: { title: 'songs.failure.not_connected.title', detail: 'songs.failure.not_connected.detail', account: true },
  consent_missing: { title: 'songs.failure.consent_missing.title', detail: 'sentence.job.consent_missing', account: true },
  pool_unusable: { title: 'songs.failure.pool_unusable.title', detail: 'sentence.job.pool_unusable' },
  pool_missing: { title: 'songs.failure.pool_missing.title', detail: 'songs.failure.pool_missing.detail' },
  pool_not_synced: { title: 'songs.failure.pool_not_synced.title', detail: 'sentence.job.pool_not_synced' },
  playlist_mismatch: { title: 'songs.failure.playlist_mismatch.title', detail: 'songs.failure.playlist_mismatch.detail' },
  pool_too_small: { title: 'songs.failure.pool_too_small.title', detail: 'sentence.job.pool_too_small' },
}

export const SONG_FAILURE_CATEGORIES: readonly string[] = Object.keys(SONG_FAILURES)

/** The sentence of a song job's failure, or null when the generic table words it. */
export function songFailure(category: string | null): SongFailure | null {
  if (category === null || !Object.hasOwn(SONG_FAILURES, category)) return null
  const row = SONG_FAILURES[category]!
  return {
    title: t(row.title),
    detail: typeof row.detail === 'function' ? row.detail() : t(row.detail),
    ...(row.account ? { account: true } : {}),
  }
}

// The undecided write of the final playlist (ADR 0008 section 2, ADR 0005 section 7).
export const playlistUncertainTitle = (): string => t('songs.uncertain.title')
export const playlistUncertainText = (): readonly string[] => [t('songs.uncertain.text1'), t('songs.uncertain.text2')]
export const playlistOverwriteWarning = (): string => t('songs.uncertain.overwriteWarning')

export type PlaylistOutcome = 'found' | 'nothing' | 'decided' | 'still_unsure'

export const playlistOutcome = (outcome: PlaylistOutcome): string => t(`songs.outcome.${outcome}`)
