import type { ReadingQuestion } from '../api/readingExtras'
import type { TFunction } from '../i18n/t'

// The words of a reading question (#1536). The model's question is shown in its own words, as
// plain text. A closed question (`code`, backend `reading_question_policy.WHICH_MUSIC`) has no
// model words: its text and options come from the catalogue, in the page's language.

const CLOSED_OPTIONS = {
  my_playlists: 'interp.question.whichMusic.myPlaylists',
  no_music: 'interp.question.whichMusic.noMusic',
} as const

/** The question's sentence. */
export function questionText(question: ReadingQuestion, t: TFunction): string {
  return question.code === 'which_music' ? t('interp.question.whichMusic') : question.text
}

/** The label of option `index` ('' for no such option). */
export function optionLabel(question: ReadingQuestion, index: number, t: TFunction): string {
  const option = question.options[index] ?? ''
  if (question.code !== 'which_music') return option
  return Object.hasOwn(CLOSED_OPTIONS, option) ? t(CLOSED_OPTIONS[option as keyof typeof CLOSED_OPTIONS]) : ''
}
