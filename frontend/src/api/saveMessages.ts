import { ApiError } from './client'
import { classReasonText } from './classMessages'
import { quotaText } from './planMessages'
import { t } from '../i18n/t'

// Plain sentences for Save, Update and the undecided write (ADR 0007 sections 5 to 8 and 12).
// Category and reason codes are never shown.

const ACCOUNT_CATEGORIES = [
  'not_connected',
  'needs_reconnect',
  'key_unavailable',
  'decrypt_failed',
]

export function needsAccount(category: string | null): boolean {
  return category !== null && ACCOUNT_CATEGORIES.includes(category)
}

const SAVE_REASON_CODES = [
  'write_unresolved',
  'call_may_be_running',
  'hash_mismatch',
  'class_busy',
  'not_connected',
  'music_unlinked',
  'invalid_input',
  'busy',
  'idempotency_mismatch',
] as const

type SaveReasonCode = (typeof SAVE_REASON_CODES)[number]

function isSaveReason(reason: string): reason is SaveReasonCode {
  return (SAVE_REASON_CODES as readonly string[]).includes(reason)
}

/** The text of a refused Save or Update (a 409 or 404 at creation). */
export function saveReasonText(error: unknown): string {
  // A plan-limit refusal (409 `quota`: classes, cost, length, feature) says which limit it was.
  // The version is already made, so a length or feature refusal cannot ask for a shorter length.
  if (error instanceof ApiError && error.reason === 'quota') return quotaText(error, 'save')
  if (error instanceof ApiError && error.reason !== null && isSaveReason(error.reason)) {
    return t(`class.saveReason.${error.reason}`)
  }
  return classReasonText(error)
}

/** A refusal that means the page is out of date or the undecided list changed: reload both. */
export function refusalChangesPage(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    (error.status === 404 ||
      (error.status === 409 && error.reason !== 'call_may_be_running'))
  )
}

const FAILURE_CODES = [
  'upstream_unavailable',
  'rate_limited',
  'lease_lost',
  'not_connected',
  'needs_reconnect',
  'key_unavailable',
  'invalid_input',
  'operation_busy',
  'music_unlinked',
  'program_changed',
  'program_missing',
  'program_other_sport',
  'internal',
] as const

type FailureCode = (typeof FAILURE_CODES)[number]

// Two categories share the sentence of another: the reader gets the same advice either way.
const FAILURE_ALIASES: Readonly<Record<string, FailureCode>> = {
  decrypt_failed: 'key_unavailable',
  outbound_blocked: 'internal',
}

function failureCode(category: string): FailureCode | null {
  if ((FAILURE_CODES as readonly string[]).includes(category)) return category as FailureCode
  return Object.hasOwn(FAILURE_ALIASES, category) ? FAILURE_ALIASES[category]! : null
}

/** The sentence for a failed Save or Update job. */
export function saveFailureText(category: string | null): string {
  const code = category === null ? null : failureCode(category)
  return code !== null ? t(`class.saveFailure.${code}`) : t('class.saveFailure.default')
}

export type WriteKind = 'save' | 'update'

/** What may or may not have happened, in plain words, per kind of write. */
export function uncertainText(kind: WriteKind): string[] {
  return kind === 'save'
    ? [t('class.uncertain.save.1'), t('class.uncertain.save.2')]
    : [t('class.uncertain.update.1'), t('class.uncertain.update.2')]
}

export function duplicateWarning(): string {
  return t('class.warning.duplicate')
}

export function overwriteWarning(): string {
  return t('class.warning.overwrite')
}

export type WriteOutcome = 'found' | 'nothing' | 'still_unsure' | 'decided'

export function outcomeText(kind: WriteKind, outcome: WriteOutcome): string {
  return t(`class.outcome.${kind}.${outcome}`)
}

/**
 * The sentence for a completed Save (ADR 0007 section 5; SL6, #532): a soundless class says it
 * was saved without music.
 */
export function savedText(soundless: boolean): string {
  return soundless ? t('class.savedSoundless') : t('class.saved')
}

/**
 * The sentence for a completed Update. "Now matches" is claimed only when classbeat read the
 * workout back and it matched (the saved row's `state_hash` is set, ADR 0007 section 8); an
 * Update MOWL took without that read-back matching says so and never claims a match.
 */
export function updatedText(verified: boolean): string {
  return verified ? t('class.updated') : t('class.updatedUnverified')
}
