import { ApiError } from '../../api/client'
import { errorMessage } from '../../api/messages'
import type { TFunction } from '../../i18n/t'

/** The text of a refused answer to the music question: `music_fixed` says why; anything else is the generic one. */
export function musicAnswerError(t: TFunction, error: unknown): string {
  if (error instanceof ApiError && error.reason === 'music_fixed') return t('cw.music.error.fixed')
  if (error instanceof ApiError && error.status === 409) return t('cw.music.error.refused')
  return errorMessage(error)
}
