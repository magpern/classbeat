import { ApiError } from './client'
import { classReasonText } from './classMessages'
import { failureText } from './jobMessages'
import { t } from '../i18n/t'
import type { CatalogueKey } from '../i18n/en'
import { PLAYER_REMOVE_OPERATION } from './playerSend'

// Plain sentences for Send to player and the player's undecided operations (ADR 0034 sections 5
// and 6). Reason and category codes are never shown.

/** The refusals of `POST /api/jobs` for a send, each with its own sentence. */
const REASONS: Readonly<Record<string, CatalogueKey>> = {
  not_connected: 'player.reason.not_connected',
  unresolved_write: 'player.reason.unresolved_write',
  no_songs: 'player.reason.no_songs',
}

/** What the player's format cannot express (`player_format_policy` refusal codes): one sentence. */
const FORMAT_CODES: readonly string[] = [
  'zone_out_of_range',
  'interval_length',
  'cue_too_long',
  'cadence_out_of_range',
  'no_intervals',
  'no_title',
  'segment_name',
  'description_too_long',
  'song_text',
  'song_window',
  'song_id',
  'songs_on_soundless',
  'crossfade',
  'song_gap',
  'songs_length',
]

export function playerReasonText(error: unknown): string {
  if (error instanceof ApiError && error.reason !== null) {
    if (Object.hasOwn(REASONS, error.reason)) return t(REASONS[error.reason]!)
    if (FORMAT_CODES.includes(error.reason)) return t('player.reason.format')
  }
  return classReasonText(error)
}

/** A refusal that means the page is out of date, or the undecided list changed: read both again. */
export function playerRefusalChangesPage(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    (error.status === 404 || (error.status === 409 && error.reason !== 'call_may_be_running'))
  )
}

/** The connection is gone or needs a reconnect: the way out is the account page. */
export function playerNeedsReconnect(category: string | null): boolean {
  return category === 'not_connected' || category === 'needs_reconnect'
}

/** The sentence of a failed send: title and detail of the category (never the code). */
export function playerFailureText(category: string | null): string {
  const found = failureText(category)
  return `${found.title}. ${found.detail}`
}

export type PlayerOutcome = 'found' | 'nothing' | 'decided' | 'still_unsure'

/** What Check again or Do it anyway found, for a send or a removal. */
export function playerOutcomeText(operationKind: string, outcome: PlayerOutcome): string {
  if (outcome === 'still_unsure') return t('player.write.stillUnsure')
  const which = operationKind === PLAYER_REMOVE_OPERATION ? 'remove' : 'send'
  return t(`player.write.outcome.${which}.${outcome}`)
}
