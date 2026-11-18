import { ApiError } from './client'
import { classReasonText } from './classMessages'
import { t } from '../i18n/t'

/** The sentence for the closed code a refused pending message was `failed` with (ADR 0038 section 8). */
export function pendingReasonText(reason: string | null): string {
  if (reason === null) return t('class.reason.changedMeanwhile')
  return classReasonText(new ApiError(409, reason, null, { detail: { reason } }))
}

/** The sentence for a refused store of a message (`message_pending`, `pending_full`, ...). */
export function storeReasonText(error: unknown): string {
  if (error instanceof ApiError && error.reason === 'message_pending') return t('cw.chat.messagePending')
  if (error instanceof ApiError && error.reason === 'pending_full') return t('cw.chat.pendingFull')
  return classReasonText(error)
}
