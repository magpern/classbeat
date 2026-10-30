import { ApiError } from './client'
import { errorMessage } from './messages'
import type { ChangeSummary } from './classes'
import { clock } from '../lib/workout'

// Plain sentences for the class page: the 409 reasons of the class API and of `POST /api/jobs`
// for `revise_draft` (ADR 0007 sections 2, 3 and 12), the change summary in words, and labels.

const REASONS: Record<string, string> = {
  stale_base:
    'This class was changed in another tab or window, so your request was not sent. The page has been reloaded. Read the current version and ask again.',
  hash_mismatch:
    'That version is not the one this page showed: it was changed elsewhere. The page has been reloaded.',
  class_busy:
    'This class is busy with another task, perhaps from another tab. The page has been reloaded. Wait for it to finish, then try again.',
  revision_limit:
    'This class has reached the most changes one class can have. Keep the version you like, or design a new class.',
  chat_full:
    'This conversation is full. Keep the version you like, or design a new class.',
  not_found: 'This class, or that version, no longer exists. The page has been reloaded.',
  not_connected: 'MOWL is not connected. Connect it on your account page, then ask again.',
  invalid_input: 'That request could not be used. Check it and try again.',
  quota: 'You have used up what your plan allows for now.',
  busy: 'You already have designs running. Wait for one to finish, then try again.',
  call_may_be_running:
    'classbeat may still be writing this class to MOWL, and deleting it now would remove the only record of that write. Wait a few minutes, then try to delete it again.',
  music_unlinked:
    'The music of this version is not in your connected MOWL account. Change it and save the new version.',
}

/** A 409 or 404 means what the page shows is out of date: it shows the reason and reloads. */
export function isStale(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 409 || error.status === 404)
}

export function classReasonText(error: unknown): string {
  if (error instanceof ApiError && error.reason !== null && Object.hasOwn(REASONS, error.reason)) {
    return REASONS[error.reason]!
  }
  if (error instanceof ApiError && error.status === 409) {
    return 'This class changed meanwhile. The page has been reloaded.'
  }
  return errorMessage(error)
}

export function originLabel(origin: string): string {
  if (origin === 'design_draft') return 'First design'
  if (origin === 'revise_draft') return 'Changed on request'
  return 'Version'
}

/** The backend's change summary in the page's own words; it is data, not text (ADR 0007 section 3). */
export function describeChange(change: ChangeSummary): string[] {
  const lines: string[] = []
  const { segmentsAdded: sa, segmentsRemoved: sr } = change
  if ((sa ?? 0) > 0 || (sr ?? 0) > 0) {
    lines.push(`Segments: ${String(sa ?? 0)} added, ${String(sr ?? 0)} removed.`)
  }
  const { intervalsAdded: ia, intervalsRemoved: ir, intervalsChanged: ic } = change
  if ((ia ?? 0) > 0 || (ir ?? 0) > 0 || (ic ?? 0) > 0) {
    lines.push(
      `Intervals: ${String(ia ?? 0)} added, ${String(ir ?? 0)} removed, ${String(ic ?? 0)} changed.`,
    )
  }
  if (change.durationBefore !== null && change.durationAfter !== null) {
    lines.push(
      change.durationBefore === change.durationAfter
        ? `Length unchanged at ${clock(change.durationAfter)}.`
        : `Length: ${clock(change.durationBefore)} to ${clock(change.durationAfter)}.`,
    )
  }
  if (change.tssBefore !== null && change.tssAfter !== null) {
    lines.push(
      change.tssBefore === change.tssAfter
        ? `TSS unchanged at ${String(change.tssAfter)}.`
        : `TSS: ${String(change.tssBefore)} to ${String(change.tssAfter)}.`,
    )
  }
  if (change.textChanged) lines.push('Names, description or cues changed.')
  return lines
}
