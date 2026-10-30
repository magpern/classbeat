import { ApiError } from './client'
import { classReasonText } from './classMessages'

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

const SAVE_REASONS: Record<string, string> = {
  write_unresolved:
    'An earlier save or update of this class to MOWL is still undecided. Decide it first with Check again or Do it anyway in the box below, then try again.',
  call_may_be_running:
    'classbeat may still be writing this class to MOWL. Wait a few minutes, then try again.',
  hash_mismatch:
    'That version is not the one this page showed: it was changed elsewhere. The page has been reloaded.',
  class_busy:
    'This class is busy with another task, perhaps from another tab. The page has been reloaded. Wait for it to finish, then try again.',
  not_connected: 'MOWL is not connected. Connect it on your account page, then try again.',
  music_unlinked:
    'The music of this version is not in your connected MOWL account. Change it and save the new version.',
  invalid_input:
    'That could not be saved. The page has been reloaded; check the class and try again.',
  quota: 'You have used up what your plan allows for now.',
  busy: 'You already have tasks running. Wait for one to finish, then try again.',
  idempotency_mismatch: 'That request was already sent with other contents. Reload the page and try again.',
}

/** The text of a refused Save or Update (a 409 or 404 at creation). */
export function saveReasonText(error: unknown): string {
  if (error instanceof ApiError && error.reason !== null && Object.hasOwn(SAVE_REASONS, error.reason)) {
    return SAVE_REASONS[error.reason]!
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

const FAILURES: Record<string, string> = {
  upstream_unavailable: 'MOWL could not be reached. Nothing was written. Try again in a few minutes.',
  rate_limited: 'MOWL asked classbeat to slow down. Nothing was written. Wait a few minutes and try again.',
  lease_lost:
    'The work was interrupted too many times. If classbeat could not tell whether anything reached MOWL, it is listed below for you to decide. Otherwise nothing was written; try again.',
  not_connected: 'MOWL is not connected. Connect it on your account page, then try again.',
  needs_reconnect: 'MOWL no longer accepts the stored sign-in. Connect it again on your account page, then try again.',
  key_unavailable: 'The stored MOWL sign-in could not be read. Connect MOWL again on your account page.',
  decrypt_failed: 'The stored MOWL sign-in could not be read. Connect MOWL again on your account page.',
  invalid_input: 'This version could not be written to MOWL. Reload the page and try again.',
  operation_busy: 'Another task of yours was still using MOWL. Nothing was written. Try again in a moment.',
  music_unlinked:
    'The music of this version is not in your connected MOWL account. Change it and save the new version.',
  program_changed: 'This workout was changed in MOWL after classbeat saved it. Nothing was written.',
  program_missing: 'This workout is no longer in your MOWL account. Nothing was written.',
  internal: 'Something went wrong on our side. Try again later.',
  outbound_blocked: 'Something went wrong on our side. Try again later.',
}

/** The sentence for a failed Save or Update job. */
export function saveFailureText(category: string | null): string {
  return category !== null && Object.hasOwn(FAILURES, category)
    ? FAILURES[category]!
    : 'The job did not finish. Check the box below for anything undecided, then try again.'
}

export type WriteKind = 'save' | 'update'

/** What may or may not have happened, in plain words, per kind of write. */
export const UNCERTAIN_TEXT: Record<WriteKind, string[]> = {
  save: [
    'classbeat asked MOWL to add this class as a new workout and did not get a clear answer.',
    'The workout may be in your MOWL account, whole or only partly, or it may not be there at all. classbeat does not try again on its own, so you do not end up with two copies by accident.',
  ],
  update: [
    'classbeat asked MOWL to update the workout it saved earlier and did not get a clear answer.',
    'Your MOWL copy may be unchanged, updated, or only partly updated. MOWL may also have made a new, empty workout with the same name in My Workouts; if you find one, it is your own private workout and you can delete it in MOWL. classbeat does not try again on its own.',
  ],
}

export const DUPLICATE_WARNING =
  'Do it anyway means you decide the workout was not saved. If it was saved after all, this may add a second workout to your MOWL account; you can delete extras in the MOWL app.'
export const OVERWRITE_WARNING =
  'Do it anyway means you decide the update did not happen. Your MOWL copy may be partly updated; the next update replaces it with this version.'

export type WriteOutcome = 'found' | 'nothing' | 'still_unsure' | 'decided'

export const OUTCOME_TEXT: Record<WriteKind, Record<WriteOutcome, string>> = {
  save: {
    found: 'classbeat found the workout in your MOWL account and recorded it as saved.',
    nothing: 'classbeat checked and the workout was not saved. You can save it again now.',
    still_unsure: 'classbeat still cannot tell whether the workout was saved.',
    decided: 'Understood. You can save it again now; if it was saved after all, you will have two copies in MOWL.',
  },
  update: {
    found: 'classbeat found your MOWL workout updated and recorded it.',
    nothing: 'classbeat checked and your MOWL workout is unchanged. You can update it again now.',
    still_unsure: 'classbeat still cannot tell whether the workout was updated.',
    decided: 'Understood. The next update replaces your MOWL copy with this version.',
  },
}
