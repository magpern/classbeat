import { ApiError } from '../../api/client'
import { errorMessage } from '../../api/messages'
import type { TFunction } from '../../i18n/t'

/** The text of a refused answer to the music question: `music_fixed` says why; anything else is the generic one. */
export function musicAnswerError(t: TFunction, error: unknown): string {
  if (error instanceof ApiError && error.reason === 'music_fixed') return t('cw.music.error.fixed')
  // No song is picked without a hint (#1538): the same words as the hand-off's refusal.
  if (error instanceof ApiError && error.reason === 'music_unanswered') return t('ws.refused.music_unanswered')
  if (error instanceof ApiError && error.status === 409) return t('cw.music.error.refused')
  return errorMessage(error)
}
