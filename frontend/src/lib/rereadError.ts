import { ApiError } from '../api/client'
import { errorMessage } from '../api/messages'
import type { TFunction } from '../i18n/t'
import { REFUSALS } from './handoffRefusal'

/**
 * What the confirm screen says when the re-read was refused (#1538). The refusals the hand-off
 * has (`not_connected`, `feature_off`, `class_busy`, `not_ready` ...) keep their words; the two a
 * re-read adds have their own; anything else is the generic answer text.
 */
export function rereadErrorText(t: TFunction, error: unknown): string {
  if (error instanceof ApiError && error.reason !== null) {
    if (error.reason === 'chat_full') return t('interp.confirm.error.full')
    if (error.reason === 'invalid_answer' || error.reason === 'invalid_input') return t('interp.confirm.error.answer')
    const known = REFUSALS[error.reason]
    if (known !== undefined) return t(known.text)
  }
  return errorMessage(error)
}
