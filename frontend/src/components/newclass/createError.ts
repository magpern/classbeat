import { ApiError } from '../../api/client'
import type { CatalogueKey } from '../../i18n/en'
import type { TFunction } from '../../i18n/t'
import { errorMessage } from '../../api/messages'
import { planRefusalText } from '../../api/planMessages'
import { MAX_BRIEF_BYTES } from '../../lib/brief'

const CREATE_ERRORS: Record<string, CatalogueKey> = {
  not_connected: 'newclass.createError.not_connected',
  class_busy: 'newclass.createError.class_busy',
  idempotency_mismatch: 'newclass.createError.idempotency_mismatch',
  feature_off: 'newclass.createError.feature_off',
  soundless_not_evaluated: 'newclass.createError.soundless_not_evaluated',
  // #509: the designer for the user's plan has not been checked for running classes yet.
  running_not_evaluated: 'newclass.createError.running_not_evaluated',
  invalid_input: 'newclass.createError.invalid_input',
}

/** `noMusic`: the form asked for "No music", so a `feature_off` is the soundless switch, not running's. */
export function createError(t: TFunction, error: unknown, noMusic = false): string {
  const refusal = planRefusalText(error)
  if (refusal !== null) return refusal
  if (noMusic && error instanceof ApiError && error.reason === 'feature_off') return t('newclass.createError.soundlessOff')
  if (error instanceof ApiError && error.reason !== null) {
    const known = Object.hasOwn(CREATE_ERRORS, error.reason) ? CREATE_ERRORS[error.reason] : undefined
    if (known !== undefined) return t(known, { bytes: MAX_BRIEF_BYTES })
  }
  return errorMessage(error)
}
