import { ApiError, post } from './client'
import { object, text } from './decode'
import { errorMessage } from './messages'
import { planRefusalText } from './planMessages'
import type { CatalogueKey } from '../i18n/en'
import { t } from '../i18n/t'

// Duplicate a class (ADR 0007 amendment of 2026-11-03, part D; backend classbeat/classes/duplicate.py).
// The browser names a version by id and the hash of its content; the server copies it.

export const TITLE_MAX = 100

export interface Duplicated {
  classId: string
  revisionId: string
}

export async function duplicateClass(
  classId: string,
  revisionId: string,
  contentHash: string,
  title: string,
  /** Made once per dialog: a retried request makes no second class and takes no second credit. */
  idempotencyKey: string,
): Promise<Duplicated> {
  const body = object(
    await post(
      `/api/classes/${encodeURIComponent(classId)}/duplicate`,
      {
        revision_id: revisionId,
        content_hash: contentHash,
        title,
      },
      { 'Idempotency-Key': idempotencyKey },
    ),
    'duplicated',
  )
  return {
    classId: text(body, 'class_id'),
    revisionId: text(body, 'revision_id'),
  }
}

/** The name the dialog proposes: the title with " (copy)" (in the active language), cut to the server's limit. */
export function copyTitle(title: string): string {
  const key = 'class.duplicate.copyOf'
  // The room the wording takes, so the title is cut to what is left whatever the language says.
  const room = TITLE_MAX - t(key, { title: '' }).length
  return t(key, { title: title.slice(0, room).trimEnd() })
}

const REASONS: Record<string, CatalogueKey> = {
  not_ready: 'class.duplicate.reason.not_ready',
  feature_off: 'class.duplicate.reason.feature_off',
  soundless_not_evaluated: 'class.duplicate.reason.soundless_not_evaluated',
  not_changeable: 'class.duplicate.reason.not_changeable',
  hash_mismatch: 'class.duplicate.reason.hash_mismatch',
  game_retired: 'class.reason.game_retired',
  not_found: 'class.duplicate.reason.not_found',
  invalid_input: 'class.duplicate.reason.invalid_input',
  idempotency_mismatch: 'class.duplicate.reason.idempotency_mismatch',
  internal: 'class.duplicate.reason.internal',
}

/** The refusals of a duplicate in plain sentences; the plan's (`quota`) as everywhere else. */
export function duplicateReasonText(error: unknown): string {
  const plan = planRefusalText(error)
  if (plan !== null) return plan
  if (error instanceof ApiError && error.reason !== null && Object.hasOwn(REASONS, error.reason)) {
    return t(REASONS[error.reason]!)
  }
  return errorMessage(error)
}
